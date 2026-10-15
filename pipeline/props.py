"""Player prop engine: shots on goal, goals, assists, points (skaters) and saves (goalies).

Everything is computed from the stored NHL box scores with strictly PRE-GAME features (every rolling
window is shifted by one game), so the same code produces the historical backtest and tonight's numbers.

Skater model (one Poisson-family regression per stat, fit on training seasons only for the backtest):
  log E[stat] = b0 + b1*log(base rate)                 base = recency-weighted (EWM) per-game rate, shrunk to position mean
              + b2*dev(L5) + b3*dev(L10) + b4*dev(L20)  recent form vs base (each shrunk toward base)
              + b5*dev(season) + b6*log(TOI L5 / TOI EWM) role change
              + b7*log(opponent suppression factor)     opp shots allowed (SOG) or goals allowed (G/A/P)
              + b8*opp goalie rating + b9*log(own team scoring factor) + b10*home + b11*dev(H2H vs this opp)
              + b12*defenceman
  Distribution: negative binomial with dispersion estimated by maximum likelihood (alpha=0 -> Poisson).
The fitted coefficients answer empirically how much L5/L10/L20 and H2H matter; ablations in the backtest
measure whether they improve out-of-sample accuracy.

Goalie saves: shots faced ~ NB(mu), mu from opponent shot generation x own team shot suppression x home;
saves | shots ~ Binomial(shots, shrunk pre-game save %); plus an empirical "pulled early" mixture.
"""
import math
import unicodedata

import numpy as np
import pandas as pd
from scipy.special import gammaln

from .model import fit_poisson

STATS = ["sog", "goals", "assists", "points"]
LINES = {"sog": [0.5, 1.5, 2.5, 3.5, 4.5, 5.5], "goals": [0.5, 1.5], "assists": [0.5, 1.5],
         "points": [0.5, 1.5, 2.5]}
SAVES_LINES = [20.5, 22.5, 24.5, 25.5, 26.5, 27.5, 28.5, 29.5, 30.5, 32.5]
PMF_MAX = {"sog": 14, "goals": 5, "assists": 5, "points": 7}
WINDOWS = [5, 10, 15, 20]
FEATS = {
    "sog": ["base", "d5", "d10", "d20", "dseason", "toi", "opp_sa", "home", "h2h", "is_d"],
    "goals": ["base", "d5", "d10", "d20", "dseason", "toi", "opp_ga", "gk", "team_gf", "home", "h2h", "is_d"],
    "assists": ["base", "d5", "d10", "d20", "dseason", "toi", "opp_ga", "gk", "team_gf", "home", "h2h", "is_d"],
    "points": ["base", "d5", "d10", "d20", "dseason", "toi", "opp_ga", "gk", "team_gf", "home", "h2h", "is_d"],
}
FEAT_LABEL = {"base": "base rate (EWM, shrunk)", "d5": "last 5 vs base", "d10": "last 10 vs base",
              "d20": "last 20 vs base", "dseason": "season vs base", "toi": "recent ice-time change",
              "opp_sa": "opponent shots allowed", "opp_ga": "opponent goals allowed", "gk": "opposing goalie (worse = +)",
              "team_gf": "own team scoring", "home": "home ice", "h2h": "H2H vs this opponent", "is_d": "defenceman"}
EWM_HL = 15        # games
SHRINK_K = 4.0     # pseudo-games toward position mean
TEAM_HL = 20


def norm_name(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return " ".join(s.replace(".", " ").replace("-", " ").replace("'", "").split())


# --------------------------------------------------------------------------------------------- teams
def team_table(games, future=()):
    """One row per team-game with pre-game (shifted) shot/goal factors relative to the league."""
    g = games
    cols = ["game_id", "date", "team", "opp", "is_home", "gf", "ga", "sf", "sa"]
    h = pd.DataFrame({"game_id": g.game_id, "date": g.date, "team": g.home, "opp": g.away, "is_home": 1,
                      "gf": g.home_goals, "ga": g.away_goals, "sf": g.home_sog, "sa": g.away_sog})
    a = pd.DataFrame({"game_id": g.game_id, "date": g.date, "team": g.away, "opp": g.home, "is_home": 0,
                      "gf": g.away_goals, "ga": g.home_goals, "sf": g.away_sog, "sa": g.home_sog})
    fut = []
    for r in future:
        fut.append({"game_id": r["game_id"], "date": r["date"], "team": r["home"], "opp": r["away"], "is_home": 1})
        fut.append({"game_id": r["game_id"], "date": r["date"], "team": r["away"], "opp": r["home"], "is_home": 0})
    t = pd.concat([h, a, pd.DataFrame(fut, columns=cols)], ignore_index=True).astype({"gf": float, "ga": float, "sf": float, "sa": float})
    t["date"] = pd.to_datetime(t["date"])
    t = t.sort_values(["team", "date", "game_id"]).reset_index(drop=True)
    # league averages over the prior 365 days (excluding the current day)
    daily = t.dropna(subset=["sf"]).groupby("date")[["sf", "gf"]].agg(["sum", "count"])
    daily.columns = ["sf_sum", "sf_n", "gf_sum", "gf_n"]
    idx = pd.date_range(t.date.min(), t.date.max(), freq="D")
    daily = daily.reindex(idx, fill_value=0)
    roll = daily.rolling("365D", closed="left").sum()
    lg_sf = (roll.sf_sum / roll.sf_n).bfill().fillna(30.0)
    lg_gf = (roll.gf_sum / roll.gf_n).bfill().fillna(3.0)
    t["lg_sf"] = lg_sf.reindex(t.date).to_numpy()
    t["lg_gf"] = lg_gf.reindex(t.date).to_numpy()
    tcodes = t["team"].to_numpy()
    n_prior = t.groupby("team", sort=False).cumcount().clip(upper=40).astype(float)
    for c, lg in (("sf", "lg_sf"), ("sa", "lg_sf"), ("gf", "lg_gf"), ("ga", "lg_gf")):
        prev = grp_shift(t[c], tcodes)
        ewm = pd.Series(grp_ewm(prev, tcodes, TEAM_HL))
        shr = (ewm.fillna(t[lg]) * n_prior + t[lg] * 5) / (n_prior + 5)
        t[f"{c}_f"] = shr / t[lg]
    t["date"] = t["date"].dt.strftime("%Y-%m-%d")
    return t[["game_id", "team", "opp", "is_home", "date", "lg_sf", "lg_gf", "sf_f", "sa_f", "gf_f", "ga_f"]]


# --------------------------------------------------------------------------------------------- skaters
def _codes(keys_df):
    return keys_df.groupby(list(keys_df.columns), sort=False).ngroup().to_numpy()


def roll_sum(values, codes, n):
    """Rolling sum of the last n values within each group. Arrays must be sorted by group then time.
    NaN counts as 0 for the sum. Returns (sum, count_of_non_nan)."""
    v = np.asarray(values, float)
    ok = ~np.isnan(v)
    vz = np.where(ok, v, 0.0)
    c = np.concatenate([[0.0], np.cumsum(vz)])
    cn = np.concatenate([[0.0], np.cumsum(ok.astype(float))])
    idx = np.arange(len(v))
    start = np.zeros(len(v), dtype=np.int64)
    if len(v):
        brk = np.r_[True, codes[1:] != codes[:-1]]
        first = np.where(brk, idx, 0)
        start = np.maximum.accumulate(first)
    lo = np.maximum(idx + 1 - n, start)
    return c[idx + 1] - c[lo], cn[idx + 1] - cn[lo]


def grp_shift(values, codes):
    v = np.asarray(values, float)
    out = np.empty_like(v)
    out[0:1] = np.nan
    out[1:] = v[:-1]
    if len(v):
        out[np.r_[True, codes[1:] != codes[:-1]]] = np.nan
    return out


def grp_ewm(values, codes, halflife):
    """EWM mean within groups (ignore_na), values already shifted. Implemented via pandas cython path."""
    s = pd.Series(values)
    return s.groupby(codes, sort=False).ewm(halflife=halflife, ignore_na=True).mean().reset_index(level=0, drop=True).sort_index().to_numpy()


def grp_cumsum(values, codes):
    v = np.nan_to_num(np.asarray(values, float))
    c = np.cumsum(v)
    if not len(v):
        return c
    brk = np.r_[True, codes[1:] != codes[:-1]]
    base = np.where(brk, c - v, np.nan)
    base = pd.Series(base).ffill().to_numpy()
    return c - base


def skater_features(sk, teams, gk_map, prior_means):
    """sk: skater game rows (+ future rows with NaN stats). Returns feature frame (sorted by player/date)."""
    d = sk.copy()
    d["is_d"] = d["pos"].astype(str).str.upper().str.startswith("D").astype(float)
    d = d.sort_values(["player_id", "date", "game_id"]).reset_index(drop=True)
    codes = d["player_id"].to_numpy()
    brk = np.r_[True, codes[1:] != codes[:-1]] if len(d) else np.array([], bool)
    d["n_prior"] = d.groupby("player_id", sort=False).cumcount().astype(float)
    d["toi_min"] = d["toi_sec"] / 60.0
    toi_prev = grp_shift(d["toi_min"], codes)
    d["toi_ewm"] = grp_ewm(toi_prev, codes, 10)
    ts, tn = roll_sum(toi_prev, codes, 5)
    d["toi_l5"] = np.where(tn > 0, ts / np.maximum(tn, 1), np.nan)
    w = d["n_prior"].clip(upper=30).to_numpy()
    scodes = _codes(d[["player_id", "season"]])
    # H2H ordering: player, opponent, date
    h_order = np.lexsort((d["game_id"].to_numpy(), d["date"].to_numpy(), d["opp"].astype(str).to_numpy(), codes))
    hcodes = _codes(d.iloc[h_order][["player_id", "opp"]])
    for s in STATS:
        prev = grp_shift(d[s], codes)
        ewm = grp_ewm(prev, codes, EWM_HL)
        prior = np.where(d["is_d"] > 0, prior_means[s]["D"], prior_means[s]["F"])
        shr = (np.nan_to_num(ewm) * w + prior * SHRINK_K) / (w + SHRINK_K)
        d[f"{s}_base"] = np.maximum(shr, 1e-3)
        for n in (5, 10, 15, 20):
            d[f"{s}_l{n}_sum"], d[f"{s}_l{n}_n"] = roll_sum(prev, codes, n)
        sprev = grp_shift(d[s], scodes)
        d[f"{s}_season_sum"] = grp_cumsum(sprev, scodes)
        d[f"{s}_season_n"] = grp_cumsum((~np.isnan(sprev)).astype(float), scodes)
        hv = grp_shift(d[s].to_numpy(float)[h_order], hcodes)
        hs, hn = roll_sum(hv, hcodes, 10)
        hsum, hcnt = np.empty(len(d)), np.empty(len(d))
        hsum[h_order], hcnt[h_order] = hs, hn
        d[f"{s}_h2h_sum"], d[f"{s}_h2h_n"] = hsum, hcnt
    own = teams[["game_id", "team", "gf_f"]].rename(columns={"gf_f": "team_gf_f"})
    opp = teams[["game_id", "team", "sa_f", "ga_f"]].rename(columns={"team": "opp", "sa_f": "opp_sa_f", "ga_f": "opp_ga_f"})
    hm = teams[["game_id", "team", "is_home"]]
    d = d.merge(own, on=["game_id", "team"], how="left").merge(opp, on=["game_id", "opp"], how="left") \
         .merge(hm, on=["game_id", "team"], how="left")
    d["gk"] = [gk_map.get((int(a), b), 0.0) for a, b in zip(d.game_id, d.team)]
    for c in ("team_gf_f", "opp_sa_f", "opp_ga_f"):
        d[c] = d[c].fillna(1.0)
    d["is_home"] = d["is_home"].fillna(0.5)
    return d


def design(d, stat):
    b = d[f"{stat}_base"].to_numpy()
    lb = np.log(b)

    def dev(sum_c, n_c, k):
        return np.log((d[sum_c].to_numpy() + k * b) / (d[n_c].to_numpy() + k)) - lb
    cols = {
        "base": lb,
        "d5": dev(f"{stat}_l5_sum", f"{stat}_l5_n", 5.0),
        "d10": dev(f"{stat}_l10_sum", f"{stat}_l10_n", 5.0),
        "d20": dev(f"{stat}_l20_sum", f"{stat}_l20_n", 5.0),
        "dseason": dev(f"{stat}_season_sum", f"{stat}_season_n", 10.0),
        "toi": np.clip(np.log(d["toi_l5"].fillna(d["toi_ewm"]).to_numpy() / d["toi_ewm"].to_numpy()), -1, 1),
        "opp_sa": np.log(d["opp_sa_f"].to_numpy()), "opp_ga": np.log(d["opp_ga_f"].to_numpy()),
        "gk": d["gk"].to_numpy(), "team_gf": np.log(d["team_gf_f"].to_numpy()),
        "home": d["is_home"].to_numpy() - 0.5,
        "h2h": dev(f"{stat}_h2h_sum", f"{stat}_h2h_n", 8.0), "is_d": d["is_d"].to_numpy(),
    }
    names = FEATS[stat]
    X = np.column_stack([np.ones(len(d))] + [np.nan_to_num(np.asarray(cols[c], dtype=float)) for c in names])
    return X, names


def nb_logpmf(k, mu, alpha):
    k = np.asarray(k, float)
    mu = np.maximum(np.asarray(mu, float), 1e-9)
    if alpha < 1e-9:
        return k * np.log(mu) - mu - gammaln(k + 1)
    r = 1.0 / alpha
    return gammaln(k + r) - gammaln(r) - gammaln(k + 1) + r * np.log(r / (r + mu)) + k * np.log(mu / (r + mu))


def nb_pmf_matrix(mu, alpha, kmax):
    k = np.arange(kmax + 1)
    P = np.exp(nb_logpmf(k[None, :], np.asarray(mu)[:, None], alpha))
    P[:, -1] += np.clip(1 - P.sum(1), 0, None)
    return P


def fit_alpha(y, mu):
    best = (-np.inf, 0.0)
    for a in (0.0, 0.01, 0.02, 0.04, 0.06, 0.08, 0.1, 0.13, 0.16, 0.2, 0.25, 0.3, 0.4, 0.5):
        ll = nb_logpmf(y, mu, a).sum()
        if ll > best[0]:
            best = (ll, a)
    return best[1]


class StatModel:
    def __init__(self, stat, drop=()):
        self.stat, self.drop = stat, set(drop)

    def _X(self, d):
        X, names = design(d, self.stat)
        keep = [0] + [i + 1 for i, n in enumerate(names) if n not in self.drop]
        self.names = [n for n in names if n not in self.drop]
        return X[:, keep]

    def fit(self, d):
        X = self._X(d)
        y = d[self.stat].to_numpy(float)
        pen = np.full(X.shape[1], 1.0)
        pen[0] = 1e-6
        self.beta, cov = fit_poisson(X, y, np.ones(len(y)), pen)
        self.se = np.sqrt(np.diag(cov))
        mu = np.exp(np.clip(X @ self.beta, -10, 5))
        self.alpha = fit_alpha(y, mu)
        return self

    def mu(self, d):
        return np.exp(np.clip(self._X(d) @ self.beta, -10, 5))

    def coef_table(self):
        return [{"feature": n, "label": FEAT_LABEL[n], "coef": float(b), "se": float(s)}
                for n, b, s in zip(self.names, self.beta[1:], self.se[1:])]

    def export(self):
        return {"stat": self.stat, "intercept": float(self.beta[0]), "coefs": self.coef_table(), "alpha": self.alpha}


def prior_means(sk_train):
    out = {}
    isd = sk_train["pos"].astype(str).str.upper().str.startswith("D")
    for s in STATS:
        out[s] = {"F": float(sk_train.loc[~isd, s].mean()), "D": float(sk_train.loc[isd, s].mean())}
    return out


def eligible(d):
    """Regular skaters: >=10 prior games and >=10 min recent average ice time."""
    return (d["n_prior"] >= 10) & (d["toi_ewm"] >= 10)


# --------------------------------------------------------------------------------------------- goalies
def goalie_features(gg, teams):
    d = gg[gg.starter == 1].copy() if "starter" in gg else gg.copy()
    d = d.sort_values(["goalie_id", "date", "game_id"]).reset_index(drop=True)
    codes = d["goalie_id"].to_numpy()
    d["n_prior"] = d.groupby("goalie_id", sort=False).cumcount().astype(float)
    sh = pd.Series(grp_shift(d["shots_against"], codes))
    sv = pd.Series(grp_shift(d["saves"], codes))
    ew_sh = pd.Series(grp_ewm(sh.to_numpy(), codes, 40))
    ew_sv = pd.Series(grp_ewm(sv.to_numpy(), codes, 40))
    lg = float((gg.saves.sum() / max(gg.shots_against.sum(), 1)))
    neff = ew_sh.fillna(0) * d["n_prior"].clip(upper=60)
    d["sv_hat"] = ((ew_sv / ew_sh).fillna(lg) * neff + lg * 800) / (neff + 800)
    for n in (5, 10, 15, 20):
        d[f"saves_l{n}_sum"], d[f"saves_l{n}_n"] = roll_sum(sv.to_numpy(), codes, n)
    opp = teams[["game_id", "team", "sf_f"]].rename(columns={"team": "opp", "sf_f": "opp_sf_f"})
    own = teams[["game_id", "team", "sa_f", "lg_sf", "is_home"]]
    d = d.merge(opp, on=["game_id", "opp"], how="left").merge(own, on=["game_id", "team"], how="left", suffixes=("", "_t"))
    d["opp_sf_f"] = d["opp_sf_f"].fillna(1.0)
    d["sa_f"] = d["sa_f"].fillna(1.0)
    d["lg_sf"] = d["lg_sf"].fillna(30.0)
    home = d["is_home_t"] if "is_home_t" in d else d["is_home"]
    d["home_c"] = home.fillna(0.5) - 0.5
    return d


class SavesModel:
    def fit(self, d):
        full = d[d.toi_sec >= 3000]
        X = self._X(full)
        y = full.shots_against.to_numpy(float)
        pen = np.array([1e-6, 1.0, 1.0, 1.0, 1.0])
        self.beta, _ = fit_poisson(X, y, np.ones(len(y)), pen)
        self.alpha = fit_alpha(y, np.exp(X @ self.beta))
        pulled = d[(d.toi_sec < 3000) & (d.toi_sec > 0)]
        self.p_pull = float(len(pulled) / max(len(d), 1))
        self.pull_frac = float((pulled.toi_sec / 3600).mean()) if len(pulled) else 0.45
        return self

    def _X(self, d):
        return np.column_stack([np.ones(len(d)), np.log(d.opp_sf_f.astype(float)), np.log(d.sa_f.astype(float)), d.home_c.astype(float), np.log(d.lg_sf.astype(float) / 30.0)]).astype(float)

    def shots_mu(self, d):
        return np.exp(self._X(d) @ self.beta)

    def pmf(self, mu_shots, sv, kmax=60):
        """Saves pmf for each row: mixture (full game / pulled) of NB shots x Binomial(sv)."""
        mu_shots = np.atleast_1d(mu_shots)
        sv = np.atleast_1d(sv)
        n = np.arange(kmax + 1)
        out = np.zeros((len(mu_shots), kmax + 1))
        from scipy.stats import binom
        B = None
        for i, (m, s) in enumerate(zip(mu_shots, sv)):
            ps_full = nb_pmf_matrix([m], self.alpha, kmax)[0]
            ps_pull = nb_pmf_matrix([m * self.pull_frac], self.alpha, kmax)[0]
            ps = (1 - self.p_pull) * ps_full + self.p_pull * ps_pull
            B = binom.pmf(n[None, :], n[:, None], s)  # [shots, saves]
            out[i] = ps @ B
        return out

    def export(self):
        return {"beta": self.beta.tolist(), "alpha": self.alpha, "p_pull": self.p_pull, "pull_frac": self.pull_frac}


# --------------------------------------------------------------------------------------------- helpers
def p_over(pmf, line):
    k = np.arange(pmf.shape[-1])
    over = (pmf * (k > line + 1e-9)).sum(-1)
    push = (pmf * (np.abs(k - line) < 1e-9)).sum(-1)
    return over, push


def hit_rate(vals, line):
    v = [x for x in vals if x is not None and x == x]
    return (sum(1 for x in v if x > line) / len(v)) if v else None


def load_skaters(store):
    import glob
    files = sorted(glob.glob(str(store.dir / "skaters_*.csv.gz")))
    if not files:
        return pd.DataFrame()
    sk = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    sk = sk[sk.toi_sec > 0]
    return sk


# --------------------------------------------------------------------------------------------- tonight
PROP_MARKETS = {"player_shots_on_goal": "sog", "player_points": "points", "player_assists": "assists",
                "player_goals": "goals", "player_goal_scorer_anytime": "goals", "player_total_saves": "saves"}


def _r(x, n=3):
    return None if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else round(float(x), n)


def build_tonight(store, games, gg, slate, slate_feats, hist_feats, rosters, prop_rows, settings, now_iso, tiers, log=print):
    """Projections, pmfs, game logs, H2H and prop-odds EV for every projected skater/goalie on tonight's slate."""
    sk = load_skaters(store)
    if not len(sk) or not slate:
        return {"available": False, "reason": "No player data or no games today.", "players": [], "goalies": []}
    today = slate[0]["date"]
    gsorted = games.sort_values(["date", "game_id"]).reset_index(drop=True)
    gk = {}
    for i, r in enumerate(gsorted[["game_id", "home", "away"]].itertuples(index=False)):
        gk[(int(r.game_id), r.home)] = float(hist_feats["gadj_away_proj"][i])
        gk[(int(r.game_id), r.away)] = float(hist_feats["gadj_home_proj"][i])
    for f in slate_feats:
        g = f["game"]
        ha = (f["away"]["goalie"] or {}).get("adj", 0.0)
        hh = (f["home"]["goalie"] or {}).get("adj", 0.0)
        gk[(int(g["game_id"]), g["home"])] = ha
        gk[(int(g["game_id"]), g["away"])] = hh
    teams = team_table(games, [{"game_id": g["game_id"], "date": today, "home": g["home"], "away": g["away"]} for g in slate])
    # projected lineups: skaters who played the team's most recent game (filtered to current roster if known)
    future, lineup_note = [], {}
    roster_ids = {t: {int(p["id"]) for p in v} for t, v in (rosters or {}).items()}
    full_names = {int(p["id"]): p for v in (rosters or {}).values() for p in v}
    for g in slate:
        for side, opp in (("home", "away"), ("away", "home")):
            team = g[side]
            tr = sk[sk.team == team]
            if not len(tr):
                continue
            last_gid = tr.sort_values(["date", "game_id"]).game_id.iloc[-1]
            last = tr[tr.game_id == last_gid]
            if team in roster_ids and roster_ids[team]:
                last = last[last.player_id.isin(roster_ids[team])]
            lineup_note[team] = f"{len(last)} skaters from {team}'s last game ({tr[tr.game_id == last_gid].date.iloc[0]})"
            for r in last.itertuples(index=False):
                future.append({"game_id": g["game_id"], "season": g["season"], "date": today, "team": team, "opp": g[opp],
                               "player_id": int(r.player_id), "name": r.name, "pos": r.pos, "goals": np.nan,
                               "assists": np.nan, "points": np.nan, "sog": np.nan, "pp_goals": np.nan, "toi_sec": np.nan})
    if not future:
        return {"available": False, "reason": "Could not project any lineups.", "players": [], "goalies": []}
    fut = pd.DataFrame(future)
    pm = prior_means(sk)
    d = skater_features(pd.concat([sk, fut], ignore_index=True), teams, gk, pm)
    hist_rows = d[d["sog"].notna()]
    train = hist_rows[eligible(hist_rows)]
    slate_ids = {int(g["game_id"]) for g in slate}
    tn = d[d["sog"].isna() & d.game_id.isin(slate_ids)].copy()
    models, mus, pmfs = {}, {}, {}
    for s in STATS:
        models[s] = StatModel(s).fit(train)
        mus[s] = models[s].mu(tn)
        pmfs[s] = nb_pmf_matrix(mus[s], models[s].alpha, PMF_MAX[s])
    log(f"  props: fit on {len(train)} player-games, projecting {len(tn)} skaters")
    # game logs
    sk_sorted = sk.sort_values(["date", "game_id"])
    logs = {pid: grp.tail(20) for pid, grp in sk_sorted[sk_sorted.player_id.isin(tn.player_id)].groupby("player_id")}
    cur_season = max(int(g["season"]) for g in slate)
    players = []
    for i, r in enumerate(tn.itertuples(index=False)):
        pid = int(r.player_id)
        lg = logs.get(pid, pd.DataFrame())
        allg = sk_sorted[(sk_sorted.player_id == pid)]
        h2h = allg[allg.opp == r.opp].tail(10)
        fn = full_names.get(pid)
        full = f'{fn["firstName"]} {fn["lastName"]}' if fn else r.name

        def season_line(s):
            x = allg[allg.season == s]
            return {"gp": int(len(x)), "goals": int(x.goals.sum()), "assists": int(x.assists.sum()),
                    "points": int(x.points.sum()), "sog": int(x.sog.sum()), "toi": _r(x.toi_sec.mean() / 60, 1) if len(x) else None}
        rec = {"id": pid, "name": full, "short": r.name, "team": r.team, "opp": r.opp, "game_id": int(r.game_id),
               "pos": r.pos, "home": bool(r.is_home == 1), "status": "PROJECTED",
               "toi_proj": _r(r.toi_ewm, 1), "proj": {s: _r(mus[s][i], 3) for s in STATS},
               "pmf": {s: [round(float(x), 4) for x in pmfs[s][i]] for s in STATS},
               "factors": {"opp_sa": _r(r.opp_sa_f), "opp_ga": _r(r.opp_ga_f), "base": {s: _r(getattr(r, f"{s}_base")) for s in STATS}},
               "logs": [[x.date, x.opp, int(x.goals), int(x.assists), int(x.points), int(x.sog), _r(x.toi_sec / 60, 1)]
                        for x in lg.itertuples(index=False)],
               "h2h": [[x.date, int(x.goals), int(x.assists), int(x.points), int(x.sog), _r(x.toi_sec / 60, 1)]
                       for x in h2h.itertuples(index=False)],
               "season": season_line(cur_season), "prev_season": season_line(cur_season - 10001)}
        players.append(rec)
    # goalies
    gf_hist = gg.copy()
    gfut = []
    for f in slate_feats:
        g = f["game"]
        for side, opp in (("home", "away"), ("away", "home")):
            gi = f[side]["goalie"]
            if not gi:
                continue
            gfut.append({"game_id": g["game_id"], "season": g["season"], "date": today, "team": g[side], "opp": g[opp],
                         "is_home": int(side == "home"), "goalie_id": gi["id"], "name": gi["name"], "starter": 1,
                         "toi_sec": np.nan, "shots_against": np.nan, "saves": np.nan, "goals_against": np.nan,
                         "decision": "", "g_status": f[side]["status"], "g_source": f[side]["source"]})
    goalies = []
    if gfut:
        gfu = pd.DataFrame(gfut)
        gfa = goalie_features(pd.concat([gf_hist, gfu.drop(columns=["g_status", "g_source"])], ignore_index=True), teams)
        ghist = gfa[gfa.saves.notna() & (gfa.n_prior >= 5)]
        sm = SavesModel().fit(ghist)
        gt = gfa[gfa.saves.isna() & gfa.game_id.isin(slate_ids)].merge(gfu[["game_id", "goalie_id", "g_status", "g_source"]],
                                                                         on=["game_id", "goalie_id"], how="left")
        if len(gt):
            mu_s = np.asarray(sm.shots_mu(gt))
            pm_s = sm.pmf(mu_s, gt.sv_hat.to_numpy())
            for i, r in enumerate(gt.itertuples(index=False)):
                gid = int(r.goalie_id)
                gl = gf_hist[(gf_hist.goalie_id == gid) & (gf_hist.starter == 1)].sort_values("date")
                fn = full_names.get(gid)
                goalies.append({"id": gid, "name": f'{fn["firstName"]} {fn["lastName"]}' if fn else r.name, "team": r.team,
                                "opp": r.opp, "game_id": int(r.game_id), "status": r.g_status, "source": r.g_source,
                                "sv_proj": _r(r.sv_hat, 4), "shots_proj": _r(mu_s[i], 2),
                                "saves_proj": _r((pm_s[i] * np.arange(len(pm_s[i]))).sum(), 2),
                                "pmf": [round(float(x), 4) for x in pm_s[i]],
                                "logs": [[x.date, x.opp, int(x.shots_against), int(x.saves), int(x.goals_against), _r(x.toi_sec / 60, 1)]
                                         for x in gl.tail(20).itertuples(index=False)],
                                "h2h": [[x.date, int(x.shots_against), int(x.saves), int(x.goals_against)]
                                        for x in gl[gl.opp == r.opp].tail(10).itertuples(index=False)]})
            models["_saves"] = sm
    out = {"available": True, "date": today, "generated_at": now_iso, "lineup_source": lineup_note,
           "lineup_note": "Lineups are PROJECTED: skaters who played each team's most recent game"
                          + (", filtered to the current NHL roster." if rosters else ".")
                          + " Scratches and injuries are not in the data feed.",
           "models": {s: models[s].export() for s in STATS}, "lines": LINES,
           "saves_model": models["_saves"].export() if "_saves" in models else None, "saves_lines": SAVES_LINES,
           "players": players, "goalies": goalies, "tiers": tiers}
    out["odds"], out["edges"] = price_props(prop_rows, players, goalies, slate, settings, now_iso, tiers, models)
    return out


def match_player(name, team_players):
    n = norm_name(name)
    for p in team_players:
        if norm_name(p["name"]) == n:
            return p
    parts = n.split()
    if len(parts) >= 2:
        cands = [p for p in team_players if norm_name(p["name"]).split()[-1:] == parts[-1:]
                 and norm_name(p["name"])[:1] == parts[0][:1]]
        if len(cands) == 1:
            return cands[0]
    return None


def price_props(rows, players, goalies, slate, settings, now_iso, tiers, models):
    """Attach sportsbook prop prices to projections and compute EV (push-aware), plus a pessimistic EV that
    assumes the projection is 15% too favourable to the side being bet."""
    from .config import parse_iso
    from .odds_math import american_to_decimal, fair_american_with_push
    if not rows:
        return [], []
    now = parse_iso(now_iso)
    es = settings["edges"]
    max_age = settings.get("props", {}).get("max_odds_age_minutes", settings["odds"]["max_odds_age_minutes"])
    by_game = {}
    for p in players:
        by_game.setdefault(p["game_id"], []).append(p)
    gby = {}
    for g in goalies:
        gby.setdefault(g["game_id"], []).append(g)
    start = {int(g["game_id"]): g["start_utc"] for g in slate}
    priced, edges = [], []
    for r in rows:
        gid = int(r["game_id"])
        stat = r["stat"]
        pool = gby.get(gid, []) if stat == "saves" else by_game.get(gid, [])
        p = match_player(r["player"], pool)
        if p is None:
            continue
        line = float(r["point"])
        pmf = np.array(p["pmf"] if stat == "saves" else p["pmf"][stat])
        po, push = p_over(pmf[None, :], line)
        po, push = float(po[0]), float(push[0])
        pw = po if r["side"] == "over" else 1 - po - push
        dec = american_to_decimal(r["price"])
        ev = pw * (dec - 1) - (1 - pw - push)
        # pessimistic: scale the projected mean 15% against the bet
        mu = (np.arange(len(pmf)) * pmf).sum()
        if stat == "saves":
            sm = models.get("_saves")
            f = 0.9 if r["side"] == "over" else 1.1
            pl = sm.pmf([p["shots_proj"] * f], [p["sv_proj"]])[0] if sm else pmf
        else:
            m = models[stat]
            pl = nb_pmf_matrix([mu * (0.85 if r["side"] == "over" else 1.15)], m.alpha, len(pmf) - 1)[0]
        lo_over, lo_push = p_over(pl[None, :], line)
        pw_lo = float(lo_over[0]) if r["side"] == "over" else 1 - float(lo_over[0]) - float(lo_push[0])
        ev_lo = pw_lo * (dec - 1) - (1 - pw_lo - float(lo_push[0]))
        age = (now - parse_iso(r["fetched_at"])).total_seconds() / 60
        started = parse_iso(start[gid]) <= now if gid in start else True
        try:
            fair = fair_american_with_push(pw, push)
        except ValueError:
            fair = None
        tier = "BEATS_BASELINE" if tiers.get(stat) else "EXPERIMENTAL"
        e = {"id": f'p-{gid}-{p["id"]}-{stat}-{r["side"]}-{line}-{r["book"]}', "game_id": gid, "player_id": p["id"],
             "player": p["name"], "team": p["team"], "opp": p["opp"], "stat": stat, "side": r["side"], "line": line,
             "book": r["book_title"], "price": r["price"], "p_win": pw, "p_push": push, "fair": fair, "ev": ev, "ev_lo": ev_lo,
             "break_even": (1 - push) / dec, "odds_updated": r["fetched_at"], "fresh": age <= max_age and not started,
             "tier": tier, "proj": float(mu)}
        e["candidate"] = bool(ev >= es["min_ev"] and ev_lo > es["min_ev_lower"] and e["fresh"])
        e["qualified"] = False  # props cannot reach EDGE_SUPPORTED until closing-line evidence exists
        priced.append(e)
        if e["candidate"]:
            edges.append(e)
    edges.sort(key=lambda e: -e["ev_lo"])
    return priced, edges
