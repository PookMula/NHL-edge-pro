"""Forward (live) validation: locked pre-game predictions, closing lines, closing-line value, realized results,
and the per-market validation tier.

Nothing here can be back-filled: a prediction or candidate only counts if it was logged before puck drop,
and closing lines only exist for games whose odds this pipeline actually captured.
"""
import json
from pathlib import Path
from statistics import median

import numpy as np
import pandas as pd

from .backtest import _boot_ci, _ll
from .config import parse_iso
from .odds_math import american_to_decimal, no_vig_multiplicative

PRED_COLS = ["game_id", "locked_at", "start_utc", "date", "home", "away", "p_home_ml", "exp_total",
             "home_goalie", "away_goalie", "goalie_status"]
EDGE_COLS = ["key", "game_id", "date", "flagged_at", "start_utc", "market", "side", "point", "book", "book_key",
             "price", "p_win", "p_push", "ev", "ev_p10", "tier", "qualified"]
PARLAY_COLS = ["id", "date", "flagged_at", "book", "n_legs", "american", "decimal", "p_joint", "ev", "ev_p10",
               "bucket", "qualified", "legs"]


def load_odds_archive(data_dir):
    files = sorted((Path(data_dir) / "odds").glob("*.csv"))
    if not files:
        return pd.DataFrame()
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    df["point"] = pd.to_numeric(df["point"], errors="coerce")
    return df


def closing_snapshot(arch, home, away, start_utc):
    if not len(arch):
        return None
    st = parse_iso(start_utc)
    d = arch[(arch.home == home) & (arch.away == away)]
    if not len(d):
        return None
    ts = pd.to_datetime(d.fetched_at, utc=True)
    d = d[(ts < st) & (ts > st - pd.Timedelta(hours=36))]
    if not len(d):
        return None
    last = d[d.fetched_at == d.fetched_at.max()]
    return last


def close_novig(snap, market, side, point):
    """Median across books of the no-vig closing probability for this exact selection (same point)."""
    if snap is None:
        return None
    pt = None if point is None or point != point else float(point)
    probs = []
    for book, b in snap.groupby("book"):
        def sel(s, p):
            m = (b.market == market) & (b.side == s)
            m &= b.point.isna() if p is None else (np.abs(b.point - p) < 1e-9)
            r = b[m]
            return float(r.price.iloc[0]) if len(r) else None
        if market == "totals":
            other_side, other_pt = ("under" if side == "over" else "over"), pt
        else:
            other_side, other_pt = ("away" if side == "home" else "home"), (None if pt is None else -pt)
        a, o = sel(side, pt), sel(other_side, other_pt)
        if a is not None and o is not None:
            probs.append(no_vig_multiplicative([a, o])[0])
    return float(median(probs)) if probs else None


def settle(market, side, point, hg, ag):
    """Return 'win' | 'loss' | 'push' for a full-game bet given final goals."""
    if market == "h2h":
        won = hg > ag if side == "home" else ag > hg
        return "win" if won else "loss"
    if market == "spreads":
        m = (hg - ag) if side == "home" else (ag - hg)
        v = m + float(point)
    else:
        t = hg + ag
        v = (t - float(point)) if side == "over" else (float(point) - t)
    return "win" if v > 1e-9 else "push" if abs(v) < 1e-9 else "loss"


def append_unique(path, rows, cols, key):
    path = Path(path)
    old = pd.read_csv(path) if path.exists() and path.stat().st_size else pd.DataFrame(columns=cols)
    new = pd.DataFrame(rows, columns=cols)
    if not len(new):
        return old
    df = pd.concat([old, new[~new[key].astype(str).isin(old[key].astype(str))]], ignore_index=True)
    df.to_csv(path, index=False)
    return df


def upsert_predictions(path, rows):
    """Keep updating a game's prediction until it starts; then it is locked (never changed again)."""
    path = Path(path)
    old = pd.read_csv(path) if path.exists() and path.stat().st_size else pd.DataFrame(columns=PRED_COLS)
    now = pd.Timestamp.utcnow()
    locked = set()
    for r in old.itertuples(index=False):
        if pd.Timestamp(r.start_utc) <= now:
            locked.add(int(r.game_id))
    new = [r for r in rows if int(r["game_id"]) not in locked]
    keep = old[~old.game_id.astype(int).isin({int(r["game_id"]) for r in new})]
    df = pd.concat([keep, pd.DataFrame(new, columns=PRED_COLS)], ignore_index=True)
    df.to_csv(path, index=False)
    return df


def evaluate(data_dir, games, backtest_tiers, settings):
    data_dir = Path(data_dir)
    es = settings["edges"]
    arch = load_odds_archive(data_dir)
    res = {int(r.game_id): (int(r.home_goals), int(r.away_goals)) for r in games.itertuples(index=False)}
    out = {"min_market_games": es["min_market_games_for_competitive"], "min_clv_bets": es["min_clv_bets_for_support"]}

    # --- model vs closing market (moneyline) ---
    pl = data_dir / "predictions_log.csv"
    preds = pd.read_csv(pl) if pl.exists() and pl.stat().st_size else pd.DataFrame(columns=PRED_COLS)
    rows = []
    for r in preds.itertuples(index=False):
        if int(r.game_id) not in res:
            continue
        snap = closing_snapshot(arch, r.home, r.away, r.start_utc)
        pm = close_novig(snap, "h2h", "home", None)
        if pm is None:
            continue
        hg, ag = res[int(r.game_id)]
        rows.append({"date": r.date, "y": float(hg > ag), "p": r.p_home_ml, "m": pm})
    ml = pd.DataFrame(rows)
    out["n_games"] = int(len(ml))
    if len(ml) >= 20:
        lm, lk = _ll(ml.p.to_numpy(), ml.y.to_numpy()), _ll(ml.m.to_numpy(), ml.y.to_numpy())
        out["ml"] = {"model_ll": float(lm.mean()), "market_ll": float(lk.mean()), "diff": float((lm - lk).mean()),
                     "ci": _boot_ci(lm - lk, ml.date.to_numpy())}
        ml = ml.sort_values("date")
        roll = pd.DataFrame({"date": ml.date, "model": pd.Series(lm).rolling(100, min_periods=30).mean().values,
                             "market": pd.Series(lk).rolling(100, min_periods=30).mean().values}).dropna()
        out["drift"] = roll.iloc[::max(1, len(roll) // 200)].to_dict("records")

    # --- closing-line value + realized results of logged candidates ---
    el = data_dir / "edge_log.csv"
    edges = pd.read_csv(el) if el.exists() and el.stat().st_size else pd.DataFrame(columns=EDGE_COLS)
    clv_rows, realized = [], []
    for r in edges.itertuples(index=False):
        hg_ag = res.get(int(r.game_id))
        point = None if pd.isna(r.point) else float(r.point)
        g = games[games.game_id == r.game_id]
        snap = closing_snapshot(arch, g.home.iloc[0], g.away.iloc[0], r.start_utc) if len(g) else None
        pc = close_novig(snap, r.market, r.side, point)
        d = american_to_decimal(r.price)
        if pc is not None:
            clv_rows.append({"date": r.date, "market": r.market, "clv": pc * d - 1})
        if hg_ag:
            o = settle(r.market, r.side, point, *hg_ag)
            realized.append({"date": r.date, "pnl": (d - 1) if o == "win" else 0.0 if o == "push" else -1.0, "exp": r.ev})
    clv = pd.DataFrame(clv_rows)
    out["clv"] = {"n": int(len(clv))}
    if len(clv) >= 10:
        out["clv"].update({"mean": float(clv.clv.mean()), "ci": _boot_ci(clv.clv.to_numpy(), clv.date.to_numpy())})
    rz = pd.DataFrame(realized)
    out["realized"] = {"n": int(len(rz))}
    if len(rz):
        rz = rz.sort_values("date")
        cum = rz.pnl.cumsum().to_numpy()
        out["realized"].update({"roi": float(rz.pnl.mean()), "expected_roi": float(rz.exp.mean()),
                                "ci": _boot_ci(rz.pnl.to_numpy(), rz.date.to_numpy()) if len(rz) >= 10 else None,
                                "max_dd": float(np.max(np.maximum.accumulate(np.concatenate([[0], cum])) - np.concatenate([[0], cum]))),
                                "curve": cum.round(3).tolist()[-1000:]})

    # --- tiers ---
    tiers = {}
    for mk in ("h2h", "spreads", "totals"):
        c = clv[clv.market == mk] if len(clv) else clv
        bt = backtest_tiers.get(mk)
        clv_ci = _boot_ci(c.clv.to_numpy(), c.date.to_numpy()) if len(c) >= 10 else None
        if len(c) >= es["min_clv_bets_for_support"] and clv_ci and clv_ci[0] > 0:
            tier, why = "EDGE_SUPPORTED", f"{len(c)} logged candidates beat the closing no-vig price on average (95% CI {clv_ci[0]:+.3f} to {clv_ci[1]:+.3f})."
        elif mk == "h2h" and out.get("ml") and out["n_games"] >= es["min_market_games_for_competitive"] and out["ml"]["diff"] <= 0:
            tier, why = "MARKET_COMPETITIVE", f"Over {out['n_games']} live games the model's log loss is no worse than the closing market's."
        elif mk != "h2h" and len(c) >= 100 and c.clv.mean() >= 0:
            tier, why = "MARKET_COMPETITIVE", f"{len(c)} logged candidates show non-negative average CLV (not yet significant)."
        elif bt:
            tier, why = "BEATS_BASELINE", "Walk-forward backtest beats the naive baseline (95% CI above zero); not yet shown to beat the market."
        else:
            tier, why = "EXPERIMENTAL", "No out-of-sample evidence yet that this market model beats even a naive baseline."
        tiers[mk] = {"tier": tier, "reason": why,
                     "backtest": "beats baseline" if bt else ("not shown" if bt is False else "no backtest yet"),
                     "market": (f"Δlog loss {out['ml']['diff']:+.4f} over {out['n_games']} games" if mk == "h2h" and out.get("ml") else "insufficient live data"),
                     "clv": (f"{len(c)} candidates, mean {c.clv.mean():+.2%}" if len(c) else "no closed candidates yet")}
    out["tiers"] = tiers
    return out


def parlay_track(data_dir, games):
    p = Path(data_dir) / "parlay_log.csv"
    if not p.exists() or not p.stat().st_size:
        return {"n_settled": 0}
    pl = pd.read_csv(p)
    res = {int(r.game_id): (int(r.home_goals), int(r.away_goals)) for r in games.itertuples(index=False)}
    rows = []
    for r in pl.itertuples(index=False):
        legs = json.loads(r.legs)
        if not all(int(l["game_id"]) in res for l in legs):
            continue
        pay, lost = 1.0, False
        for l in legs:
            o = settle(l["market"], l["side"], l["point"], *res[int(l["game_id"])])
            if o == "loss":
                lost = True
                break
            if o == "win":
                pay *= american_to_decimal(l["price"])
        rows.append({"date": r.date, "hit": 0 if lost else 1, "pnl": -1.0 if lost else pay - 1, "p": r.p_joint, "ev": r.ev,
                     "american": r.american, "bucket": r.bucket, "legs": r.n_legs})
    if not rows:
        return {"n_settled": 0}
    d = pd.DataFrame(rows).sort_values("date")
    cum = d.pnl.cumsum().to_numpy()
    groups = []
    for col, lab in (("bucket", "+{}"), ("legs", "{} legs")):
        for k, g in d.groupby(col):
            groups.append({"group": lab.format(k), "n": len(g), "hits": int(g.hit.sum()), "expected_hits": float(g.p.sum()),
                           "roi": float(g.pnl.mean())})
    return {"n_settled": int(len(d)), "hit_rate": float(d.hit.mean()), "expected_hit_rate": float(d.p.mean()),
            "avg_american": float(d.american.mean()), "avg_expected_roi": float(d.ev.mean()), "roi": float(d.pnl.mean()),
            "max_dd": float(np.max(np.maximum.accumulate(np.concatenate([[0], cum])) - np.concatenate([[0], cum]))),
            "groups": groups}
