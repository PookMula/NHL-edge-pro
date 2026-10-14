"""Ingest schedules and box scores from the NHL API into data/*.csv.

backfill(): walk every season from settings.backfill_start_season, fetch every final game's box score
            that is not stored yet. Resumable (saves every 100 games).
incremental(): last ~8 days + today; fetches new finals and returns today's slate.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from .nhl_api import NHLApiError
from .storage import GAME_COLS, GOALIE_COLS, SKATER_COLS

FINAL_STATES = {"OFF", "FINAL"}
ET = ZoneInfo("America/New_York")


def _name(o):
    if isinstance(o, dict):
        return o.get("default", "")
    return o or ""


def toi_to_sec(s):
    try:
        m, sec = str(s).split(":")
        return int(m) * 60 + int(sec)
    except Exception:
        return 0


def today_et():
    return datetime.now(ET).date()


def season_for_date(d):
    """NHL season id (e.g. 20262027) that a date falls in (season rolls over in August)."""
    y = d.year if d.month >= 8 else d.year - 1
    return y * 10000 + (y + 1)


def parse_schedule_game(g, day_date=None):
    h, a = g["homeTeam"], g["awayTeam"]
    return {
        "game_id": int(g["id"]), "season": int(g["season"]), "game_type": int(g["gameType"]),
        "start_utc": g.get("startTimeUTC"), "game_state": g.get("gameState"),
        "neutral": int(bool(g.get("neutralSite", False))),
        "home": h["abbrev"], "away": a["abbrev"],
        "home_name": f"{_name(h.get('placeName'))} {_name(h.get('commonName'))}".strip(),
        "away_name": f"{_name(a.get('placeName'))} {_name(a.get('commonName'))}".strip(),
        "home_logo": h.get("logo", ""), "away_logo": a.get("logo", ""),
        "home_dark_logo": h.get("darkLogo", ""), "away_dark_logo": a.get("darkLogo", ""),
        "home_score": h.get("score"), "away_score": a.get("score"),
        "venue": _name(g.get("venue")), "date": day_date,
    }


def parse_boxscore(bs, sched=None):
    """Return (game_row, goalie_rows, skater_rows) from a final box score."""
    gid = int(bs["id"])
    home, away = bs["homeTeam"], bs["awayTeam"]
    last = (bs.get("gameOutcome") or {}).get("lastPeriodType") or \
        (bs.get("periodDescriptor") or {}).get("periodType", "REG")
    pbg = bs.get("playerByGameStats", {})
    goalie_rows, skater_rows, starters = [], [], {}
    for side, team, opp, is_home in (("homeTeam", home, away, 1), ("awayTeam", away, home, 0)):
        block = pbg.get(side, {})
        goalies = block.get("goalies", [])
        played = [x for x in goalies if toi_to_sec(x.get("toi", "0:00")) > 0]
        st = [x for x in played if x.get("starter")]
        if not st and played:
            st = [max(played, key=lambda x: toi_to_sec(x.get("toi", "0:00")))]
        starters[is_home] = int(st[0]["playerId"]) if st else None
        for x in played:
            goalie_rows.append({
                "game_id": gid, "season": int(bs["season"]), "date": bs["gameDate"],
                "team": team["abbrev"], "opp": opp["abbrev"], "is_home": is_home,
                "goalie_id": int(x["playerId"]), "name": _name(x.get("name")),
                "starter": int(starters[is_home] == int(x["playerId"])),
                "toi_sec": toi_to_sec(x.get("toi")), "shots_against": int(x.get("shotsAgainst") or 0),
                "saves": int(x.get("saves") or 0), "goals_against": int(x.get("goalsAgainst") or 0),
                "decision": x.get("decision", "")})
        for pos_key in ("forwards", "defense"):
            for x in block.get(pos_key, []):
                skater_rows.append({
                    "game_id": gid, "season": int(bs["season"]), "date": bs["gameDate"],
                    "team": team["abbrev"], "opp": opp["abbrev"], "player_id": int(x["playerId"]),
                    "name": _name(x.get("name")), "pos": x.get("position", ""),
                    "goals": int(x.get("goals") or 0), "assists": int(x.get("assists") or 0),
                    "points": int(x.get("points") or 0), "sog": int(x.get("sog") or 0),
                    "pp_goals": int(x.get("powerPlayGoals") or 0), "toi_sec": toi_to_sec(x.get("toi"))})
    game_row = {
        "game_id": gid, "season": int(bs["season"]), "game_type": int(bs["gameType"]),
        "date": bs["gameDate"], "start_utc": bs.get("startTimeUTC"),
        "home": home["abbrev"], "away": away["abbrev"],
        "home_goals": int(home.get("score", 0)), "away_goals": int(away.get("score", 0)),
        "last_period": last, "home_sog": int(home.get("sog") or 0), "away_sog": int(away.get("sog") or 0),
        "neutral": int(sched["neutral"]) if sched else 0,
        "home_starter": starters.get(1), "away_starter": starters.get(0)}
    return game_row, goalie_rows, skater_rows


def list_season_games(client, season):
    y = season // 10000
    d = date(y, 9, 1)
    end = date(y + 1, 7, 10)
    out = {}
    while d <= end:
        data = client.schedule(d.isoformat())
        for day in data.get("gameWeek", []):
            for g in day.get("games", []):
                if int(g.get("season", 0)) == season and int(g.get("gameType", 0)) in (2, 3):
                    out[int(g["id"])] = parse_schedule_game(g, day.get("date"))
        nxt = data.get("nextStartDate")
        nd = date.fromisoformat(nxt) if nxt else None
        d = nd if nd and nd > d else d + timedelta(days=7)
    return out


class Ingestor:
    def __init__(self, client, store, log=print):
        self.client, self.store, self.log = client, store, log
        self.games = store.games()
        self.goalies = store.goalie_games()
        self._new_games, self._new_goalies, self._new_skaters = [], [], {}
        self.errors = []

    def known_ids(self):
        ids = set(self.games["game_id"].astype(int)) if len(self.games) else set()
        return ids | {g["game_id"] for g in self._new_games}

    def fetch_final(self, sched_rows):
        known = self.known_ids()
        todo = [s for s in sched_rows if s["game_id"] not in known and s["game_state"] in FINAL_STATES]
        for i, s in enumerate(todo, 1):
            try:
                bs = self.client.boxscore(s["game_id"])
            except NHLApiError as e:
                self.errors.append(str(e))
                continue
            if bs.get("gameState") not in FINAL_STATES:
                continue
            g, gr, sk = parse_boxscore(bs, s)
            self._new_games.append(g)
            self._new_goalies.extend(gr)
            self._new_skaters.setdefault(g["season"], []).extend(sk)
            if i % 100 == 0:
                self.log(f"  fetched {i}/{len(todo)} box scores")
                self.flush()
        self.flush()
        return len(todo)

    def known_keys(self):
        """(date, home, away) of every stored game from any source."""
        ks = set(zip(self.games["date"].astype(str), self.games["home"], self.games["away"])) if len(self.games) else set()
        return ks | {(g["date"], g["home"], g["away"]) for g in self._new_games}

    def flush(self):
        if not self._new_games:
            return
        ng = pd.DataFrame(self._new_games, columns=GAME_COLS)
        # an official NHL row replaces any ESPN fallback row (negative id) for the same game
        nhl_keys = {(g["date"], g["home"], g["away"]) for g in self._new_games if g["game_id"] > 0}
        drop_ids = set()
        if len(self.games) and nhl_keys:
            k = list(zip(self.games["date"].astype(str), self.games["home"], self.games["away"]))
            drop_ids = {gid for gid, kk in zip(self.games["game_id"], k) if gid < 0 and kk in nhl_keys}
        self.games = pd.concat([self.games, ng], ignore_index=True) if len(self.games) else ng
        self.games = self.games[~self.games["game_id"].isin(drop_ids)]
        self.games = self.games.drop_duplicates("game_id", keep="last").sort_values(["date", "game_id"])
        self.store.write("games.csv", self.games)
        if self._new_goalies or drop_ids:
            gg = pd.DataFrame(self._new_goalies, columns=GOALIE_COLS)
            self.goalies = pd.concat([self.goalies, gg], ignore_index=True) if len(self.goalies) else gg
            self.goalies = self.goalies[~self.goalies["game_id"].isin(drop_ids)]
            self.goalies = self.goalies.drop_duplicates(["game_id", "goalie_id"], keep="last")
            self.store.write("goalie_games.csv", self.goalies)
        seasons = set(self._new_skaters) | ({int(s) for s in ng["season"]} if drop_ids else set())
        for season in seasons:
            rows = self._new_skaters.get(season, [])
            old = self.store.skaters(season)
            if drop_ids and len(old):
                old = old[~old["game_id"].isin(drop_ids)]
            new = pd.DataFrame(rows, columns=SKATER_COLS)
            df = pd.concat([old, new], ignore_index=True) if len(old) else new
            df = df.drop_duplicates(["game_id", "player_id"], keep="last")
            self.store.write(f"skaters_{season}.csv.gz", df)
        self._new_games, self._new_goalies, self._new_skaters = [], [], {}

    def backfill(self, start_season, end_season=None):
        end_season = end_season or season_for_date(today_et())
        s = start_season
        while s <= end_season:
            self.log(f"season {s}: listing schedule")
            rows = list_season_games(self.client, s)
            n = self.fetch_final(list(rows.values()))
            self.log(f"season {s}: {len(rows)} games listed, {n} new box scores")
            y = s // 10000 + 1
            s = y * 10000 + y + 1

    # ---------------- ESPN fallback ----------------
    def fetch_final_espn(self, espn_client, sched_rows):
        from . import espn
        gmap = espn.build_goalie_map(self.goalies)
        known = self.known_keys()
        todo = [s for s in sched_rows if s["game_state"] == "OFF" and (s["date"], s["home"], s["away"]) not in known
                and s.get("home_score") is not None]
        for i, s in enumerate(todo, 1):
            try:
                summ = espn_client.summary(s["espn_id"])
                g, gr, sk = espn.parse_summary(summ, s, gmap)
            except (espn.EspnError, KeyError, ValueError, TypeError) as e:
                self.errors.append(f"espn {s['espn_id']}: {e}")
                continue
            self._new_games.append(g)
            self._new_goalies.extend(gr)
            self._new_skaters.setdefault(g["season"], []).extend(sk)
            if i % 100 == 0:
                self.log(f"  [espn] fetched {i}/{len(todo)} summaries")
                self.flush()
        self.flush()
        return len(todo)

    def incremental_espn(self, espn_client, today=None):
        from . import espn
        today = today or today_et()
        rows, today_rows = [], []
        for k in range(14, -1, -1):
            d = today - timedelta(days=k)
            r = espn.day_rows(espn_client, d)
            rows += r
            if k == 0:
                today_rows = r
        n = self.fetch_final_espn(espn_client, rows)
        self.log(f"incremental [espn fallback]: {n} new final games")
        return today_rows

    def backfill_espn(self, espn_client, start_season, end_season=None):
        from . import espn
        end_season = end_season or season_for_date(today_et())
        s = start_season
        while s <= end_season:
            rows = []
            for d in espn.season_dates(s):
                if d > today_et():
                    break
                rows += espn.day_rows(espn_client, d)
            n = self.fetch_final_espn(espn_client, rows)
            self.log(f"season {s} [espn]: {len(rows)} games listed, {n} new")
            y = s // 10000 + 1
            s = y * 10000 + y + 1

    def incremental(self, today=None):
        """Fetch finals from the last ~2 weeks and return today's schedule rows."""
        today = today or today_et()
        rows = {}
        for start in (today - timedelta(days=14), today - timedelta(days=7), today):
            data = self.client.schedule(start.isoformat())
            for day in data.get("gameWeek", []):
                for g in day.get("games", []):
                    if int(g.get("gameType", 0)) in (2, 3):
                        rows[int(g["id"])] = parse_schedule_game(g, day.get("date"))
        n = self.fetch_final([r for r in rows.values() if r["date"] and r["date"] < today.isoformat()
                              or r["game_state"] in FINAL_STATES])
        self.log(f"incremental: {n} new final games")
        return [r for r in rows.values() if r["date"] == today.isoformat()]
