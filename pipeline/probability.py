"""Score-distribution engine.

Regulation goals: Poisson for each team with a SHARED gamma "game pace" multiplier g ~ Gamma(1/phi, phi)
(mean 1). phi > 0 adds overdispersion and positive correlation between the two teams' scoring.
The mixture is integrated exactly with generalized Gauss-Laguerre quadrature.

Empty-net adjustment: a fraction theta of 1-goal regulation results become 2-goal results (leader scores
into the empty net). theta is estimated from data, not assumed.

Regulation ties go to overtime / shootout (regular season) or sudden-death OT (playoffs):
  P(home wins | tie) = (1 - p_so) * q_ot + p_so * q_so,  q_ot = clip(ot_base + kappa * (lh-la)/(lh+la))
The winner gets +1 goal in the final score (NHL scoring rule for OT and shootout wins), which is how
sportsbook full-game totals and puck lines settle.
"""
import numpy as np
from scipy.special import gammaln, roots_genlaguerre

MAXG = 15  # goals per team in regulation tracked 0..MAXG (tail folded into the last bin)
_I, _J = np.meshgrid(np.arange(MAXG + 2), np.arange(MAXG + 2), indexing="ij")
_NODE_CACHE = {}


def gamma_nodes(phi, n=24):
    key = (round(float(phi), 6), n)
    if key in _NODE_CACHE:
        return _NODE_CACHE[key]
    if phi <= 1e-9:
        out = (np.array([1.0]), np.array([1.0]))
    else:
        k = 1.0 / phi
        x, w = roots_genlaguerre(n, k - 1.0)
        out = (x / k, w / w.sum())
    _NODE_CACHE[key] = out
    return out


def pois_pmf(lam, maxg=MAXG):
    lam = np.asarray(lam, dtype=float)[..., None]
    k = np.arange(maxg + 1)
    p = np.exp(k * np.log(np.maximum(lam, 1e-12)) - lam - gammaln(k + 1))
    p[..., -1] += np.clip(1.0 - p.sum(-1), 0, None)
    return p


def score_matrices(lh, la, phi):
    """Joint regulation score pmf, shape (G, MAXG+1, MAXG+1); [g, i, j] = P(home=i, away=j)."""
    lh = np.atleast_1d(np.asarray(lh, float))
    la = np.atleast_1d(np.asarray(la, float))
    g, w = gamma_nodes(phi)
    ph = pois_pmf(lh[:, None] * g[None, :])
    pa = pois_pmf(la[:, None] * g[None, :])
    return np.einsum("gnk,gnj,n->gkj", ph, pa, w)


def apply_empty_net(M, theta):
    """Move theta of each 1-goal-margin cell to the matching 2-goal-margin cell. M: (G,K,K) -> (G,K+1,K+1)."""
    G, K, _ = M.shape
    out = np.zeros((G, K + 1, K + 1))
    out[:, :K, :K] = M
    if theta <= 0:
        return out
    for i in range(K - 1):
        mv = theta * M[:, i + 1, i]        # home leads by 1
        out[:, i + 1, i] -= mv
        out[:, i + 2, i] += mv
        mv = theta * M[:, i, i + 1]        # away leads by 1
        out[:, i, i + 1] -= mv
        out[:, i, i + 2] += mv
    return out


def tie_home_prob(lh, la, aux, playoff=False):
    d = (lh - la) / (lh + la)
    q_ot = np.clip(aux["ot_base"] + aux["kappa"] * d, 0.05, 0.95)
    p_so = 0.0 if playoff else aux["p_so"]
    return (1 - p_so) * q_ot + p_so * aux["q_so"], q_ot, p_so


class GameDistribution:
    """All market probabilities for one game, derived from the regulation score matrix."""

    def __init__(self, R, lh, la, aux, playoff=False):
        self.R = R  # (K, K) regulation pmf after empty-net adjustment
        self.lh, self.la = float(lh), float(la)
        K = R.shape[0]
        I, J = _I[:K, :K], _J[:K, :K]
        diff = I - J
        self.p_tie = float(np.trace(R))
        self.p_reg_home = float(R[diff > 0].sum())
        self.p_reg_away = float(R[diff < 0].sum())
        q_tie, q_ot, p_so = tie_home_prob(self.lh, self.la, aux, playoff)
        self.q_tie, self.q_ot, self.p_so_given_tie = float(q_tie), float(q_ot), float(p_so)
        self.p_ml_home = self.p_reg_home + self.p_tie * self.q_tie
        self.p_ml_away = 1.0 - self.p_ml_home
        self.p_ot = self.p_tie * (1 - p_so)
        self.p_so = self.p_tie * p_so
        # final margin (home - away), index offset
        self.moff = K
        margin = np.zeros(2 * K + 1)
        np.add.at(margin, (diff + K)[diff != 0], R[diff != 0])
        margin[K + 1] += self.p_tie * q_tie
        margin[K - 1] += self.p_tie * (1 - q_tie)
        self.margin = margin
        total = np.zeros(2 * K + 2)
        tot = I + J
        np.add.at(total, tot[diff != 0], R[diff != 0])
        diag = np.diag(R)
        np.add.at(total, 2 * np.arange(K) + 1, diag)
        self.total = total
        hg = np.zeros(K + 1)
        ag = np.zeros(K + 1)
        np.add.at(hg, I[diff != 0], R[diff != 0])
        np.add.at(ag, J[diff != 0], R[diff != 0])
        idx = np.arange(K)
        np.add.at(hg, idx + 1, diag * q_tie)
        np.add.at(hg, idx, diag * (1 - q_tie))
        np.add.at(ag, idx + 1, diag * (1 - q_tie))
        np.add.at(ag, idx, diag * q_tie)
        self.home_goals, self.away_goals = hg, ag

    # ---- markets: return (p_win, p_push) ------------------------------------------------
    def spread(self, side, point):
        """Full-game spread. side 'home'/'away', point e.g. -1.5 means side must win by 2+."""
        m = np.arange(-self.moff, self.moff + 1)
        mm = m if side == "home" else -m
        adj = mm + point
        return float(self.margin[adj > 1e-9].sum()), float(self.margin[np.abs(adj) < 1e-9].sum())

    def total_market(self, side, line):
        t = np.arange(len(self.total))
        over, push = float(self.total[t > line + 1e-9].sum()), float(self.total[np.abs(t - line) < 1e-9].sum())
        return (over, push) if side == "over" else (1.0 - over - push, push)

    def team_total(self, team, side, line):
        dist = self.home_goals if team == "home" else self.away_goals
        t = np.arange(len(dist))
        over, push = float(dist[t > line + 1e-9].sum()), float(dist[np.abs(t - line) < 1e-9].sum())
        return (over, push) if side == "over" else (1.0 - over - push, push)

    def moneyline(self, side):
        return (self.p_ml_home if side == "home" else self.p_ml_away), 0.0

    def market(self, market, side, point=None):
        if market == "h2h":
            return self.moneyline(side)
        if market == "spreads":
            return self.spread(side, float(point))
        if market == "totals":
            return self.total_market(side, float(point))
        raise KeyError(market)

    def expected_total(self):
        return float((np.arange(len(self.total)) * self.total).sum())

    def expected_margin(self):
        return float((np.arange(-self.moff, self.moff + 1) * self.margin).sum())

    def top_scorelines(self, n=10):
        K = self.R.shape[0]
        out = {}
        for i in range(K):
            for j in range(K):
                p = self.R[i, j]
                if p < 1e-6:
                    continue
                if i == j:
                    out[(i + 1, j)] = out.get((i + 1, j), 0) + p * self.q_tie
                    out[(i, j + 1)] = out.get((i, j + 1), 0) + p * (1 - self.q_tie)
                else:
                    out[(i, j)] = out.get((i, j), 0) + p
        top = sorted(out.items(), key=lambda kv: -kv[1])[:n]
        return [{"home": h, "away": a, "p": round(p, 5)} for (h, a), p in top]


def distributions(lh, la, aux, playoff=None):
    """Vectorised constructor: returns a list of GameDistribution."""
    lh = np.atleast_1d(np.asarray(lh, float))
    la = np.atleast_1d(np.asarray(la, float))
    M = apply_empty_net(score_matrices(lh, la, aux["phi"]), aux["theta"])
    po = np.zeros(len(lh), bool) if playoff is None else np.atleast_1d(playoff)
    return [GameDistribution(M[g], lh[g], la[g], aux, bool(po[g])) for g in range(len(lh))]


DEFAULT_AUX = {"phi": 0.0, "theta": 0.0, "p_so": 0.4, "q_so": 0.5, "ot_base": 0.5, "kappa": 0.5}
