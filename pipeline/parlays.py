"""Parlay Intelligence Lab: correlation-aware search for +600-or-longer parlays.

* A parlay is placed at ONE sportsbook, so legs are priced at that book; the multi-game payout is the
  product of the book's leg prices (standard settlement for cross-game parlays).
* Same-game parlays are NOT priced: books quote SGPs with their own correlation adjustment, and the
  connected odds feed does not provide those quotes. Multiplying single-game prices would be wrong.
* Joint probability is computed per posterior draw: P(all win | params) = prod p_win(leg | params);
  games are independent GIVEN the parameters, but every leg shares the same parameter draw, so model
  uncertainty correlates the legs. Reported: mean, 10th/90th percentile, and the naive product of means.
* Pushes: a pushed leg is removed (odds reduced). E[payout | params] = prod(p_win*d + p_push).
* Legs must individually have positive EV, so no weak leg is ever added just to reach a payout target.
"""
import hashlib
import itertools

import numpy as np

from .odds_math import decimal_to_american

BUCKETS = [(600, 999, "600-999"), (1000, 1499, "1000-1499"), (1500, 2499, "1500-2499"), (2500, 10 ** 9, "2500+")]
CORRELATION_NOTE = ("Legs are in different games. Outcomes are treated as independent given the model parameters, "
                    "and joint probability is averaged over posterior parameter draws, so shared model uncertainty "
                    "(e.g. league scoring level, home-ice size) is carried into the joint estimate. "
                    "The difference from the naive product of leg probabilities is shown as the correlation impact.")


def bucket(american):
    for lo, hi, name in BUCKETS:
        if lo <= american <= hi:
            return name
    return None


def _metrics(legs):
    pw = np.prod([l["pw"] for l in legs], axis=0)
    pay = np.prod([l["pw"] * l["decimal"] + l["pp"] for l in legs], axis=0)
    ev = pay - 1.0
    dec = float(np.prod([l["decimal"] for l in legs]))
    return {"decimal": dec, "american": decimal_to_american(dec), "p_joint": float(pw.mean()),
            "p_joint_lo": float(np.quantile(pw, 0.1)), "p_joint_hi": float(np.quantile(pw, 0.9)),
            "p_joint_naive": float(np.prod([l["pw"].mean() for l in legs])),
            "ev": float(ev.mean()), "ev_p10": float(np.quantile(ev, 0.1)), "pw_draws": pw}


def build_legs(evals, preds_by_game, game_meta):
    """Per book: list of leg dicts with per-draw probabilities."""
    by_book = {}
    for e in evals:
        if not (e["ev"] > 0 and e["fresh"] and e["goalie_status"] != "UNKNOWN"):
            continue
        pred = preds_by_game[e["game_id"]]
        m = pred.market(e["market"], e["side"], e["point"])
        by_book.setdefault(e["book"], []).append({**e, "pw": m["p_draws"], "pp": m["push_draws"],
                                                  "game": game_meta[e["game_id"]]["title"]})
    return by_book


def search(evals, preds_by_game, game_meta, settings, tiers, gate, label_fn, now_iso):
    ps = settings["parlays"]
    min_dec = 1 + ps["min_american"] / 100.0
    by_book = build_legs(evals, preds_by_game, game_meta)
    n_searched = 0
    found = []
    for book, legs in by_book.items():
        legs = sorted(legs, key=lambda l: -l["ev_p10"])
        # one leg per game: keep the best-scoring selection per game
        seen, pool = set(), []
        for l in legs:
            if l["game_id"] in seen:
                continue
            seen.add(l["game_id"])
            pool.append(l)
        pool = pool[:ps["legs_pool_per_book"]]
        for k in range(2, ps["max_legs"] + 1):
            for combo in itertools.combinations(pool, k):
                n_searched += 1
                dec = float(np.prod([l["decimal"] for l in combo]))
                if dec < min_dec:
                    continue
                m = _metrics(combo)
                if m["ev_p10"] <= 0:
                    continue
                found.append((book, combo, m))
    found.sort(key=lambda x: -x[2]["ev_p10"])
    out_q, out_c, use = [], [], {}
    per_bucket = {}
    for book, combo, m in found:
        b = bucket(m["american"])
        if b is None:
            continue
        ids = [f'{l["game_id"]}|{l["market"]}|{l["side"]}|{l["point"]}' for l in combo]
        if any(use.get(i, 0) >= ps["max_reuse_of_leg"] for i in ids):
            continue
        if per_bucket.get(b, 0) >= ps["max_candidates_per_bucket"]:
            continue
        per_bucket[b] = per_bucket.get(b, 0) + 1
        for i in ids:
            use[i] = use.get(i, 0) + 1
        legs_tiers = [tiers.get(l["market"], {}).get("tier", "EXPERIMENTAL") for l in combo]
        from .edges import tier_ok
        qualified = all(tier_ok(t, gate) for t in legs_tiers)
        pid = hashlib.sha1(("|".join(sorted(ids)) + book).encode()).hexdigest()[:12]
        removal = []
        for i, l in enumerate(combo):
            rest = combo[:i] + combo[i + 1:]
            if len(rest) >= 1:
                mm = _metrics(rest)
                removal.append({"removed": label_fn(l), "american": mm["american"], "p_joint": mm["p_joint"],
                                "ev": mm["ev"], "ev_p10": mm["ev_p10"]})
        weakest = min(combo, key=lambda l: l["p_win"])
        hist_counts, hist_edges = np.histogram(m["pw_draws"], bins=12)
        rec = {
            "id": pid, "book": book, "type": "multi", "n_legs": len(combo), "bucket": b,
            "legs": [{"game_id": l["game_id"], "game": l["game"], "label": label_fn(l), "market": l["market"],
                      "side": l["side"], "point": l["point"], "price": l["price"], "p_win": l["p_win"], "ev": l["ev"],
                      "goalie_status": l["goalie_status"], "tier": t,
                      "reason": f"model {100 * l['p_win']:.1f}% vs break-even {100 * l['break_even']:.1f}%; "
                                f"leg EV {100 * l['ev']:+.1f}% (p10 {100 * l['ev_p10']:+.1f}%)"}
                     for l, t in zip(combo, legs_tiers)],
            "decimal": m["decimal"], "american": m["american"], "payout_basis": "product of this book's leg prices",
            "p_joint": m["p_joint"], "p_joint_lo": m["p_joint_lo"], "p_joint_hi": m["p_joint_hi"],
            "p_joint_naive": m["p_joint_naive"], "break_even": 1 / m["decimal"],
            "fair_american": decimal_to_american(1 / m["p_joint"]) if m["p_joint"] > 0 else None,
            "ev": m["ev"], "ev_p10": m["ev_p10"], "qualified": qualified,
            "status_reason": "every leg's market meets the validation gate" if qualified else
                             f"leg markets not yet at {gate}: {', '.join(sorted(set(legs_tiers)))}",
            "goalies_confirmed": all(l["goalie_status"] == "CONFIRMED" for l in combo),
            "leg_removal": removal, "correlation_note": CORRELATION_NOTE,
            "risks": [f"Weakest leg: {label_fn(weakest)} at {100 * weakest['p_win']:.1f}% — it alone loses the parlay "
                      f"{100 * (1 - weakest['p_win']):.0f}% of the time.",
                      f"All {len(combo)} legs must win: the model's joint estimate is {100 * m['p_joint']:.1f}% "
                      f"(80% band {100 * m['p_joint_lo']:.1f}–{100 * m['p_joint_hi']:.1f}%).",
                      "Starting goalies are projected, not confirmed." if not all(l["goalie_status"] == "CONFIRMED" for l in combo) else
                      "Goalies confirmed, but injuries/lineups are not in the data feed.",
                      "Chosen as the best of many searched combinations: expect realized returns below the modeled EV (selection bias)."],
            "p_joint_hist": {"counts": hist_counts.tolist(), "edges": hist_edges.tolist()},
            "generated_at": now_iso, "odds_updated": min(l["odds_updated"] for l in combo),
        }
        (out_q if qualified else out_c).append(rec)
    return out_q, out_c, n_searched
