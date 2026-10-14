"""Seeded Monte Carlo of a game under exactly the same generative assumptions as probability.py.

Used by the Simulation Center and as an independent cross-check of the analytic engine (tests assert the
two agree within Monte-Carlo error). Reproducible: same seed -> identical results.
"""
import numpy as np

from .probability import tie_home_prob


def simulate_game(lh, la, aux, n=10000, seed=0, playoff=False, beta_draw_lambdas=None):
    """Return dict of per-simulation arrays: home, away (final goals), reg_home, reg_away, ending (0 REG,1 OT,2 SO).

    beta_draw_lambdas: optional (lh_array, la_array) of per-simulation rates (parameter uncertainty).
    """
    rng = np.random.default_rng(seed)
    if beta_draw_lambdas is not None:
        lh_s = np.resize(beta_draw_lambdas[0], n)
        la_s = np.resize(beta_draw_lambdas[1], n)
    else:
        lh_s = np.full(n, float(lh))
        la_s = np.full(n, float(la))
    phi = aux["phi"]
    g = rng.gamma(1.0 / phi, phi, n) if phi > 1e-9 else np.ones(n)
    h = rng.poisson(lh_s * g)
    a = rng.poisson(la_s * g)
    # empty-net: a 1-goal lead becomes 2 with probability theta
    en = rng.random(n) < aux["theta"]
    h = np.where(en & (h - a == 1), h + 1, h)
    a = np.where(en & (a - h == 1), a + 1, a)
    reg_h, reg_a = h.copy(), a.copy()
    tie = h == a
    q_tie, q_ot, p_so = tie_home_prob(lh_s, la_s, aux, playoff)
    so = tie & (rng.random(n) < p_so)
    q = np.where(so, aux["q_so"], q_ot)
    home_wins_tie = rng.random(n) < q
    h = np.where(tie & home_wins_tie, h + 1, h)
    a = np.where(tie & ~home_wins_tie, a + 1, a)
    ending = np.where(~tie, 0, np.where(so, 2, 1))
    return {"home": h, "away": a, "reg_home": reg_h, "reg_away": reg_a, "ending": ending}


def summarize(sim, lines=(4.5, 5.5, 6.5, 7.5)):
    h, a = sim["home"], sim["away"]
    n = len(h)
    tot = h + a
    marg = h - a
    se = lambda p: float(np.sqrt(p * (1 - p) / n))
    p_home = float(np.mean(h > a))
    out = {
        "n": n,
        "p_home_ml": p_home, "p_home_ml_se": se(p_home),
        "p_reg_home": float(np.mean(sim["reg_home"] > sim["reg_away"])),
        "p_reg_tie": float(np.mean(sim["reg_home"] == sim["reg_away"])),
        "p_reg_away": float(np.mean(sim["reg_home"] < sim["reg_away"])),
        "p_ot": float(np.mean(sim["ending"] == 1)), "p_so": float(np.mean(sim["ending"] == 2)),
        "p_home_m15": float(np.mean(marg >= 2)), "p_away_m15": float(np.mean(marg <= -2)),
        "exp_total": float(tot.mean()), "exp_home": float(h.mean()), "exp_away": float(a.mean()),
        "over": {str(L): float(np.mean(tot > L)) for L in lines},
        "total_hist": np.bincount(tot, minlength=15)[:15].tolist(),
        "margin_hist": {int(k): int(v) for k, v in zip(*np.unique(np.clip(marg, -6, 6), return_counts=True))},
    }
    sc, cnt = np.unique(np.stack([h, a], 1), axis=0, return_counts=True)
    top = np.argsort(-cnt)[:10]
    out["top_scorelines"] = [{"home": int(sc[i, 0]), "away": int(sc[i, 1]), "p": float(cnt[i] / n)} for i in top]
    return out
