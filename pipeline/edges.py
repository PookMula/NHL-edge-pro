"""Expected value for every priced bet, no-vig market probabilities, and the edge gate.

Gate for a QUALIFIED edge (all must hold):
  1. EV (posterior mean) >= min_ev
  2. pessimistic EV (10th percentile over posterior draws) > min_ev_lower
  3. odds fetched within max_odds_age_minutes, game not started
  4. both goalies known (if exclude_unknown_goalies)
  5. the market's validation tier >= gate_tier (default EDGE_SUPPORTED: logged candidates have
     beaten closing prices out of sample with a CI above zero)
Bets passing 1-4 but not 5 are CANDIDATES: shown as experimental and logged for CLV tracking.

Ranking score = pessimistic EV (p10). Transparent and directly penalises uncertainty.
"""
from collections import defaultdict
from statistics import median

import numpy as np

from .config import parse_iso
from .odds_math import american_to_decimal, break_even_probability, fair_american_with_push, kelly_fraction, \
    no_vig_multiplicative

TIER_ORDER = ["EXPERIMENTAL", "BEATS_BASELINE", "MARKET_COMPETITIVE", "EDGE_SUPPORTED"]
RANKING_FORMULA = ("score = EV_p10 = 10th percentile over posterior parameter draws of [p_win*(decimal-1) - p_loss]. "
                   "Qualify: EV_mean >= min_ev AND EV_p10 > min_ev_lower AND odds fresh AND goalies known AND "
                   "market tier >= gate tier. Uncertainty, calibration and data quality enter through EV_p10 and the tier; "
                   "goalie sensitivity is reported per bet.")


def tier_ok(tier, gate):
    return TIER_ORDER.index(tier or "EXPERIMENTAL") >= TIER_ORDER.index(gate)


def same_point(a, b):
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) < 1e-9


def opposite(row):
    if row["market"] == "totals":
        return ("under" if row["side"] == "over" else "over"), row["point"]
    other = "away" if row["side"] == "home" else "home"
    return other, (-row["point"] if row["point"] is not None else None)


def novig_by_book(rows):
    """{(market, side, point): [no-vig prob at each book that prices both sides]}"""
    idx = {(r["book"], r["market"], r["side"], r["point"]): r for r in rows}
    out = defaultdict(list)
    for r in rows:
        os_, op = opposite(r)
        o = idx.get((r["book"], r["market"], os_, op))
        if o is None:
            continue
        p = no_vig_multiplicative([r["price"], o["price"]])[0]
        out[(r["market"], r["side"], r["point"])].append(p)
    return out


def evaluate_game(game_rec, pred, odds_rows, now, settings, tiers, swap_pred=None, gate=None):
    """Return list of evaluated bet dicts for every (book, market, side, point) priced for this game."""
    es = settings["edges"]
    gate = gate or es["gate_tier"]
    g = game_rec["game"]
    nv = novig_by_book(odds_rows)
    started = g.get("game_state") not in (None, "FUT", "PRE")
    gs_home, gs_away = game_rec["home"]["status"], game_rec["away"]["status"]
    goalie_status = "CONFIRMED" if gs_home == gs_away == "CONFIRMED" else ("UNKNOWN" if "UNKNOWN" in (gs_home, gs_away) else "PROJECTED")
    out = []
    cache = {}
    for r in odds_rows:
        key = (r["market"], r["side"], r["point"])
        if key not in cache:
            cache[key] = pred.market(*key)
        m = cache[key]
        d = american_to_decimal(r["price"])
        ev_draws = m["p_draws"] * (d - 1) - (1 - m["p_draws"] - m["push_draws"])
        ev = float(ev_draws.mean())
        ev_p10 = float(np.quantile(ev_draws, 0.1))
        be = break_even_probability(d, m["push"])
        age_min = (now - parse_iso(r["fetched_at"])).total_seconds() / 60
        fresh = age_min <= settings["odds"]["max_odds_age_minutes"]
        try:
            fair = fair_american_with_push(m["p"], m["push"])
        except ValueError:
            fair = None
        nvs = nv.get(key, [])
        ev_swap = None
        if swap_pred is not None:
            ms = swap_pred.market(*key)
            ev_swap = ms["p"] * (d - 1) - (1 - ms["p"] - ms["push"])
        tier = tiers.get(r["market"], {}).get("tier", "EXPERIMENTAL")
        candidate = (ev >= es["min_ev"] and ev_p10 > es["min_ev_lower"] and fresh and not started and
                     not (es.get("exclude_unknown_goalies", True) and goalie_status == "UNKNOWN"))
        out.append({
            "game_id": g["game_id"], "market": r["market"], "side": r["side"], "point": r["point"],
            "book": r["book_title"], "book_key": r["book"], "price": r["price"], "decimal": d,
            "p_win": m["p"], "p_push": m["push"], "p_win_lo": m["p_lo"], "p_win_hi": m["p_hi"],
            "p_market_novig": float(median(nvs)) if nvs else None,
            "novig_method": f"multiplicative, both sides at same book, median of {len(nvs)} book(s)" if nvs else
                            "opposite side not offered — no precise no-vig probability",
            "fair_american": fair, "break_even": be, "prob_edge": m["p"] - be, "ev": ev, "ev_p10": ev_p10,
            "ev_goalie_swap": ev_swap, "kelly_quarter": 0.25 * kelly_fraction(m["p"], d, m["push"]),
            "odds_updated": r["fetched_at"], "book_last_update": r.get("last_update"), "fresh": fresh,
            "goalie_status": goalie_status, "tier": tier, "candidate": candidate,
            "qualified": candidate and tier_ok(tier, gate),
        })
    return out


def best_per_selection(evals):
    best = {}
    for e in evals:
        k = (e["game_id"], e["market"], e["side"], e["point"])
        if k not in best or e["decimal"] > best[k]["decimal"]:
            best[k] = e
    return list(best.values())
