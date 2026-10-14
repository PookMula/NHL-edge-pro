import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pipeline.edges import novig_by_book
from pipeline.goalies import project_starter
from pipeline.ingest import parse_boxscore, parse_schedule_game, season_for_date, toi_to_sec
from pipeline.market_eval import settle
from pipeline.model import Design, fit_poisson
from pipeline.odds_api import build_name_lookup, normalize

FIX = Path(__file__).parent / "fixtures"


def test_poisson_fit_recovers_known_ratings():
    rng = np.random.default_rng(0)
    teams = [f"T{i}" for i in range(8)]
    att = rng.normal(0, 0.15, 8)
    dfn = rng.normal(0, 0.15, 8)
    d = Design(teams)
    home, away = [], []
    for _ in range(6000):
        h, a = rng.choice(8, 2, replace=False)
        home.append(teams[h]); away.append(teams[a])
    n = len(home)
    X = d.rows(home, away, np.zeros(n), np.zeros(n), np.zeros(n), np.zeros(n), np.zeros(n))
    true = np.zeros(d.P)
    true[0], true[1] = np.log(2.9), 0.05
    true[d.i_att:d.i_att + 8], true[d.i_def:d.i_def + 8] = att, dfn
    y = rng.poisson(np.exp(X @ true))
    b, cov = fit_poisson(X, y, np.ones(len(y)), d.penalty(1.0))
    est_att = b[d.i_att:d.i_att + 8] - b[d.i_att:d.i_att + 8].mean()
    assert np.corrcoef(est_att, att - att.mean())[0, 1] > 0.9
    assert b[1] == pytest.approx(0.05, abs=0.03)
    assert np.all(np.diag(cov) > 0)


def test_project_starter_rest_rule():
    hist = [1, 1, 2, 1, 1, 1, 2, 1, 1, 1]
    assert project_starter(hist) == (1, 2)
    assert project_starter(hist, played_yesterday_starter=1) == (2, 1)
    assert project_starter([]) == (None, None)


def test_settlement_rules():
    assert settle("h2h", "home", None, 3, 2) == "win"
    assert settle("spreads", "home", -1.5, 3, 2) == "loss"
    assert settle("spreads", "away", 1.5, 3, 2) == "win"
    assert settle("totals", "over", 5.5, 3, 3) == "win"
    assert settle("totals", "under", 6.0, 3, 3) == "push"
    assert settle("totals", "over", 6.0, 4, 3) == "win"


def test_season_for_date():
    from datetime import date
    assert season_for_date(date(2026, 10, 13)) == 20262027
    assert season_for_date(date(2027, 3, 1)) == 20262027


def test_parse_boxscore_fixture():
    bs = json.loads((FIX / "boxscore.json").read_text())
    sched = {"neutral": 0}
    g, goalies, skaters = parse_boxscore(bs, sched)
    assert g["home"] == "NYI" and g["away"] == "SJS"
    assert g["last_period"] == "OT"
    assert g["home_starter"] == 8478009 and g["away_starter"] == 8482137
    assert {x["goalie_id"] for x in goalies} == {8478009, 8482137}
    ask = [x for x in goalies if x["goalie_id"] == 8482137][0]
    assert ask["shots_against"] == 27 and ask["saves"] == 23 and ask["toi_sec"] == toi_to_sec("62:31")
    assert any(s["player_id"] == 8484227 and s["sog"] == 6 for s in skaters)


def test_parse_schedule_game():
    g = {"id": 2026020090, "season": 20262027, "gameType": 2, "startTimeUTC": "2026-10-13T22:00:00Z", "gameState": "FUT",
         "neutralSite": False, "venue": {"default": "Little Caesars Arena"},
         "homeTeam": {"abbrev": "DET", "placeName": {"default": "Detroit"}, "commonName": {"default": "Red Wings"}, "logo": "x"},
         "awayTeam": {"abbrev": "NJD", "placeName": {"default": "New Jersey"}, "commonName": {"default": "Devils"}, "logo": "y"}}
    r = parse_schedule_game(g, "2026-10-13")
    assert r["home_name"] == "Detroit Red Wings" and r["away"] == "NJD" and r["neutral"] == 0


def test_odds_normalize_and_novig():
    slate = [{"home": "MTL", "away": "TOR", "home_name": "Montréal Canadiens", "away_name": "Toronto Maple Leafs"}]
    lut = build_name_lookup(slate)
    events = [{"id": "e1", "commence_time": "2026-10-13T23:00:00Z", "home_team": "Montreal Canadiens",
               "away_team": "Toronto Maple Leafs", "bookmakers": [{"key": "dk", "title": "DraftKings", "markets": [
                   {"key": "h2h", "last_update": "t", "outcomes": [{"name": "Montreal Canadiens", "price": 120},
                                                                   {"name": "Toronto Maple Leafs", "price": -140}]},
                   {"key": "totals", "outcomes": [{"name": "Over", "price": -110, "point": 6.5},
                                                  {"name": "Under", "price": -110, "point": 6.5}]}]}]},
              {"id": "e2", "commence_time": "x", "home_team": "Nowhere Ghosts", "away_team": "Toronto Maple Leafs", "bookmakers": []}]
    rows, unmatched = normalize(events, "2026-10-13T18:00:00Z", lut)
    assert unmatched == ["Toronto Maple Leafs @ Nowhere Ghosts"]
    home = [r for r in rows if r["market"] == "h2h" and r["side"] == "home"][0]
    assert home["home"] == "MTL" and home["price"] == 120
    nv = novig_by_book(rows)
    assert nv[("totals", "over", 6.5)] == [pytest.approx(0.5)]
    assert sum(nv[("h2h", "home", None)] + nv[("h2h", "away", None)]) == pytest.approx(1.0)


def test_no_lookahead_in_goalie_features():
    from pipeline.goalies import sequential_goalie_features
    games = pd.DataFrame([
        {"game_id": 1, "date": "2025-10-01", "home": "A", "away": "B", "home_starter": 10, "away_starter": 20},
        {"game_id": 2, "date": "2025-10-03", "home": "A", "away": "B", "home_starter": 10, "away_starter": 20}])
    gg = pd.DataFrame([
        {"game_id": 1, "goalie_id": 10, "shots_against": 30, "saves": 20, "name": "x", "team": "A"},
        {"game_id": 1, "goalie_id": 20, "shots_against": 30, "saves": 30, "name": "y", "team": "B"},
        {"game_id": 2, "goalie_id": 10, "shots_against": 30, "saves": 30, "name": "x", "team": "A"},
        {"game_id": 2, "goalie_id": 20, "shots_against": 30, "saves": 20, "name": "y", "team": "B"}])
    f, *_ = sequential_goalie_features(games, gg)
    # game 1: nobody has history -> 0; game 2 uses only game 1 (goalie 10 was bad -> positive adj)
    assert f["gadj_home_actual"][0] == 0.0 and f["gadj_away_actual"][0] == 0.0
    assert f["gadj_home_actual"][1] > 0 > f["gadj_away_actual"][1]
