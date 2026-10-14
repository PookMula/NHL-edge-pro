"""Flat-file storage (CSV in the repo). Small enough to commit, diff-friendly, no DB server needed.

Files in data/:
  games.csv            one row per completed game (regular season + playoffs)
  goalie_games.csv     one row per goalie appearance
  skaters_<season>.csv.gz  skater game logs (kept for the future player-prop models)
  odds/<date>.csv      every odds snapshot fetched (append-only archive)
  predictions_log.csv  last pre-game prediction per game (locked at puck drop)
  edge_log.csv         every model edge candidate when first flagged (for CLV tracking)
  parlay_log.csv       every parlay candidate when first flagged
"""
from pathlib import Path

import pandas as pd

GAME_COLS = ["game_id", "season", "game_type", "date", "start_utc", "home", "away",
             "home_goals", "away_goals", "last_period", "home_sog", "away_sog",
             "neutral", "home_starter", "away_starter"]
GOALIE_COLS = ["game_id", "season", "date", "team", "opp", "is_home", "goalie_id", "name",
               "starter", "toi_sec", "shots_against", "saves", "goals_against", "decision"]
SKATER_COLS = ["game_id", "season", "date", "team", "opp", "player_id", "name", "pos",
               "goals", "assists", "points", "sog", "pp_goals", "toi_sec"]


class Store:
    def __init__(self, data_dir):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, name):
        return self.dir / name

    def read(self, name, cols=None):
        p = self.path(name)
        if not p.exists() or p.stat().st_size == 0:
            return pd.DataFrame(columns=cols or [])
        return pd.read_csv(p)

    def write(self, name, df):
        p = self.path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(p, index=False)

    def games(self):
        df = self.read("games.csv", GAME_COLS)
        if len(df):
            df = df.sort_values(["date", "game_id"]).reset_index(drop=True)
        return df

    def goalie_games(self):
        return self.read("goalie_games.csv", GOALIE_COLS)

    def skaters(self, season):
        return self.read(f"skaters_{season}.csv.gz", SKATER_COLS)
