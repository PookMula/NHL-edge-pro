import numpy as np
import pandas as pd
import pytest

from pipeline import props as P
from pipeline.odds_api import normalize_props


def test_roll_sum_matches_pandas_per_group():
    rng = np.random.default_rng(0)
    codes = np.repeat([1, 2, 3], [7, 3, 12])
    v = rng.integers(0, 5, len(codes)).astype(float)
    v[[2, 15]] = np.nan
    s, n = P.roll_sum(v, codes, 5)
    ref = pd.Series(v).groupby(codes).transform(lambda x: x.rolling(5, min_periods=1).sum())
    cnt = pd.Series(v).notna().astype(float).groupby(codes).transform(lambda x: x.rolling(5, min_periods=1).sum())
    assert np.allclose(s, ref.fillna(0)) and np.allclose(n, cnt)


def test_grp_shift_and_cumsum():
    codes = np.array([1, 1, 1, 2, 2])
    v = np.array([1., 2., 3., 4., 5.])
    sh = P.grp_shift(v, codes)
    assert np.isnan(sh[0]) and sh[1] == 1 and sh[2] == 2 and np.isnan(sh[3]) and sh[4] == 4
    assert list(P.grp_cumsum(v, codes)) == [1, 3, 6, 4, 9]


def test_nb_pmf_properties():
    for alpha in (0.0, 0.1, 0.4):
        pm = P.nb_pmf_matrix([2.3], alpha, 60)[0]
        assert pm.sum() == pytest.approx(1.0, abs=1e-9)
        assert (np.arange(61) * pm).sum() == pytest.approx(2.3, abs=1e-3)
    poisson = P.nb_pmf_matrix([2.0], 0.0, 30)[0]
    assert poisson[0] == pytest.approx(np.exp(-2.0))
    wide = P.nb_pmf_matrix([2.0], 0.4, 30)[0]
    assert wide[0] > poisson[0]  # overdispersion fattens the zero/tail


def test_p_over_and_push():
    pm = np.array([[0.2, 0.3, 0.5]])
    o, pu = P.p_over(pm, 1.5)
    assert o[0] == pytest.approx(0.5) and pu[0] == 0
    o, pu = P.p_over(pm, 1.0)
    assert o[0] == pytest.approx(0.5) and pu[0] == pytest.approx(0.3)


def _toy_skaters(n=30):
    rows = []
    for i in range(n):
        d = (pd.Timestamp("2025-10-01") + pd.Timedelta(days=2 * i)).strftime("%Y-%m-%d")
        rows.append({"game_id": 1000 + i, "season": 20252026, "date": d, "team": "AAA", "opp": "BBB" if i % 2 else "CCC",
                     "player_id": 1, "name": "A. Player", "pos": "C", "goals": i % 3 == 0, "assists": 0, "points": i % 3 == 0,
                     "sog": i % 5, "pp_goals": 0, "toi_sec": 1100 + i})
    return pd.DataFrame(rows).astype({"goals": int, "points": int})


def _toy_teams(sk):
    g = sk[["game_id", "date", "team", "opp"]]
    return pd.DataFrame({"game_id": g.game_id, "team": g.team, "opp": g.opp, "is_home": 1, "date": g.date,
                         "lg_sf": 30.0, "lg_gf": 3.0, "sf_f": 1.0, "sa_f": 1.0, "gf_f": 1.0, "ga_f": 1.0})


PM = {s: {"F": 1.0, "D": 0.5} for s in P.STATS}


def test_features_use_only_prior_games():
    sk = _toy_skaters()
    d1 = P.skater_features(sk, _toy_teams(sk), {}, PM)
    sk2 = sk.copy()
    sk2.loc[sk2.index[-1], "sog"] = 99          # change the LAST game's outcome
    d2 = P.skater_features(sk2, _toy_teams(sk2), {}, PM)
    cols = [c for c in d1.columns if c.startswith("sog_")]
    assert np.allclose(d1[cols].to_numpy(float), d2[cols].to_numpy(float)), "a game's own result leaked into its features"
    sk3 = sk.copy()
    sk3.loc[sk3.index[10], "sog"] = 99          # change an EARLIER game -> later features must move
    d3 = P.skater_features(sk3, _toy_teams(sk3), {}, PM)
    assert d3["sog_l5_sum"].iloc[11] == d1["sog_l5_sum"].iloc[11] - sk.sog.iloc[10] + 99
    assert d3["sog_l5_sum"].iloc[10] == d1["sog_l5_sum"].iloc[10]


def test_window_and_h2h_counts():
    sk = _toy_skaters()
    d = P.skater_features(sk, _toy_teams(sk), {}, PM)
    i = 25
    assert d["sog_l10_n"].iloc[i] == 10
    assert d["sog_l10_sum"].iloc[i] == sk.sog.iloc[i - 10:i].sum()
    prior_same_opp = sk.iloc[:i][sk.opp.iloc[:i] == sk.opp.iloc[i]].tail(10)
    assert d["sog_h2h_n"].iloc[i] == len(prior_same_opp)
    assert d["sog_h2h_sum"].iloc[i] == prior_same_opp.sog.sum()


def test_match_player_names():
    pool = [{"name": "Auston Matthews"}, {"name": "Mitch Marner"}, {"name": "Tim Stützle"}]
    assert P.match_player("Auston Matthews", pool)["name"] == "Auston Matthews"
    assert P.match_player("Tim Stutzle", pool)["name"] == "Tim Stützle"
    assert P.match_player("A. Matthews", pool)["name"] == "Auston Matthews"
    assert P.match_player("Connor McDavid", pool) is None


def test_normalize_props_over_under_and_yes_no():
    ev = {"id": "e1", "bookmakers": [{"key": "dk", "title": "DraftKings", "markets": [
        {"key": "player_shots_on_goal", "outcomes": [{"name": "Over", "description": "Auston Matthews", "point": 3.5, "price": 110},
                                                      {"name": "Under", "description": "Auston Matthews", "point": 3.5, "price": -140}]},
        {"key": "player_goal_scorer_anytime", "outcomes": [{"name": "Yes", "description": "Auston Matthews", "price": 120}]},
        {"key": "player_total_saves", "outcomes": [{"name": "Over", "description": "Joseph Woll", "point": 27.5, "price": -115}]}]}]}
    rows = normalize_props(ev, "t", 5, "TOR", "BOS")
    assert {(r["stat"], r["side"], r["point"]) for r in rows} == {("sog", "over", 3.5), ("sog", "under", 3.5),
                                                                  ("goals", "over", 0.5), ("saves", "over", 27.5)}
    assert all(r["game_id"] == 5 for r in rows)


def test_team_h2h_orientation():
    from pipeline.site import team_h2h
    g = pd.DataFrame([
        {"date": "2025-01-01", "home": "AAA", "away": "BBB", "home_goals": 2, "away_goals": 3, "last_period": "REG"},
        {"date": "2025-02-01", "home": "BBB", "away": "AAA", "home_goals": 1, "away_goals": 4, "last_period": "OT"},
        {"date": "2025-03-01", "home": "AAA", "away": "CCC", "home_goals": 5, "away_goals": 0, "last_period": "REG"}])
    h = team_h2h(g, "AAA", "BBB")
    assert h["n"] == 2 and h["home_wins"] == 1 and h["away_wins"] == 1
    assert h["avg_total"] == pytest.approx(5.0) and h["avg_margin_home"] == pytest.approx(1.0)
    assert h["games"][0]["ot"] == "OT"
