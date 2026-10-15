"""Command-line entry point.

  python -m pipeline.run backfill     # historical box scores (resumable; ~1h for 10+ seasons)
  python -m pipeline.run daily        # new results + today's slate + odds -> refit -> docs/data/*.json
  python -m pipeline.run backtest     # walk-forward validation -> data/backtest_results.json (+ site rebuild)
  python -m pipeline.run demo         # synthetic end-to-end run -> docs/data/demo/ (DEMO MODE only)
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from .config import ROOT, iso, load_settings, utcnow, write_json
from .ingest import Ingestor, today_et
from .nhl_api import NHLApiError, NHLClient
from .storage import Store


def log(*a):
    print(*a, flush=True)


def load_overrides(today):
    p = ROOT / "config" / "goalie_overrides.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text()).get(today.isoformat(), {})


def cmd_backfill(settings, store, args):
    ing = Ingestor(NHLClient(), store, log)
    start = args.start or settings["backfill_start_season"]
    if args.source == "espn":
        from .espn import EspnClient
        ing.backfill_espn(EspnClient(), start, args.end)
    else:
        try:
            ing.backfill(start, args.end)
        except NHLApiError as e:
            log("NHL API failed during backfill:", e, "-> continuing with ESPN fallback")
            from .espn import EspnClient
            ing.backfill_espn(EspnClient(), start, args.end)
    log(f"backfill done: {len(ing.games)} games stored, {len(ing.errors)} errors")
    for e in ing.errors[:20]:
        log("  error:", e)


def cmd_daily(settings, store, args):
    from . import odds_api
    from .site import build_site
    now = utcnow()
    today = today_et()
    statuses = {"sources": []}
    client = NHLClient()
    ing = Ingestor(client, store, log)
    try:
        slate = ing.incremental(today)
        statuses["nhl_fetched_at"] = iso(now)
        statuses["sources"].append({"name": "NHL API (api-web.nhle.com)", "short": "NHL", "status": "ok",
                                    "fetched_at": iso(now), "used_for": "schedule, results, box scores, goalies, skaters",
                                    "detail": f"{client.calls} calls; {len(ing.errors)} box-score errors"})
        store.path("slate_cache.json").write_text(json.dumps({"date": today.isoformat(), "rows": slate}))
    except NHLApiError as e:
        log("NHL API failed:", e, "-> trying ESPN fallback")
        statuses["sources"].append({"name": "NHL API (api-web.nhle.com)", "short": "NHL", "status": "error",
                                    "fetched_at": None, "detail": str(e)[:200]})
        from . import espn
        ec = espn.EspnClient()
        try:
            slate = ing.incremental_espn(ec, today)
            statuses["nhl_fetched_at"] = iso(now)
            statuses["sources"].append({"name": espn.SOURCE_NAME, "short": "ESPN", "status": "ok", "fetched_at": iso(now),
                                        "used_for": "schedule, results, shots, goalies (NHL API unavailable)",
                                        "detail": f"{ec.calls} calls; goalie starters inferred from time on ice"})
            store.path("slate_cache.json").write_text(json.dumps({"date": today.isoformat(), "rows": slate}))
        except espn.EspnError as e2:
            log("ESPN fallback failed too:", e2)
            statuses["sources"].append({"name": espn.SOURCE_NAME, "short": "ESPN", "status": "error", "detail": str(e2)[:200]})
            cache = store.path("slate_cache.json")
            c = json.loads(cache.read_text()) if cache.exists() else {}
            slate = c.get("rows", []) if c.get("date") == today.isoformat() else []
    odds_rows = []
    key = odds_api.get_key()
    os_ = settings["odds"]
    if not key:
        statuses["odds_note"] = "ODDS_API_KEY not configured — no sportsbook prices, so no EV is computed."
        statuses["sources"].append({"name": odds_api.SOURCE_NAME, "short": "ODDS", "status": "missing_key",
                                    "used_for": "sportsbook prices", "detail": statuses["odds_note"]})
    elif not slate:
        statuses["sources"].append({"name": odds_api.SOURCE_NAME, "short": "ODDS", "status": "skipped",
                                    "detail": "no games today"})
    else:
        try:
            events, usage = odds_api.fetch_odds(key, os_["sport_key"], os_["regions"], os_["markets"], os_["bookmakers"])
            lut = odds_api.build_name_lookup(slate)
            odds_rows, unmatched = odds_api.normalize(events, iso(now), lut)
            odds_api.archive(odds_rows, store.dir, today.isoformat())
            statuses["odds_fetched_at"] = iso(now)
            statuses["odds_source"] = odds_api.SOURCE_NAME
            statuses["sources"].append({"name": odds_api.SOURCE_NAME, "short": "ODDS", "status": "ok", "fetched_at": iso(now),
                                        "used_for": "moneyline, puck line, totals prices",
                                        "detail": f"{len(odds_rows)} prices; credits remaining {usage.get('remaining')}"
                                                  + (f"; unmatched: {', '.join(unmatched)}" if unmatched else "")})
        except odds_api.OddsApiError as e:
            statuses["odds_note"] = f"Odds feed error: {e}"
            statuses["sources"].append({"name": odds_api.SOURCE_NAME, "short": "ODDS", "status": "error", "detail": str(e)[:200]})
    usage_remaining = None
    for src in statuses["sources"]:
        if src.get("short") == "ODDS" and "credits remaining" in str(src.get("detail")):
            try:
                usage_remaining = int(str(src["detail"]).split("credits remaining ")[1].split(";")[0])
            except (IndexError, ValueError):
                pass
    if not odds_rows and slate:
        odds_rows = latest_game_odds(store, today)
        if odds_rows:
            statuses["odds_fetched_at"] = odds_rows[0]["fetched_at"]
            statuses["odds_source"] = odds_api.SOURCE_NAME + " (last archived snapshot)"
    # ---- current rosters (full names, headshots, trade filter) -- once per day
    rosters = {}
    rp = store.path("rosters.json")
    cached = json.loads(rp.read_text()) if rp.exists() else {}
    teams_today = sorted({r["home"] for r in slate} | {r["away"] for r in slate})
    if cached.get("date") == today.isoformat() and all(t in cached.get("teams", {}) for t in teams_today):
        rosters = cached["teams"]
    elif teams_today:
        from .nhl_api import fetch_rosters
        rosters = {**cached.get("teams", {}), **fetch_rosters(client, teams_today)}
        rp.write_text(json.dumps({"date": today.isoformat(), "teams": rosters}))
    # ---- player prop odds (per-event endpoint; costs credits) -- once per day by default
    ps = settings.get("props", {})
    prop_note = None
    if key and slate and odds_rows and ps.get("odds_enabled"):
        archive_p = store.path(f"prop_odds/{today.isoformat()}.csv")
        hours = ps.get("fetch_hours_utc", [21])
        # props are posted in the afternoon: fetch on the configured run(s), or the first run after them
        want = (now.hour in hours) or (not archive_p.exists() and hours and now.hour >= min(hours))
        ev_ids = {}
        for r in odds_rows:
            for g in slate:
                if r["home"] == g["home"] and r["away"] == g["away"] and g.get("game_state") in ("FUT", "PRE"):
                    ev_ids[g["game_id"]] = (r["event_id"], g["home"], g["away"])
        est = len(ev_ids) * len(ps.get("markets", []))
        if want and ev_ids:
            if usage_remaining is not None and usage_remaining - est < ps.get("min_credits_reserve", 60):
                prop_note = (f"Prop odds skipped: ~{est} credits needed, {usage_remaining} remaining "
                             f"(reserve {ps.get('min_credits_reserve', 60)}).")
            else:
                prows, rem = [], usage_remaining
                for gid, (eid, h, a) in ev_ids.items():
                    try:
                        evj, use = odds_api.fetch_event_props(key, eid, ps["markets"], os_["sport_key"], os_["regions"], os_["bookmakers"])
                        prows += odds_api.normalize_props(evj, iso(now), gid, h, a)
                        rem = use.get("remaining", rem)
                    except odds_api.OddsApiError as e:
                        prop_note = f"Prop odds error: {e}"
                odds_api.archive_props(prows, store.dir, today.isoformat())
                prop_note = prop_note or f"{len(prows)} prop prices for {len(ev_ids)} games; credits remaining {rem}"
        statuses["sources"].append({"name": "The Odds API — player props", "short": "PROPS",
                                    "status": "ok" if archive_p.exists() else ("skipped" if prop_note else "waiting"),
                                    "fetched_at": iso(now) if want else None,
                                    "used_for": "player prop prices (shots, points, anytime goal, saves)",
                                    "detail": prop_note or ("using today's earlier prop snapshot" if archive_p.exists()
                                                            else f"prop odds are fetched on the {', '.join(str(h) for h in hours)}:xx UTC run")})
    prop_rows = odds_api.latest_props(store.dir, today.isoformat())
    if not len(store.games()):
        log("No historical games stored — run backfill first. Writing status-only meta.")
        write_json(ROOT / settings["site_data_dir"] / "meta.json", {"mode": "live", "generated_at": iso(now),
                   "sources": statuses["sources"], "model": {"status": "NO DATA"}, "unavailable": []})
        return
    build_site(store, slate, odds_rows, statuses, settings, ROOT / settings["site_data_dir"], now, today, "live",
               load_overrides(today), log, rosters=rosters, prop_rows=prop_rows)


def latest_game_odds(store, today):
    p = store.path(f"odds/{today.isoformat()}.csv")
    if not p.exists():
        return []
    df = pd.read_csv(p)
    if not len(df):
        return []
    df = df[df.fetched_at == df.fetched_at.max()]
    df["point"] = pd.to_numeric(df["point"], errors="coerce")
    rows = df.to_dict("records")
    for r in rows:
        if r["point"] != r["point"]:
            r["point"] = None
    return rows


def cmd_backtest(settings, store, args):
    from .backtest import run_backtest
    games, gg = store.games(), store.goalie_games()
    ho = store.path("historical_odds.csv")
    hist_odds = pd.read_csv(ho) if ho.exists() else None
    res = run_backtest(games, gg, settings, hist_odds, log)
    preds = res.pop("_preds", None)
    if preds is not None:
        preds.to_csv(store.path("backtest_preds.csv"), index=False)
    write_json(store.path("backtest_results.json"), res)
    from .props_backtest import run_props_backtest
    log("props backtest")
    pres = run_props_backtest(store, settings, log)
    write_json(store.path("props_backtest.json"), pres)
    log("backtest:", json.dumps({k: res.get(k) for k in ("available", "n_test_games", "test_period", "hyper", "runtime", "reason")}))


def cmd_demo(settings, store, args):
    from .demo import run_demo
    run_demo(settings, log)


def main(argv=None):
    ap = argparse.ArgumentParser(description="NHL EDGE PRO pipeline")
    ap.add_argument("mode", choices=["backfill", "daily", "backtest", "demo"])
    ap.add_argument("--start", type=int, help="first season for backfill, e.g. 20152016")
    ap.add_argument("--end", type=int, help="last season for backfill")
    ap.add_argument("--source", choices=["nhl", "espn"], default="nhl",
                    help="backfill source (default nhl, automatic ESPN fallback if the NHL API fails)")
    args = ap.parse_args(argv)
    settings = load_settings()
    store = Store(ROOT / settings["data_dir"])
    {"backfill": cmd_backfill, "daily": cmd_daily, "backtest": cmd_backtest, "demo": cmd_demo}[args.mode](settings, store, args)


if __name__ == "__main__":
    sys.exit(main())
