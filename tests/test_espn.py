"""ESPN fallback: parsing (fixtures follow the feed's documented-by-observation shape) and de-duplication."""
import pandas as pd

from pipeline import espn
from pipeline.ingest import Ingestor
from pipeline.storage import Store

EVENT = {
    "id": "401802449", "date": "2025-10-21T23:00Z", "season": {"year": 2026, "type": 2},
    "competitions": [{"neutralSite": False, "venue": {"fullName": "Scotiabank Arena"},
                      "status": {"period": 5, "type": {"name": "STATUS_FINAL", "state": "post", "completed": True, "detail": "Final/SO"}},
                      "competitors": [
                          {"homeAway": "home", "score": "3", "team": {"abbreviation": "TOR", "displayName": "Toronto Maple Leafs", "logo": "t.png"}},
                          {"homeAway": "away", "score": "4", "team": {"abbreviation": "NJ", "displayName": "New Jersey Devils", "logo": "n.png"}}]}]}

GK = ["goalsAgainst", "shotsAgainst", "shootoutSaves", "shootoutShotsAgainst", "saves", "savePct", "evenStrengthSaves",
      "powerPlaySaves", "shortHandedSaves", "timeOnIce", "ytdGoals", "penaltyMinutes"]
SK = ["blockedShots", "hits", "takeaways", "plusMinus", "timeOnIce", "powerPlayTimeOnIce", "shortHandedTimeOnIce",
      "evenStrengthTimeOnIce", "shifts", "goals", "ytdGoals", "assists", "shotsTotal", "shotsMissed", "shootoutGoals",
      "faceoffsWon", "faceoffsLost", "faceoffPercent", "giveaways", "penalties", "penaltyMinutes"]


def team_block(abbr, goalies, skaters):
    return {"team": {"abbreviation": abbr}, "statistics": [
        {"name": "forwards", "keys": SK, "athletes": [{"athlete": {"id": a, "displayName": n}, "stats": s} for a, n, s in skaters]},
        {"name": "defenses", "keys": SK, "athletes": []},
        {"name": "goalies", "keys": GK, "athletes": [{"athlete": {"id": a, "displayName": n}, "stats": s} for a, n, s in goalies]}]}


def sk(g, a, s):
    return ["0", "0", "0", "0", "18:00", "0", "0", "0", "20", str(g), "0", str(a), str(s), "0", "0", "0", "0", "0", "0", "0", "0"]


SUMMARY = {"boxscore": {
    "teams": [{"team": {"abbreviation": "NJ"}, "statistics": [{"name": "shotsTotal", "displayValue": "25"}]},
              {"team": {"abbreviation": "TOR"}, "statistics": [{"name": "shotsTotal", "displayValue": "31"}]}],
    "players": [
        team_block("NJ", [("5111", "Jake Allen", ["2", "31", "0", "0", "28", ".903", "", "", "", "65:00", "0", "0"])],
                   [("1", "Jack Hughes", sk(1, 1, 5))]),
        team_block("TOR", [("3000", "Joseph Woll", ["3", "25", "0", "0", "22", ".880", "", "", "", "45:10", "0", "0"]),
                           ("3001", "Backup Guy", ["0", "0", "0", "0", "0", ".000", "", "", "", "19:50", "0", "0"])],
                   [("2", "Auston Matthews", sk(2, 0, 7))])]}}


def test_parse_event_maps_abbreviations_and_shootout():
    r = espn.parse_event(EVENT, "2025-10-21")
    assert r["home"] == "TOR" and r["away"] == "NJD"
    assert r["season"] == 20252026 and r["game_type"] == 2 and r["game_state"] == "OFF"
    assert r["last_period"] == "SO" and r["game_id"] == -401802449
    assert r["start_utc"] == "2025-10-21T23:00:00Z"


def test_parse_summary_goalies_shots_and_id_mapping():
    sched = espn.parse_event(EVENT, "2025-10-21")
    gg = pd.DataFrame([{"team": "NJD", "name": "J. Allen", "goalie_id": 8474596}])
    gmap = espn.build_goalie_map(gg)
    game, goalies, skaters = espn.parse_summary(SUMMARY, sched, gmap)
    assert game["home_goals"] == 3 and game["away_goals"] == 4 and game["last_period"] == "SO"
    assert game["home_sog"] == 31 and game["away_sog"] == 25
    assert game["away_starter"] == 8474596, "mapped to the NHL id by team + initial + last name"
    assert game["home_starter"] == -3000, "unmatched goalie keeps a negative ESPN id; starter = most TOI"
    allen = [g for g in goalies if g["goalie_id"] == 8474596][0]
    assert allen["shots_against"] == 31 and allen["saves"] == 28 and allen["toi_sec"] == 3900
    assert any(s["name"] == "Auston Matthews" and s["sog"] == 7 and s["points"] == 2 for s in skaters)


def test_nhl_row_replaces_espn_row(tmp_path):
    store = Store(tmp_path)
    ing = Ingestor(None, store, log=lambda *a: None)
    sched = espn.parse_event(EVENT, "2025-10-21")
    g, gr, sk_ = espn.parse_summary(SUMMARY, sched, {})
    ing._new_games.append(g); ing._new_goalies.extend(gr); ing._new_skaters[g["season"]] = sk_
    ing.flush()
    assert list(store.games().game_id) == [-401802449]
    nhl = dict(g, game_id=2025020100, home_starter=1, away_starter=2)
    ing._new_games.append(nhl)
    ing._new_goalies.append(dict(gr[0], game_id=2025020100, goalie_id=1))
    ing.flush()
    games = store.games()
    assert list(games.game_id) == [2025020100], "ESPN duplicate removed once the NHL version arrives"
    assert set(store.goalie_games().game_id) == {2025020100}
    assert (-401802449 not in set(store.skaters(20252026).game_id))
