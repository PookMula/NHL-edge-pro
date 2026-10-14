"""Chronological walk-forward backtest (no look-ahead).

For every evaluation date D:
  * the team model is refit on games with date < D only (warm-started, recency-weighted);
  * auxiliary parameters (phi, theta, OT/SO) are refit every `aux_refit_days` from games < D;
  * goalie features are pre-game ratings computed sequentially; the starter used is the RULE-PROJECTED one
    (what would have been known pre-game). Actual starters are scored separately as a hindsight upper bound;
  * baselines use rolling 365-day empirical frequencies from games < D.

Seasons: burn-in (training only) -> tuning (choose half-life & ridge) -> test (reported, never used to tune).
Uncertainty: 95% CIs from a date-block bootstrap of per-game score differences.
Market benchmark + ROI only if data/historical_odds.csv (licensed, real) is supplied.
"""
import time

import numpy as np
import pandas as pd

from .engine import History
from .goalies import ordinal
from .odds_math import american_to_decimal, no_vig_multiplicative
from .probability import distributions

EPS = 1e-12


def _ll(p, y):
    p = np.clip(p, EPS, 1 - EPS)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def _boot_ci(diff, dates, reps=1000, seed=7):
    rng = np.random.default_rng(seed)
    ud, inv = np.unique(dates, return_inverse=True)
    sums = np.bincount(inv, weights=diff, minlength=len(ud))
    cnts = np.bincount(inv, minlength=len(ud))
    if len(ud) < 5:
        return None
    stats = []
    for _ in range(reps):
        s = rng.integers(0, len(ud), len(ud))
        stats.append(sums[s].sum() / cnts[s].sum())
    return [float(np.quantile(stats, 0.025)), float(np.quantile(stats, 0.975))]


def _calibration(p, y, bins=10):
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    out = []
    for b in range(bins):
        m = idx == b
        if m.sum() >= 10:
            out.append({"bin": b, "p_mean": float(p[m].mean()), "y_mean": float(y[m].mean()), "n": int(m.sum())})
    return out


def walk(hist, eval_mask, hp, aux_refit_days=30, log=None):
    games = hist.games
    dates = np.array(sorted(set(games.loc[eval_mask, "date"].astype(str))))
    model, aux, last_aux = None, None, -10 ** 9
    recs = []
    gdates = games["date"].astype(str).to_numpy()
    gord = hist.dord
    final_tot = (games.home_goals + games.away_goals).to_numpy()
    hw = (games.home_goals > games.away_goals).to_numpy().astype(float)
    margin = (games.home_goals - games.away_goals).to_numpy()
    reg_res = np.sign(hist.reg_h - hist.reg_a)
    for k, d in enumerate(dates):
        od = ordinal(d)
        need_aux = od - last_aux >= aux_refit_days
        try:
            model = hist.fit(od, hp, warm=model, with_aux=need_aux, aux_prior=aux)
        except ValueError:
            continue
        if need_aux:
            aux, last_aux = model.aux, od
        idx = np.where((gdates == d) & eval_mask)[0]
        rows = np.repeat(idx * 2, 2) + np.tile([0, 1], len(idx))
        po = hist.playoff[idx]
        lh, la = model.lambdas(hist.X_proj[rows])
        lha, laa = model.lambdas(hist.X[rows])
        dp = distributions(lh, la, model.aux, po)
        da = distributions(lha, laa, model.aux, po)
        # rolling 365-day baseline from prior games
        prior = (gord < od) & (gord >= od - 365)
        bh = hw[prior].mean()
        b_reg = [np.mean(reg_res[prior] == s) for s in (1, 0, -1)]
        b_pl_h, b_pl_a = np.mean(margin[prior] >= 2), np.mean(margin[prior] <= -2)
        b_o55, b_o65 = np.mean(final_tot[prior] > 5.5), np.mean(final_tot[prior] > 6.5)
        tot_counts = np.bincount(np.minimum(final_tot[prior], 20), minlength=21) + 0.5
        tot_p = tot_counts / tot_counts.sum()
        for j, gi in enumerate(idx):
            D, A = dp[j], da[j]
            t = int(final_tot[gi])
            r = int(reg_res[gi])
            recs.append({
                "i": gi, "date": d, "season": int(games.season.iloc[gi]),
                "y_home": hw[gi], "p_home": D.p_ml_home, "p_home_actual": A.p_ml_home, "b_home": bh,
                "reg_res": r, "p_reg": [D.p_reg_home, D.p_tie, D.p_reg_away][1 - r], "b_reg": b_reg[1 - r],
                "y_pl_h": float(margin[gi] >= 2), "p_pl_h": D.spread("home", -1.5)[0], "b_pl_h": b_pl_h,
                "y_pl_a": float(margin[gi] <= -2), "p_pl_a": D.spread("away", -1.5)[0], "b_pl_a": b_pl_a,
                "y_o55": float(t > 5.5), "p_o55": D.total_market("over", 5.5)[0], "b_o55": b_o55,
                "y_o65": float(t > 6.5), "p_o65": D.total_market("over", 6.5)[0], "b_o65": b_o65,
                "p_tot": float(D.total[t]) if t < len(D.total) else EPS, "b_tot": float(tot_p[min(t, 20)]),
                "exp_total": D.expected_total(), "total": t,
                "home": games.home.iloc[gi], "away": games.away.iloc[gi]})
        if log and k % 100 == 0:
            log(f"    {d}: {len(recs)} games scored")
    return pd.DataFrame(recs)


def score(df, reps=1000):
    dates = df["date"].to_numpy()
    out = {}

    def binary(key, label, ycol, pcol, bcol, cal=True):
        y, p, b = df[ycol].to_numpy(), df[pcol].to_numpy(), df[bcol].to_numpy()
        lm, lb = _ll(p, y), _ll(b, y)
        r = {"label": label, "metric": "log loss", "model": float(lm.mean()), "baseline": float(lb.mean()),
             "improvement": float((lb - lm).mean()), "ci": _boot_ci(lb - lm, dates, reps),
             "brier_model": float(((p - y) ** 2).mean()), "brier_baseline": float(((b - y) ** 2).mean()),
             "n": int(len(y))}
        if cal:
            r["calibration"] = _calibration(p, y)
        out[key] = r

    binary("ml", "Full-game moneyline (home win)", "y_home", "p_home", "b_home")
    lm = -np.log(np.clip(df.p_reg, EPS, 1))
    lb = -np.log(np.clip(df.b_reg, EPS, 1))
    out["reg3"] = {"label": "Regulation 3-way", "metric": "log loss", "model": float(lm.mean()), "baseline": float(lb.mean()),
                   "improvement": float((lb - lm).mean()), "ci": _boot_ci((lb - lm).to_numpy(), dates, reps), "n": len(df)}
    binary("pl_home", "Puck line: home −1.5", "y_pl_h", "p_pl_h", "b_pl_h")
    binary("pl_away", "Puck line: away −1.5", "y_pl_a", "p_pl_a", "b_pl_a")
    binary("o55", "Total over 5.5", "y_o55", "p_o55", "b_o55")
    binary("o65", "Total over 6.5", "y_o65", "p_o65", "b_o65")
    lm = -np.log(np.clip(df.p_tot, EPS, 1))
    lb = -np.log(np.clip(df.b_tot, EPS, 1))
    out["total"] = {"label": "Final total distribution", "metric": "log score", "model": float(lm.mean()),
                    "baseline": float(lb.mean()), "improvement": float((lb - lm).mean()),
                    "ci": _boot_ci((lb - lm).to_numpy(), dates, reps), "n": len(df),
                    "mae_total": float(np.abs(df.exp_total - df.total).mean())}
    return out


def market_comparison(df, hist_odds, min_ev=0.02, reps=1000):
    if hist_odds is None or not len(hist_odds):
        return None
    ho = hist_odds.copy()
    ho["date"] = ho["date"].astype(str).str[:10]
    m = df.merge(ho, on=["date", "home", "away"], how="inner")
    m = m.dropna(subset=["home_ml", "away_ml"])
    if len(m) < 50:
        return None
    nv = np.array([no_vig_multiplicative([h, a])[0] for h, a in zip(m.home_ml, m.away_ml)])
    lm, lk = _ll(m.p_home.to_numpy(), m.y_home.to_numpy()), _ll(nv, m.y_home.to_numpy())
    pnl, dates_b = [], []
    for r in m.itertuples(index=False):
        for side, p, price, y in (("home", r.p_home, r.home_ml, r.y_home), ("away", 1 - r.p_home, r.away_ml, 1 - r.y_home)):
            d = american_to_decimal(price)
            if p * (d - 1) - (1 - p) >= min_ev:
                pnl.append((d - 1) if y == 1 else -1.0)
                dates_b.append(r.date)
    pnl = np.array(pnl)
    res = {"n": int(len(m)), "model_ll": float(lm.mean()), "market_ll": float(lk.mean()), "diff": float((lm - lk).mean()),
           "ci": _boot_ci(lm - lk, m.date.to_numpy(), reps), "min_ev": min_ev, "n_bets": int(len(pnl))}
    if len(pnl):
        cum = np.cumsum(pnl)
        res.update({"roi": float(pnl.mean()), "roi_ci": _boot_ci(pnl, np.array(dates_b), reps),
                    "max_dd": float(np.max(np.maximum.accumulate(np.concatenate([[0], cum])) - np.concatenate([[0], cum])))})
    return res


def run_backtest(games, goalie_games, settings, hist_odds=None, log=print):
    t0 = time.time()
    ms, bs = settings["model"], settings["backtest"]
    games = games.sort_values(["date", "game_id"]).reset_index(drop=True)
    seasons = sorted(int(s) for s in games.season.unique())
    nb, nt = bs["burn_in_seasons"], bs["tuning_seasons"]
    if len(seasons) < nb + 1:
        return {"available": False, "reason": f"Need at least {nb + 1} seasons of results; found {len(seasons)}."}
    hist = History(games, goalie_games, ms)
    burn = seasons[:nb]
    tune = seasons[nb:nb + nt] if len(seasons) > nb + nt else []
    test = seasons[nb + len(tune):]
    hp = dict(ms)
    grid = []
    if tune and ms.get("tune_hyperparameters", True):
        tmask = games.season.isin(tune).to_numpy()
        for hl in bs["grid_half_life_days"]:
            for rg in bs["grid_ridge"]:
                h = dict(ms, half_life_days=hl, ridge=rg)
                log(f"  tuning half-life={hl} ridge={rg}")
                d = walk(hist, tmask, h, bs["aux_refit_days"])
                ll = float(_ll(d.p_home, d.y_home).mean())
                tl = float(-np.log(np.clip(d.p_tot, EPS, 1)).mean())
                grid.append({"half_life_days": hl, "ridge": rg, "ml_logloss": ll, "total_logscore": tl, "n": len(d)})
        best = min(grid, key=lambda g: g["ml_logloss"])
        hp.update(half_life_days=best["half_life_days"], ridge=best["ridge"])
    log(f"  test walk-forward on {test} with half-life={hp['half_life_days']} ridge={hp['ridge']}")
    emask = games.season.isin(test).to_numpy()
    df = walk(hist, emask, hp, bs["aux_refit_days"], log)
    if not len(df):
        return {"available": False, "reason": "No test games could be scored."}
    reps = bs["bootstrap_reps"]
    markets = score(df, reps)
    by_season = []
    for s, d in df.groupby("season"):
        lm, lb = _ll(d.p_home.to_numpy(), d.y_home.to_numpy()), _ll(d.b_home.to_numpy(), d.y_home.to_numpy())
        tm, tb = -np.log(np.clip(d.p_tot, EPS, 1)), -np.log(np.clip(d.b_tot, EPS, 1))
        by_season.append({"season": f"{str(s)[:4]}-{str(s)[6:]}", "n": len(d), "model_ll": float(lm.mean()),
                          "base_ll": float(lb.mean()), "improvement": float((lb - lm).mean()),
                          "ci": _boot_ci(lb - lm, d.date.to_numpy(), reps), "totals_improvement": float((tb - tm).mean())})
    fmt = lambda ss: f"{str(ss[0])[:4]}–{str(ss[-1])[4:]}" if ss else "—"
    out = {
        "available": True, "generated_at": pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "runtime": f"{time.time() - t0:.0f}s", "n_test_games": int(len(df)),
        "burn_in_period": fmt(burn), "tuning_period": fmt(tune), "test_period": fmt(test),
        "hyper": {"half_life_days": hp["half_life_days"], "ridge": hp["ridge"]}, "tuning_grid": grid,
        "baseline_desc": "rolling 365-day empirical frequencies from prior games only (home-win rate, regulation "
                         "outcome rates, puck-line cover rates, over rates, empirical final-total distribution)",
        "markets": markets, "by_season": by_season,
        "goalie_sensitivity": {"projected_ll": float(_ll(df.p_home, df.y_home).mean()),
                               "actual_ll": float(_ll(df.p_home_actual, df.y_home).mean())},
    }
    mc = market_comparison(df, hist_odds, settings["edges"]["min_ev"], reps)
    if mc:
        out["market"] = mc
    else:
        out["market_note"] = ("No historical sportsbook odds supplied (data/historical_odds.csv). Market benchmarking and "
                              "historical ROI are therefore not computed; live closing-line tracking builds this going forward.")
    return out


def tier_from_backtest(bt):
    """Per-market backtest verdict used by the validation tiers."""
    if not bt or not bt.get("available"):
        return {}
    mk = bt["markets"]
    ok = lambda k: bool(mk.get(k, {}).get("ci") and mk[k]["ci"][0] > 0)
    return {"h2h": ok("ml"), "spreads": ok("pl_home") and ok("pl_away"), "totals": ok("total") and ok("o55")}
