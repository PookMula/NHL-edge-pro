"""Build every JSON file the static site reads (docs/data/*.json) from stored data + today's slate + odds."""
import json
import math
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from . import market_eval as ME
from .backtest import tier_from_backtest
from .config import ROOT, iso, parse_iso, write_json
from .edges import RANKING_FORMULA, evaluate_game
from .engine import History, pkey, predict, slate_features
from .goalies import ordinal
from .parlays import search as parlay_search
from .stats import goalies_json, teams_json

UNAVAILABLE = [
    "Injury reports — no licensed injury feed connected; injuries are NOT reflected in any probability.",
    "Line combinations / projected lineups — no licensed lineup feed connected.",
    "Starting-goalie confirmations — no confirmation feed; goalies are PROJECTED unless you add a sourced entry to config/goalie_overrides.json.",
    "Expected goals (xG), GSAx, Corsi/Fenwick, danger-zone chances, zone time — require MoneyPuck / Natural Stat Trick / Evolving-Hockey under their terms.",
    "First-period markets, team totals odds, alternate lines and player props odds — need the per-event odds endpoint (extra API cost); not fetched.",
    "Same-game parlay prices — books quote SGPs with their own correlation pricing; not available from the odds feed.",
    "True opening lines and historical closing lines before this pipeline started — only prices this pipeline captured are shown.",
    "Travel / time-zone effects — not yet modeled.",
]


def label_for(e, g):
    if e["market"] == "h2h":
        return f'{g[e["side"]]} ML'
    if e["market"] == "spreads":
        p = e["point"]
        return f'{g[e["side"]]} {"+" if p > 0 else ""}{p:g}'
    return f'{"Over" if e["side"] == "over" else "Under"} {e["point"]:g}'


def _f(x, n=3):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), n)


def build_site(store, slate_rows, odds_rows, statuses, settings, out_dir, now, today, mode="live",
               overrides=None, log=print):
    out_dir = Path(out_dir)
    ms = dict(settings["model"])
    bt_path = store.path("backtest_results.json")
    backtest = json.loads(bt_path.read_text()) if bt_path.exists() else {"available": False,
                                                                           "reason": "Backtest has not been run yet."}
    if backtest.get("available"):
        ms.update(backtest["hyper"])
    games = store.games()
    gg = store.goalie_games()
    today_ord = today.toordinal()
    slate_teams = {r["home"] for r in slate_rows} | {r["away"] for r in slate_rows}
    hist = History(games, gg, ms, extra_teams=slate_teams)
    model = hist.fit(today_ord, ms)
    fitted_at = iso(now)
    log(f"model fit on games through {games.date.max()} | aux {model.aux}")

    # --- validation tiers from existing logs (before adding today's) ---
    bt_t = tier_from_backtest(backtest)
    mev = ME.evaluate(store.dir, games, bt_t, settings)
    tiers = mev["tiers"]

    # --- features + predictions ---
    feats = slate_features(hist, slate_rows, today_ord, overrides)
    odds_by_game = {}
    for r in odds_rows:
        for f in feats:
            g = f["game"]
            if r["home"] == g["home"] and r["away"] == g["away"] and \
                    abs((parse_iso(r["commence_utc"]) - parse_iso(g["start_utc"])).total_seconds()) < 12 * 3600:
                odds_by_game.setdefault(g["game_id"], []).append(r)
    n_draws, seed = ms.get("posterior_draws", 200), settings["simulation"]["seed"]
    preds = predict(hist, model, feats, n_draws, seed)
    swaps = {s: predict(hist, model, feats, 1, seed, swap=s) for s in ("home", "away")}
    point_preds = predict(hist, model, feats, 1, seed)

    goalies = goalies_json(gg, hist.tracker, today_ord, int(slate_rows[0]["season"]) if slate_rows else
                           (int(games.season.max()) if len(games) else 0))
    gstats = {x["id"]: x for x in goalies["goalies"]}
    name_logo = {}
    for r in slate_rows:
        name_logo[r["home"]] = {"name": r["home_name"], "logo": r.get("home_logo", "")}
        name_logo[r["away"]] = {"name": r["away_name"], "logo": r.get("away_logo", "")}
    prev_names = store.path("team_names.json")
    if prev_names.exists():
        old = json.loads(prev_names.read_text())
        old.update(name_logo)
        name_logo = old
    prev_names.write_text(json.dumps(name_logo))
    teams = teams_json(games, store.skaters(int(games.season.max())) if len(games) else None,
                       model.team_ratings(), name_logo, int(games.season.max()) if len(games) else 0)
    tstats = {t["abbr"]: t for t in teams["teams"]}
    beta = model.export()

    all_evals, slate_out, game_meta, preds_by_game = [], [], {}, {}
    for f, P, PP, SH, SA in zip(feats, preds, point_preds, swaps["home"], swaps["away"]):
        g = f["game"]
        gid = g["game_id"]
        title = f'{g["away"]} @ {g["home"]}'
        game_meta[gid] = {"title": title}
        preds_by_game[gid] = P
        orows = odds_by_game.get(gid, [])
        latest = max((r["fetched_at"] for r in orows), default=None)
        orows = [r for r in orows if r["fetched_at"] == latest]
        extra = {(r["market"], r["side"], r["point"]) for r in orows}
        summ = P.summary(extra)
        # market no-vig consensus into summary
        from .edges import novig_by_book
        from statistics import median
        for k, v in novig_by_book(orows).items():
            if pkey(*k) in summ["markets"]:
                summ["markets"][pkey(*k)]["novig"] = float(median(v))
        # goalie swap impact
        swap_list = []
        for side, SP in (("home", SH), ("away", SA)):
            alt = f[side]["alt"]
            if not alt:
                continue
            swap_list.append({"side": side, "team": g[side], "alt_name": alt["name"],
                              "d_p_home_ml": SP.point.p_ml_home - PP.point.p_ml_home,
                              "d_exp_total": SP.point.expected_total() - PP.point.expected_total(), "_pred": SP})
        evals = []
        if orows:
            # EV under goalie swap = worst case over the two possible single swaps
            ev_list = evaluate_game(f, P, orows, now, settings, tiers)
            for side_swap in swap_list:
                sw = evaluate_game(f, side_swap["_pred"], orows, now, settings, tiers)
                for e, s in zip(ev_list, sw):
                    e["ev_goalie_swap"] = s["ev"] if e["ev_goalie_swap"] is None else min(e["ev_goalie_swap"], s["ev"])
                side_swap["flips"] = sum(1 for e, s in zip(ev_list, sw) if e["candidate"] and s["ev"] < 0)
            evals = ev_list
        insights = game_insights(f, summ, beta, tstats, goalies.get("league_sv"))
        for e in evals:
            e["game"] = title
            e["label"] = label_for(e, g)
            e["id"] = f'{gid}-{e["market"]}-{e["side"]}-{e["point"]}-{e["book_key"]}'.replace(" ", "")
            e["goalie_detail"] = f'{g["away"]}: {f["away"]["status"]}, {g["home"]}: {f["home"]["status"]}'
            e["reasons"], e["risks"] = edge_reasons(e, f, summ, insights)
        all_evals.extend(evals)
        side_out = {}
        for side in ("home", "away"):
            gi = f[side]["goalie"]
            gs = gstats.get(gi["id"]) if gi else None
            side_out[side] = {"abbr": g[side], "name": g[f"{side}_name"], "logo": g.get(f"{side}_logo", ""),
                              "b2b": bool(f[side]["b2b"]), "rest_days": f[side]["rest_days"],
                              "goalie": {"id": gi["id"] if gi else None, "name": gi["name"] if gi else None,
                                         "status": f[side]["status"], "source": f[side]["source"],
                                         "sv_rating": gi["sv_rating"] if gi else None,
                                         "season_sv": gs["season"]["sv_pct"] if gs else None,
                                         "season_gsaa": gs["season"]["gsaa"] if gs else None,
                                         "rest_days": gs["rest_days"] if gs else None}}
        cand = [e for e in evals if e["candidate"]]
        books = sorted({r["book_title"] for r in orows})
        main_total = None
        tots = [r["point"] for r in orows if r["market"] == "totals"]
        if tots:
            main_total = max(set(tots), key=tots.count)
        slate_out.append({
            "game_id": gid, "start_utc": g["start_utc"], "state": g.get("game_state"), "venue": g.get("venue"),
            "home": side_out["home"], "away": side_out["away"], "model": summ,
            "goalie_swap": [{k: v for k, v in s.items() if k != "_pred"} for s in swap_list],
            "odds": {"rows": [{"book": r["book_title"], "market": r["market"], "side": r["side"], "point": r["point"],
                               "price": r["price"], "last_update": r.get("last_update")} for r in orows],
                     "books": books, "fetched_at": latest, "main_total": main_total,
                     "status_note": None if orows else statuses.get("odds_note", "No odds for this game.")},
            "edge_ids": [e["id"] for e in cand],
            "edge_rows": [{k: e[k] for k in ("id", "market", "side", "point", "book", "ev_p10")} for e in cand],
            "insights": insights,
            "data_quality": [
                {"item": "Results through", "value": str(games.date.max())},
                {"item": "Training games (weight>1%)", "value": str(int((0.5 ** ((today_ord - hist.dord[0::2]) / model.hl) > 0.01).sum()))},
                {"item": "Goalies", "value": f'{g["away"]} {f["away"]["status"]} / {g["home"]} {f["home"]["status"]}'},
                {"item": "Books priced", "value": str(len(books))},
                {"item": "Odds fetched", "value": latest or "—"},
                {"item": "Injuries / lineups", "value": "not in data feed"},
                {"item": "Posterior draws", "value": str(n_draws)}]})

    gate = settings["edges"]["gate_tier"]
    cands = sorted([e for e in all_evals if e["candidate"]], key=lambda e: -e["ev_p10"])
    qual = [e for e in cands if e["qualified"]]
    clean = lambda e: {k: v for k, v in e.items() if k not in ("decimal",)}
    no_reason = None
    if not qual:
        if not odds_rows:
            no_reason = statuses.get("odds_note", "No sportsbook odds available, so no expected value can be computed.")
        elif cands:
            no_reason = (f"{len(cands)} price(s) look favourable to the model, but no market has reached the {gate} "
                         f"validation tier yet (current: " + ", ".join(f'{k} {v["tier"]}' for k, v in tiers.items()) + ").")
        else:
            no_reason = "No price beats the model's pessimistic (10th-percentile) estimate by the required margin."
    edges_json = {"gate_tier": gate, "thresholds": {"min_ev": settings["edges"]["min_ev"],
                                                     "min_ev_lower": settings["edges"]["min_ev_lower"]},
                  "ranking_formula": RANKING_FORMULA, "no_edge_reason": no_reason,
                  "qualified": [clean(e) for e in qual], "candidates": [clean(e) for e in cands if not e["qualified"]]}

    # --- parlays ---
    gmap = {f["game"]["game_id"]: f["game"] for f in feats}
    pq, pc, nsearch = parlay_search(all_evals, preds_by_game, game_meta, settings, tiers, gate,
                                    lambda l: label_for(l, gmap[l["game_id"]]), iso(now))
    ptrack = ME.parlay_track(store.dir, games)
    parlays_json = {"settings": settings["parlays"], "gate_tier": gate, "n_combos_searched": nsearch,
                    "qualified": pq, "candidates": pc, "track": ptrack,
                    "sgp_note": "Same-game parlays require the sportsbook's own SGP quote (books apply correlation "
                                "adjustments). The connected odds feed does not provide SGP quotes, so none are priced. "
                                "Multiplying single-game prices would misstate the payout.",
                    "no_parlay_reason": None if (pq or pc) else (
                        "No odds available." if not odds_rows else
                        "Not enough individually positive-EV legs at a single book to reach the target payout.")}

    # --- logs (forward validation) ---
    nowiso = iso(now)
    ME.upsert_predictions(store.path("predictions_log.csv"), [{
        "game_id": f["game"]["game_id"], "locked_at": nowiso, "start_utc": f["game"]["start_utc"],
        "date": today.isoformat(), "home": f["game"]["home"], "away": f["game"]["away"],
        "p_home_ml": float(np.mean([d.p_ml_home for d in P.dists])),
        "exp_total": float(np.mean([d.expected_total() for d in P.dists])),
        "home_goalie": (f["home"]["goalie"] or {}).get("id"), "away_goalie": (f["away"]["goalie"] or {}).get("id"),
        "goalie_status": f'{f["home"]["status"]}/{f["away"]["status"]}'}
        for f, P in zip(feats, preds) if f["game"].get("game_state") in ("FUT", "PRE")])
    ME.append_unique(store.path("edge_log.csv"), [{
        "key": e["id"], "game_id": e["game_id"], "date": today.isoformat(), "flagged_at": nowiso,
        "start_utc": gmap[e["game_id"]]["start_utc"], "market": e["market"], "side": e["side"], "point": e["point"],
        "book": e["book"], "book_key": e["book_key"], "price": e["price"], "p_win": e["p_win"], "p_push": e["p_push"],
        "ev": e["ev"], "ev_p10": e["ev_p10"], "tier": e["tier"], "qualified": e["qualified"]} for e in cands],
        ME.EDGE_COLS, "key")
    ME.append_unique(store.path("parlay_log.csv"), [{
        "id": p["id"], "date": today.isoformat(), "flagged_at": nowiso, "book": p["book"], "n_legs": p["n_legs"],
        "american": p["american"], "decimal": p["decimal"], "p_joint": p["p_joint"], "ev": p["ev"], "ev_p10": p["ev_p10"],
        "bucket": p["bucket"], "qualified": p["qualified"],
        "legs": json.dumps([{k: l[k] for k in ("game_id", "market", "side", "point", "price")} for l in p["legs"]])}
        for p in pq + pc], ME.PARLAY_COLS, "id")

    # --- odds history for today's games ---
    oh = {}
    arch_p = store.path(f"odds/{today.isoformat()}.csv")
    if arch_p.exists():
        a = pd.read_csv(arch_p)
        a["point"] = pd.to_numeric(a["point"], errors="coerce")
        for f in feats:
            g = f["game"]
            d = a[(a.home == g["home"]) & (a.away == g["away"])].sort_values("fetched_at")
            per = {}
            for r in d.itertuples(index=False):
                pt = None if pd.isna(r.point) else float(r.point)
                k = pkey(r.market, r.side, pt)
                e = per.setdefault(k, {"first": {"price": r.price, "point": pt, "at": r.fetched_at, "book": r.book_title}, "series": {}})
                e["series"].setdefault(r.book_title, []).append([r.fetched_at, r.price])
            oh[str(g["game_id"])] = per

    skater_rows = 0
    for p in store.dir.glob("skaters_*.csv.gz"):
        skater_rows += sum(1 for _ in pd.read_csv(p, usecols=["game_id"]).itertuples())
    meta = {
        "mode": mode, "generated_at": nowiso, "slate_date": today.isoformat(),
        "data_through": str(games.date.max()) if len(games) else None, "n_games_history": int(len(games)),
        "skater_rows": skater_rows, "sources": statuses.get("sources", []), "unavailable": UNAVAILABLE,
        "model": {"status": "EXPERIMENTAL" if not backtest.get("available") else
                  ("BACKTESTED" if any(bt_t.values()) else "BACKTESTED (no edge over baseline)"),
                  "fitted_at": fitted_at, "hyper": {"half_life_days": model.hl, "ridge": model.ridge},
                  "aux": model.aux, "draws": n_draws, "tiers": tiers}}
    slate_json = {"date": today.isoformat(), "source": "NHL API (api-web.nhle.com)", "fetched_at": statuses.get("nhl_fetched_at"),
                  "odds_source": statuses.get("odds_source"), "odds_fetched_at": statuses.get("odds_fetched_at"),
                  "games": slate_out}
    mev["tiers"] = tiers
    beta["seed"] = seed
    for name, obj in (("meta", meta), ("slate", slate_json), ("edges", edges_json), ("parlays", parlays_json),
                      ("teams", teams), ("goalies", goalies), ("backtest", backtest), ("market_eval", mev),
                      ("odds_history", {"games": oh}), ("model_params", beta)):
        write_json(out_dir / f"{name}.json", obj)
    log(f"site data written to {out_dir} | {len(slate_out)} games, {len(cands)} candidates, {len(qual)} qualified, "
        f"{len(pq)}+{len(pc)} parlays from {nsearch} combos")
    return meta


def game_insights(f, s, beta, tstats, league_sv):
    g = f["game"]
    h, a = g["home"], g["away"]
    out = []
    ti = {t: i for i, t in enumerate(beta["teams"])}
    for t in (a, h):
        if t in ti:
            i = ti[t]
            out.append(f"{t} ratings: attack {beta['att'][i]:+.3f}, defence {beta['def'][i]:+.3f} "
                       f"(log scale vs league avg; + is better) — about {100 * (math.exp(beta['att'][i]) - 1):+.0f}% goals for, "
                       f"{100 * (math.exp(-beta['def'][i]) - 1):+.0f}% goals against vs average.")
    if not g.get("neutral"):
        out.append(f"Home ice in the fitted model: {100 * (math.exp(beta['home']) - 1):+.1f}% expected goals for {h}.")
    for side in ("away", "home"):
        t = g[side]
        if f[side]["b2b"]:
            out.append(f"{t} played yesterday (back-to-back): model effect {100 * (math.exp(beta['b2b_off']) - 1):+.1f}% own scoring, "
                       f"{100 * (math.exp(beta['b2b_def']) - 1):+.1f}% for the opponent.")
        gi = f[side]["goalie"]
        if gi and league_sv:
            eff = math.exp(beta["goalie_coef"] * gi["adj"]) - 1
            out.append(f"{t} goalie {gi['name']} ({f[side]['status'].lower()}): rating SV% {gi['sv_rating']:.3f} vs league "
                       f"{league_sv:.3f} → {100 * eff:+.1f}% expected goals against.")
        elif not gi:
            out.append(f"{t} starting goalie unknown — modeled as league average; treat this game's numbers with extra caution.")
        ts = tstats.get(t, {}).get("windows", {}).get("L10", {})
        if ts.get("gp"):
            out.append(f"{t} last {ts['gp']}: {ts['gf']:.2f} GF / {ts['ga']:.2f} GA per game, shot share {100 * ts['sf_pct']:.1f}% "
                       f"(raw, not opponent-adjusted).")
    nv = s["markets"].get(pkey("h2h", "home", None), {}).get("novig")
    if nv is not None:
        out.append(f"Model {h} win {100 * s['p_home_ml']:.1f}% vs no-vig market {100 * nv:.1f}% "
                   f"({100 * (s['p_home_ml'] - nv):+.1f} pp).")
    if s.get("shots_home"):
        out.append(f"Projected shots on goal: {a} {s['shots_away']:.1f}, {h} {s['shots_home']:.1f}.")
    return out


def edge_reasons(e, f, s, insights):
    g = f["game"]
    reasons = [f"Model probability {100 * e['p_win']:.1f}% (80% band {100 * e['p_win_lo']:.1f}–{100 * e['p_win_hi']:.1f}%) vs "
               f"break-even {100 * e['break_even']:.1f}% at {e['price']:+.0f} → edge {100 * e['prob_edge']:+.1f} pp, EV {100 * e['ev']:+.1f}%."]
    if e["market"] == "totals":
        reasons.append(f"Projected total {s['exp_total']:.2f} (80% band {s['interval']['exp_total'][0]:.2f}–{s['interval']['exp_total'][1]:.2f}); "
                       f"team expected goals {g['away']} {s['exp_away']:.2f}, {g['home']} {s['exp_home']:.2f}.")
    else:
        reasons.append(f"Projected score {g['away']} {s['exp_away']:.2f} – {g['home']} {s['exp_home']:.2f}; regulation 3-way "
                       f"{100 * s['p_reg_away']:.0f}/{100 * s['p_reg_tie']:.0f}/{100 * s['p_reg_home']:.0f}%, OT/SO {100 * (s['p_ot'] + s['p_so']):.0f}%.")
    reasons += [i for i in insights if any(k in i for k in ("ratings", "goalie", "back-to-back", "Home ice"))]
    risks = []
    if e["p_market_novig"] is not None:
        risks.append(f"The market prices this at {100 * e['p_market_novig']:.1f}% (no-vig) vs the model's {100 * e['p_win']:.1f}%. "
                     f"Closing markets are hard to beat; a {100 * abs(e['p_win'] - e['p_market_novig']):.1f} pp disagreement more often "
                     f"means the model is missing information (injuries, lineups) than that the market is wrong.")
    risks.append(f"Pessimistic (10th percentile) EV is {100 * e['ev_p10']:+.1f}% — parameter uncertainty alone could erase most of the edge.")
    if e.get("ev_goalie_swap") is not None:
        risks.append(f"If the other goalie starts, EV becomes {100 * e['ev_goalie_swap']:+.1f}%.")
    if e["goalie_status"] != "CONFIRMED":
        risks.append("Starting goalies are projected, not confirmed.")
    if e["p_push"] > 0.001:
        risks.append(f"Push probability {100 * e['p_push']:.1f}% (stake returned) is included in the EV.")
    risks.append("Injuries and line changes are not in the data feed and are not reflected.")
    if e["market"] == "h2h":
        risks.append(f"Overtime/shootout ({100 * (s['p_ot'] + s['p_so']):.0f}%) is close to a coin flip for either side.")
    return reasons, risks
