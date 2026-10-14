"""Glue: build history features once, fit the model as of any date, predict any slate (with uncertainty)."""
import numpy as np

from .goalies import b2b_flags, ordinal, project_starter, rest_days, sequential_goalie_features
from .model import Design, GameModel, fit_aux, prepare
from .odds_math import fair_american_with_push
from .probability import distributions

STD_TOTALS = (4.5, 5.0, 5.5, 6.0, 6.5, 7.0, 7.5)
STD_SPREADS = (-1.5, 1.5)


def pkey(market, side, point):
    if point is None or (isinstance(point, float) and point != point):
        ps = ""
    else:
        p = float(point)
        ps = str(int(p)) if p == int(p) else repr(p)
    return f"{market}|{side}|{ps}"


class History:
    def __init__(self, games, goalie_games, ms, extra_teams=()):
        self.games = games.sort_values(["date", "game_id"]).reset_index(drop=True)
        self.ms = ms
        teams = set(self.games["home"]) | set(self.games["away"]) | set(extra_teams)
        self.design = Design(teams)
        (self.feats, self.tracker, self.hist, self.last_game) = sequential_goalie_features(
            self.games, goalie_games, ms.get("goalie_prior_shots", 1000), ms.get("goalie_half_life_days", 365))
        self.b2b_h, self.b2b_a, self.played = b2b_flags(self.games)
        (self.X, self.y, self.ys, self.dord, self.reg_h, self.reg_a) = prepare(
            self.games, self.feats, self.b2b_h, self.b2b_a, self.design, use_projected=False)
        self.X_proj = self.design.rows(self.games["home"].tolist(), self.games["away"].tolist(),
                                       self.games["neutral"].to_numpy(float), self.b2b_h, self.b2b_a,
                                       self.feats["gadj_home_proj"], self.feats["gadj_away_proj"])
        self.playoff = (self.games["game_type"].to_numpy() == 3)

    def fit(self, asof_ord, hp=None, warm=None, with_aux=True, aux_prior=None):
        hp = hp or self.ms
        m = GameModel(self.design, hp)
        keep_rows = m.fit(self.X, self.y, self.ys, self.dord, asof_ord, warm=warm)
        if with_aux:
            gmask = keep_rows[0::2]
            lh, la = m.lambdas(self.X[np.repeat(gmask, 2)])
            m.aux = fit_aux(lh, la, self.reg_h[gmask], self.reg_a[gmask], self.games[gmask], aux_prior)
        elif aux_prior is not None:
            m.aux = dict(aux_prior)
        return m


def goalie_info(hist, gid, d_ord):
    if gid is None:
        return None
    sv, adj, shots = hist.tracker.rating(gid, d_ord)
    return {"id": int(gid), "name": hist.tracker.names.get(gid, str(gid)), "sv_rating": sv, "adj": adj,
            "decayed_shots": shots}


def slate_features(hist, slate, today_ord, overrides=None):
    """Per game: b2b, rest, projected/confirmed goalies and alternates. overrides: {TEAM: {...}}."""
    overrides = overrides or {}
    out = []
    for g in slate:
        rec = {"game": g}
        for side in ("home", "away"):
            team = g[side]
            lg = hist.last_game.get(team)
            yest = lg[1] if lg and lg[0] == today_ord - 1 else None
            proj, alt = project_starter(list(hist.hist.get(team, [])), yest)
            ov = overrides.get(team)
            if ov and ov.get("goalie_id"):
                gid, status, src = int(ov["goalie_id"]), ov.get("status", "PROJECTED"), ov.get("source", "manual override")
                if status == "CONFIRMED" and not ov.get("source"):
                    status = "PROJECTED"  # never CONFIRMED without a named source
                if alt == gid or alt is None:
                    alt = proj if proj != gid else alt
            elif proj is not None:
                gid, status, src = proj, "PROJECTED", "usage model: most starts in last 10 games, rest-adjusted"
            else:
                gid, status, src = None, "UNKNOWN", "no starter history for this team"
            rec[side] = {"team": team, "b2b": float((team, today_ord - 1) in hist.played),
                         "rest_days": rest_days(team, today_ord, hist.played),
                         "goalie": goalie_info(hist, gid, today_ord), "status": status, "source": src,
                         "alt": goalie_info(hist, alt, today_ord) if alt and alt != gid else None}
        out.append(rec)
    return out


def rows_for(hist, feats, swap=None):
    """Design rows for slate; swap=('home'|'away') uses that side's alternate goalie."""
    home = [f["home"]["team"] for f in feats]
    away = [f["away"]["team"] for f in feats]
    neutral = np.array([f["game"].get("neutral", 0) for f in feats], float)

    def adj(f, side):
        gi = f[side]["alt"] if swap == side and f[side]["alt"] else f[side]["goalie"]
        return gi["adj"] if gi else 0.0
    return hist.design.rows(home, away, neutral,
                            np.array([f["home"]["b2b"] for f in feats]), np.array([f["away"]["b2b"] for f in feats]),
                            np.array([adj(f, "home") for f in feats]), np.array([adj(f, "away") for f in feats]))


class Prediction:
    """Posterior-predictive market probabilities for one game (list of per-draw GameDistributions)."""

    def __init__(self, dists, point_dist, shots):
        self.dists, self.point, self.shots = dists, point_dist, shots

    def market(self, market, side, point=None):
        arr = np.array([d.market(market, side, point) for d in self.dists])
        p, push = arr[:, 0], arr[:, 1]
        return {"p": float(p.mean()), "push": float(push.mean()), "p_draws": p, "push_draws": push,
                "p_lo": float(np.quantile(p, 0.1)), "p_hi": float(np.quantile(p, 0.9))}

    def summary(self, extra_keys=()):
        D = self.dists
        mean = lambda f: float(np.mean([f(d) for d in D]))
        q = lambda f: [float(x) for x in np.quantile([f(d) for d in D], [0.1, 0.9])]
        K = len(D[0].margin)
        margin = np.mean([d.margin for d in D], axis=0)
        total = np.mean([d.total for d in D], axis=0)
        off = D[0].moff
        md = {str(k): float(margin[k + off]) for k in range(-6, 7)}
        md[str(-6)] = float(margin[:off - 5].sum())
        md[str(6)] = float(margin[off + 6:].sum())
        s = {
            "p_home_ml": mean(lambda d: d.p_ml_home), "p_away_ml": mean(lambda d: d.p_ml_away),
            "p_reg_home": mean(lambda d: d.p_reg_home), "p_reg_tie": mean(lambda d: d.p_tie),
            "p_reg_away": mean(lambda d: d.p_reg_away), "p_ot": mean(lambda d: d.p_ot), "p_so": mean(lambda d: d.p_so),
            "p_home_m15": mean(lambda d: d.spread("home", -1.5)[0]), "p_away_m15": mean(lambda d: d.spread("away", -1.5)[0]),
            "exp_home": mean(lambda d: float((np.arange(len(d.home_goals)) * d.home_goals).sum())),
            "exp_away": mean(lambda d: float((np.arange(len(d.away_goals)) * d.away_goals).sum())),
            "exp_total": mean(lambda d: d.expected_total()), "exp_margin": mean(lambda d: d.expected_margin()),
            "lambda_home": mean(lambda d: d.lh), "lambda_away": mean(lambda d: d.la),
            "shots_home": self.shots[0], "shots_away": self.shots[1],
            "interval": {"p_home_ml": q(lambda d: d.p_ml_home), "exp_total": q(lambda d: d.expected_total())},
            "margin_dist": md, "total_dist": [float(x) for x in total[:16]],
            "top_scorelines": self.point.top_scorelines(10),
        }
        s["totals"] = {}
        for L in (4.5, 5.5, 6.5, 7.5):
            o = self.market("totals", "over", L)
            s["totals"][str(L)] = {"over": o["p"], "under": 1 - o["p"] - o["push"], "push": o["push"]}
        keys = {("h2h", "home", None), ("h2h", "away", None)}
        for p in STD_SPREADS:
            keys |= {("spreads", "home", p), ("spreads", "away", p)}
        for L in STD_TOTALS:
            keys |= {("totals", "over", L), ("totals", "under", L)}
        keys |= set(extra_keys)
        s["markets"] = {}
        for (m, sd, pt) in sorted(keys, key=str):
            r = self.market(m, sd, pt)
            try:
                fair = fair_american_with_push(r["p"], r["push"])
            except ValueError:
                fair = None
            s["markets"][pkey(m, sd, pt)] = {"p": r["p"], "push": r["push"], "p_lo": r["p_lo"], "p_hi": r["p_hi"], "fair": fair}
        return s


def predict(hist, model, feats, n_draws=200, seed=0, swap=None):
    if not feats:
        return []
    X = rows_for(hist, feats, swap)
    playoff = np.array([f["game"].get("game_type", 2) == 3 for f in feats])
    lh0, la0 = model.lambdas(X)
    point = distributions(lh0, la0, model.aux, playoff)
    sh, sa = model.shots(X)
    if n_draws and n_draws > 1:
        B = model.draws(n_draws, seed)
        eta = np.clip(X @ B.T, -10, 5)          # (2G, S)
        mu = np.exp(eta)
        per = [distributions(mu[0::2, s], mu[1::2, s], model.aux, playoff) for s in range(n_draws)]
        dists = [[per[s][g] for s in range(n_draws)] for g in range(len(feats))]
    else:
        dists = [[point[g]] for g in range(len(feats))]
    return [Prediction(dists[g], point[g], (None if sh is None else float(sh[g]), None if sa is None else float(sa[g])))
            for g in range(len(feats))]
