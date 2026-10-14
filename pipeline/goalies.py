"""Goalie ratings and starter projection, computed strictly sequentially (no look-ahead).

Rating = shrunk, time-decayed save percentage:
    sv_g = (decayed_saves + K * sv_league) / (decayed_shots + K)
    adj  = log((1 - sv_g) / (1 - sv_league))      # >0 = worse than average, used as a model feature
This is Goals Saved Above AVERAGE, not above EXPECTED: shot quality (xG) is not in the NHL box score.
Plug in a licensed xG source to upgrade to GSAx (see README "Upgrades").

Starter projection (used when no confirmation source is configured):
  most frequent starter in the team's last 10 games; if the team played yesterday and that goalie started
  it, the next most frequent goalie. Status is always PROJECTED, never CONFIRMED.
"""
from collections import Counter, defaultdict, deque
from datetime import date, timedelta

import numpy as np


class GoalieTracker:
    def __init__(self, prior_shots=1000.0, half_life_days=365.0, league_prior_sv=0.905):
        self.K = prior_shots
        self.hl = half_life_days
        self.state = {}  # goalie_id -> [shots, saves, last_date_ordinal]
        self.league = [3000.0, 3000.0 * league_prior_sv, None]
        self.names, self.team = {}, {}

    def _decay(self, rec, d):
        if rec[2] is not None and d > rec[2]:
            f = 0.5 ** ((d - rec[2]) / self.hl)
            rec[0] *= f
            rec[1] *= f
        rec[2] = d

    def league_sv(self, d):
        self._decay(self.league, d)
        return self.league[1] / self.league[0]

    def rating(self, goalie_id, d):
        """Pre-game (as of ordinal day d) shrunk save% and log-adjustment."""
        lg = self.league_sv(d)
        rec = self.state.get(goalie_id)
        shots = saves = 0.0
        if rec is not None:
            self._decay(rec, d)
            shots, saves = rec[0], rec[1]
        sv = (saves + self.K * lg) / (shots + self.K)
        return sv, float(np.log((1 - sv) / (1 - lg))), shots

    def update(self, goalie_id, d, shots, saves, name=None, team=None):
        rec = self.state.setdefault(goalie_id, [0.0, 0.0, d])
        self._decay(rec, d)
        rec[0] += shots
        rec[1] += saves
        self._decay(self.league, d)
        self.league[0] += shots
        self.league[1] += saves
        if name:
            self.names[goalie_id] = name
        if team:
            self.team[goalie_id] = team


def project_starter(history, played_yesterday_starter=None):
    """history: deque of recent starter ids (most recent last)."""
    if not history:
        return None, None
    counts = Counter(history)
    ranked = [g for g, _ in sorted(counts.items(), key=lambda kv: (-kv[1], -_last_index(history, kv[0])))]
    primary = ranked[0]
    alt = ranked[1] if len(ranked) > 1 else None
    if played_yesterday_starter is not None and primary == played_yesterday_starter and alt is not None:
        return alt, primary
    return primary, alt


def _last_index(h, g):
    for i in range(len(h) - 1, -1, -1):
        if h[i] == g:
            return i
    return -1


def ordinal(dstr):
    return date.fromisoformat(str(dstr)[:10]).toordinal()


def sequential_goalie_features(games, goalie_games, prior_shots=1000.0, half_life=365.0, window=10):
    """For every game (in date order), compute PRE-GAME goalie features using only earlier games.

    Returns (features DataFrame-like dict of arrays aligned to games index, tracker, team_history)
      gadj_home_actual / gadj_away_actual    rating adj of the goalie who actually started
      gadj_home_proj / gadj_away_proj        rating adj of the rule-projected starter
      proj_home / proj_away                  projected starter ids
    """
    tr = GoalieTracker(prior_shots, half_life)
    hist = defaultdict(lambda: deque(maxlen=window))
    last_game = {}  # team -> (ordinal, starter)
    gg_by_game = defaultdict(list)
    for r in goalie_games.itertuples(index=False):
        gg_by_game[int(r.game_id)].append(r)
    n = len(games)
    out = {k: np.zeros(n) for k in ("gadj_home_actual", "gadj_away_actual", "gadj_home_proj", "gadj_away_proj")}
    out["proj_home"] = np.full(n, -1, dtype=np.int64)
    out["proj_away"] = np.full(n, -1, dtype=np.int64)
    dates = games["date"].astype(str).to_numpy()
    order = np.argsort(dates, kind="stable")
    i = 0
    while i < n:
        d = dates[order[i]]
        j = i
        while j < n and dates[order[j]] == d:
            j += 1
        dd = ordinal(d)
        block = order[i:j]
        for gi in block:
            row = games.iloc[gi]
            for side in ("home", "away"):
                team = row[side]
                lg = last_game.get(team)
                yest = lg[1] if lg and lg[0] == dd - 1 else None
                proj, _ = project_starter(list(hist[team]), yest)
                actual = row[f"{side}_starter"]
                actual = int(actual) if actual == actual and actual is not None else None
                out[f"gadj_{side}_actual"][gi] = tr.rating(actual, dd)[1] if actual else 0.0
                out[f"gadj_{side}_proj"][gi] = tr.rating(proj, dd)[1] if proj else 0.0
                out[f"proj_{side}"][gi] = proj if proj else -1
        for gi in block:
            row = games.iloc[gi]
            for r in gg_by_game.get(int(row["game_id"]), []):
                tr.update(int(r.goalie_id), dd, r.shots_against, r.saves, r.name, r.team)
            for side in ("home", "away"):
                st = row[f"{side}_starter"]
                if st == st and st is not None:
                    hist[row[side]].append(int(st))
                    last_game[row[side]] = (dd, int(st))
        i = j
    return out, tr, hist, last_game


def b2b_flags(games):
    """1 if the team also played on the previous calendar day."""
    played = set()
    for r in games[["home", "away", "date"]].itertuples(index=False):
        o = ordinal(r.date)
        played.add((r.home, o))
        played.add((r.away, o))
    hb = np.array([(r.home, ordinal(r.date) - 1) in played for r in games[["home", "date"]].itertuples(index=False)], float)
    ab = np.array([(r.away, ordinal(r.date) - 1) in played for r in games[["away", "date"]].itertuples(index=False)], float)
    return hb, ab, played


def rest_days(team, d_ord, played):
    for k in range(1, 15):
        if (team, d_ord - k) in played:
            return k - 1
    return None
