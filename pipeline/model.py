"""Team scoring model: time-decayed, ridge-penalised Poisson regression (fit by Newton/IRLS).

log E[regulation goals for team X vs Y] =
      mu + home*(not neutral) + att[X] - def[Y]
      + b2b_off * X_on_back_to_back + b2b_def * Y_on_back_to_back
      + gk * goalie_adj(Y's starter)

* Recency weighting: weight = 0.5 ** (age_days / half_life)   (no over-reaction to hot streaks)
* Ridge penalty on att/def = shrinkage toward league average (sample-size adjustment)
* Laplace approximation (inverse Hessian) gives a parameter covariance -> posterior draws -> uncertainty
* The same machinery fits a shots-on-goal model (no goalie term) for projected shots.

Auxiliary game-flow parameters (phi, theta, OT/SO behaviour) are estimated from the training window only.
"""
import numpy as np

from .goalies import ordinal
from .probability import DEFAULT_AUX, apply_empty_net, score_matrices


def regulation_goals(games):
    hg = games["home_goals"].to_numpy(float)
    ag = games["away_goals"].to_numpy(float)
    ot = games["last_period"].isin(["OT", "SO"]).to_numpy()
    m = np.minimum(hg, ag)
    return np.where(ot, m, hg), np.where(ot, m, ag), ot


class Design:
    """Pre-built design matrix: rows 2g (home scoring) and 2g+1 (away scoring)."""

    def __init__(self, teams):
        self.teams = sorted(teams)
        self.idx = {t: i for i, t in enumerate(self.teams)}
        T = len(self.teams)
        self.T = T
        self.i_mu, self.i_home = 0, 1
        self.i_att, self.i_def = 2, 2 + T
        self.i_b2b_off, self.i_b2b_def, self.i_gk = 2 + 2 * T, 3 + 2 * T, 4 + 2 * T
        self.P = 5 + 2 * T

    def rows(self, home, away, neutral, b2b_h, b2b_a, gadj_h, gadj_a):
        """Build design rows for n games (2n rows). gadj_h = HOME goalie adj (affects away scoring)."""
        n = len(home)
        X = np.zeros((2 * n, self.P))
        hi = np.array([self.idx[t] for t in home])
        ai = np.array([self.idx[t] for t in away])
        r = np.arange(n)
        for k, (att, dfn, is_home, own_b2b, opp_b2b, opp_g) in enumerate(
                ((hi, ai, 1 - np.asarray(neutral, float), b2b_h, b2b_a, gadj_a),
                 (ai, hi, 0.0, b2b_a, b2b_h, gadj_h))):
            rows = 2 * r + k
            X[rows, self.i_mu] = 1.0
            X[rows, self.i_home] = is_home
            X[rows, self.i_att + att] = 1.0
            X[rows, self.i_def + dfn] = -1.0
            X[rows, self.i_b2b_off] = own_b2b
            X[rows, self.i_b2b_def] = opp_b2b
            X[rows, self.i_gk] = opp_g
        return X

    def penalty(self, ridge):
        p = np.full(self.P, 1e-6)
        p[self.i_att:self.i_att + 2 * self.T] = ridge
        p[self.i_b2b_off] = p[self.i_b2b_def] = 2.0
        p[self.i_gk] = 2.0
        return p


def fit_poisson(X, y, w, penalty, beta0=None, max_iter=60, tol=1e-8):
    b = np.zeros(X.shape[1]) if beta0 is None else beta0.copy()
    if beta0 is None:
        b[0] = np.log(max(np.average(y, weights=w), 1e-3))
    H = None
    for _ in range(max_iter):
        eta = np.clip(X @ b, -10, 5)
        mu = np.exp(eta)
        grad = X.T @ (w * (y - mu)) - penalty * b
        H = (X * (w * mu)[:, None]).T @ X + np.diag(penalty)
        step = np.linalg.solve(H, grad)
        b += step
        if np.max(np.abs(step)) < tol:
            break
    cov = np.linalg.inv(H)
    return b, cov


def fit_aux(lh, la, reg_h, reg_a, games_win, aux_prior=None):
    """Estimate phi (pace overdispersion), theta (empty-net), and OT/shootout behaviour from training data."""
    aux = dict(DEFAULT_AUX if aux_prior is None else aux_prior)
    K = 16
    ih = np.minimum(reg_h.astype(int), K)
    ia = np.minimum(reg_a.astype(int), K)
    best = (-np.inf, None, None)
    for phi in (0.0, 0.01, 0.02, 0.04, 0.06, 0.09, 0.12):
        M = score_matrices(lh, la, phi)
        for theta in (0.0, 0.04, 0.08, 0.12, 0.16, 0.2, 0.25, 0.3):
            R = apply_empty_net(M, theta)
            ll = np.log(np.maximum(R[np.arange(len(lh)), ih, ia], 1e-12)).sum()
            if ll > best[0]:
                best = (ll, phi, theta)
    aux["phi"], aux["theta"] = best[1], best[2]
    last = games_win["last_period"].to_numpy()
    reg_season = games_win["game_type"].to_numpy() == 2
    home_won = games_win["home_goals"].to_numpy() > games_win["away_goals"].to_numpy()
    tied = np.isin(last, ["OT", "SO"]) & reg_season
    so = (last == "SO") & reg_season
    aux["p_so"] = float((so.sum() + 40 * 0.4) / (tied.sum() + 40))
    aux["q_so"] = float((home_won[so].sum() + 25) / (so.sum() + 50))
    otg = last == "OT"
    aux["ot_base"] = float((home_won[otg].sum() + 25) / (otg.sum() + 50))
    if otg.sum() >= 30:
        d = ((lh - la) / (lh + la))[otg]
        y = home_won[otg]
        bestk = (-np.inf, 0.5)
        for k in np.arange(0, 2.01, 0.25):
            p = np.clip(aux["ot_base"] + k * d, 0.05, 0.95)
            ll = np.sum(y * np.log(p) + (1 - y) * np.log(1 - p))
            if ll > bestk[0]:
                bestk = (ll, float(k))
        aux["kappa"] = bestk[1]
    return aux


class GameModel:
    """Holds a fitted goals model + shots model + aux params as of a date."""

    def __init__(self, design, settings):
        self.d = design
        self.hl = settings.get("half_life_days", 120)
        self.ridge = settings.get("ridge", 15)
        self.max_hist = settings.get("max_history_days", 900)
        self.beta = self.cov = self.beta_shots = None
        self.aux = dict(DEFAULT_AUX)

    def weights(self, date_ord, asof_ord):
        age = asof_ord - date_ord
        w = 0.5 ** (age / self.hl)
        w[(age <= 0) | (age > self.max_hist)] = 0.0
        return w

    def fit(self, X, y_goals, y_shots, date_ord, asof_ord, warm=None, fit_shots=True):
        w = np.repeat(self.weights(date_ord, asof_ord), 2)
        keep = w > 0
        if keep.sum() < 200:
            raise ValueError("not enough training games")
        pen = self.d.penalty(self.ridge)
        self.beta, self.cov = fit_poisson(X[keep], y_goals[keep], w[keep], pen,
                                          None if warm is None else warm.beta)
        if fit_shots and y_shots is not None:
            Xs = X[keep].copy()
            Xs[:, self.d.i_gk] = 0.0
            ok = y_shots[keep] > 0
            self.beta_shots, _ = fit_poisson(Xs[ok], y_shots[keep][ok], w[keep][ok], pen,
                                             None if (warm is None or warm.beta_shots is None) else warm.beta_shots)
        return keep

    def lambdas(self, Xrows, beta=None):
        b = self.beta if beta is None else beta
        eta = np.clip(Xrows @ b, -10, 5)
        mu = np.exp(eta)
        return mu[0::2], mu[1::2]

    def shots(self, Xrows):
        if self.beta_shots is None:
            return None, None
        Xs = Xrows.copy()
        Xs[:, self.d.i_gk] = 0.0
        mu = np.exp(np.clip(Xs @ self.beta_shots, -10, 6))
        return mu[0::2], mu[1::2]

    def draws(self, n, seed):
        rng = np.random.default_rng(seed)
        cov = (self.cov + self.cov.T) / 2
        return rng.multivariate_normal(self.beta, cov, size=n, method="cholesky")

    def team_ratings(self):
        T = self.d.T
        sd = np.sqrt(np.diag(self.cov))
        out = []
        for t, i in self.d.idx.items():
            out.append({"team": t,
                        "att": float(self.beta[self.d.i_att + i]), "att_sd": float(sd[self.d.i_att + i]),
                        "def": float(self.beta[self.d.i_def + i]), "def_sd": float(sd[self.d.i_def + i]),
                        "shots_att": float(self.beta_shots[self.d.i_att + i]) if self.beta_shots is not None else None,
                        "shots_def": float(self.beta_shots[self.d.i_def + i]) if self.beta_shots is not None else None})
        return out

    def export(self):
        d = self.d
        return {"teams": d.teams, "mu": float(self.beta[d.i_mu]), "home": float(self.beta[d.i_home]),
                "att": self.beta[d.i_att:d.i_att + d.T].tolist(), "def": self.beta[d.i_def:d.i_def + d.T].tolist(),
                "b2b_off": float(self.beta[d.i_b2b_off]), "b2b_def": float(self.beta[d.i_b2b_def]),
                "goalie_coef": float(self.beta[d.i_gk]), "aux": self.aux,
                "half_life_days": self.hl, "ridge": self.ridge}


def prepare(games, goalie_feats, b2b_h, b2b_a, design, use_projected=True):
    """Full design matrix + targets for all games."""
    gh = goalie_feats["gadj_home_proj" if use_projected else "gadj_home_actual"]
    ga = goalie_feats["gadj_away_proj" if use_projected else "gadj_away_actual"]
    X = design.rows(games["home"].tolist(), games["away"].tolist(), games["neutral"].to_numpy(float),
                    b2b_h, b2b_a, gh, ga)
    reg_h, reg_a, _ = regulation_goals(games)
    y = np.empty(2 * len(games))
    y[0::2], y[1::2] = reg_h, reg_a
    ys = np.empty(2 * len(games))
    ys[0::2], ys[1::2] = games["home_sog"].to_numpy(float), games["away_sog"].to_numpy(float)
    dord = np.array([ordinal(d) for d in games["date"].astype(str)])
    return X, y, ys, dord, reg_h, reg_a
