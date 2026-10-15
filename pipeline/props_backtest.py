"""Out-of-sample validation of the player prop models.

Coefficients are fit on the training seasons only; every feature is pre-game (shifted), so test-season
predictions use only information available before each game. Each prop line is scored with log loss
against the bettor's usual heuristic -- the player's hit rate over his last 10 games (Laplace-smoothed) --
and against the last-20 hit rate. Ablations refit the model without the recent-form windows and without
H2H to measure whether those inputs add out-of-sample accuracy. 95% CIs: date-block bootstrap.
"""
import time

import numpy as np
import pandas as pd

from . import props as P
from .backtest import _boot_ci, _calibration, _ll
from .goalies import sequential_goalie_features


def gk_map_from(games, feats):
    gk = {}
    for i, r in enumerate(games[["game_id", "home", "away"]].itertuples(index=False)):
        gk[(int(r.game_id), r.home)] = float(feats["gadj_away_proj"][i])
        gk[(int(r.game_id), r.away)] = float(feats["gadj_home_proj"][i])
    return gk


def hit_counts(d, stat, line, n):
    codes = d["player_id"].to_numpy() if "player_id" in d else d["goalie_id"].to_numpy()
    col = stat if stat in d else "saves"
    prev = P.grp_shift(d[col], codes)
    ind = np.where(np.isnan(prev), np.nan, (prev > line).astype(float))
    return P.roll_sum(ind, codes, n)


def score_line(y_over, p_model, base_hits, base_n, dates, reps):
    p_base = (base_hits + 1) / (base_n + 2)
    lm, lb = _ll(p_model, y_over), _ll(p_base, y_over)
    return {"model_ll": float(lm.mean()), "base_ll": float(lb.mean()), "improvement": float((lb - lm).mean()),
            "ci": _boot_ci(lb - lm, dates, reps), "brier_model": float(((p_model - y_over) ** 2).mean()),
            "brier_base": float(((p_base - y_over) ** 2).mean()), "hit_rate": float(y_over.mean()),
            "n": int(len(y_over)), "calibration": _calibration(p_model, y_over)}


def run_props_backtest(store, settings, log=print):
    t0 = time.time()
    reps = min(settings["backtest"]["bootstrap_reps"], 400)
    games, gg = store.games(), store.goalie_games()
    sk = P.load_skaters(store)
    seasons = sorted(int(s) for s in games.season.unique())
    nb, nt = settings["backtest"]["burn_in_seasons"], settings["backtest"]["tuning_seasons"]
    if len(seasons) < nb + nt + 1 or not len(sk):
        return {"available": False, "reason": "Not enough seasons of player data."}
    first_test = seasons[nb + nt]
    feats, *_ = sequential_goalie_features(games.sort_values(["date", "game_id"]).reset_index(drop=True), gg)
    gsorted = games.sort_values(["date", "game_id"]).reset_index(drop=True)
    gk = gk_map_from(gsorted, feats)
    teams = P.team_table(games)
    pm = P.prior_means(sk[sk.season < first_test])
    d = P.skater_features(sk, teams, gk, pm)
    log(f"  props features built ({len(d)} rows, {time.time() - t0:.0f}s)")
    elig = P.eligible(d).to_numpy()
    train = elig & (d.season < first_test).to_numpy()
    test = elig & (d.season >= first_test).to_numpy()
    dtr, dte = d[train], d[test]
    dates = dte["date"].to_numpy()
    out = {"available": True, "train_period": f"{str(seasons[0])[:4]}–{str(first_test)[:4]}",
           "test_period": f"{str(first_test)[:4]}–{str(seasons[-1])[4:]}", "n_train": int(train.sum()),
           "n_test": int(test.sum()), "baseline_desc": "player's hit rate over his previous 10 games (Laplace-smoothed: (hits+1)/(n+2))",
           "eligibility": "skaters with >=10 prior games and >=10 min average ice time", "stats": {}}
    for stat in P.STATS:
        m = P.StatModel(stat).fit(dtr)
        mu = m.mu(dte)
        y = dte[stat].to_numpy(float)
        kmax = P.PMF_MAX[stat]
        pmf = P.nb_pmf_matrix(mu, m.alpha, kmax)
        res = {"coefs": m.coef_table(), "alpha": m.alpha, "intercept": float(m.beta[0]),
               "mae": float(np.abs(mu - y).mean()), "mean_actual": float(y.mean()), "mean_pred": float(mu.mean()),
               "lines": {}}
        logscore = -P.nb_logpmf(y, mu, m.alpha)
        for line in P.LINES[stat]:
            po, _ = P.p_over(pmf, line)
            hits, n = hit_counts(d, stat, line, 10)
            hits20, n20 = hit_counts(d, stat, line, 20)
            r = score_line((y > line).astype(float), po, hits[test], n[test], dates, reps)
            p20 = (hits20[test] + 1) / (n20[test] + 2)
            r["base20_ll"] = float(_ll(p20, (y > line).astype(float)).mean())
            res["lines"][str(line)] = r
        abl = {}
        for name, drop in (("no_recent_form", ("d5", "d10", "d20", "dseason")), ("no_h2h", ("h2h",)),
                           ("no_opponent", ("opp_sa", "opp_ga", "gk"))):
            ma = P.StatModel(stat, drop).fit(dtr)
            ls_a = -P.nb_logpmf(y, ma.mu(dte), ma.alpha)
            diff = ls_a - logscore   # >0 means the full model is better (lower log score)
            abl[name] = {"delta_logscore": float(diff.mean()), "ci": _boot_ci(diff, dates, reps)}
        res["ablations"] = abl
        by = []
        line0 = {"sog": 2.5, "goals": 0.5, "assists": 0.5, "points": 0.5}[stat]
        po0, _ = P.p_over(pmf, line0)
        hits, n = hit_counts(d, stat, line0, 10)
        pb = (hits[test] + 1) / (n[test] + 2)
        yo = (y > line0).astype(float)
        for s in sorted(dte.season.unique()):
            mk = (dte.season == s).to_numpy()
            diff = _ll(pb[mk], yo[mk]) - _ll(po0[mk], yo[mk])
            by.append({"season": f"{str(s)[:4]}-{str(s)[6:]}", "n": int(mk.sum()), "improvement": float(diff.mean()),
                       "ci": _boot_ci(diff, dates[mk], reps)})
        res["by_season_line"] = line0
        res["by_season"] = by
        out["stats"][stat] = res
        log(f"  props {stat}: done ({time.time() - t0:.0f}s)")

    # ---------------- goalie saves ----------------
    gf = P.goalie_features(gg, teams).reset_index(drop=True)
    codes = gf.goalie_id.to_numpy()
    sprev = P.grp_shift(gf.saves, codes)
    SL = (22.5, 24.5, 26.5, 28.5, 30.5)
    for line in SL:
        ind = np.where(np.isnan(sprev), np.nan, (sprev > line).astype(float))
        gf[f"h{line}"], gf[f"n{line}"] = P.roll_sum(ind, codes, 10)
    gf = gf[gf.n_prior >= 5]
    gtr, gte = gf[gf.season < first_test], gf[gf.season >= first_test]
    sm = P.SavesModel().fit(gtr)
    mu_s = np.asarray(sm.shots_mu(gte))
    pmf = sm.pmf(mu_s, gte.sv_hat.to_numpy())
    y = gte.saves.to_numpy(float)
    sres = {"model": sm.export(), "mae": float(np.abs((pmf * np.arange(pmf.shape[1])).sum(1) - y).mean()),
            "lines": {}, "n": int(len(gte))}
    gdates = gte.date.astype(str).to_numpy()
    for line in SL:
        po, _ = P.p_over(pmf, line)
        sres["lines"][str(line)] = score_line((y > line).astype(float), po, gte[f"h{line}"].to_numpy(),
                                              gte[f"n{line}"].to_numpy(), gdates, reps)
    out["saves"] = sres
    out["runtime"] = f"{time.time() - t0:.0f}s"
    out["generated_at"] = pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    return out


def tiers_from(bt):
    """Per prop stat: BEATS_BASELINE when the model beats the last-10 hit-rate baseline (CI > 0) on its main line."""
    out = {}
    if not bt or not bt.get("available"):
        return out
    main = {"sog": "2.5", "goals": "0.5", "assists": "0.5", "points": "0.5"}
    for s, ln in main.items():
        r = bt["stats"].get(s, {}).get("lines", {}).get(ln)
        out[s] = bool(r and r.get("ci") and r["ci"][0] > 0)
    r = bt.get("saves", {}).get("lines", {}).get("26.5")
    out["saves"] = bool(r and r.get("ci") and r["ci"][0] > 0)
    return out
