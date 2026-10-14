"""Fallback source: ESPN's public NHL JSON feed (site.api.espn.com).

Used automatically when the NHL API is unreachable (or on demand: `backfill --source espn`).
It is unofficial/undocumented, so the NHL API stays primary. Rows from ESPN are stored with NEGATIVE
game ids (-espn_id) and are replaced by the NHL API version of the same game (same date/home/away)
as soon as the NHL API is reachable again, so nothing is double-counted.

ESPN goalie ids differ from NHL ids; goalies are mapped to known NHL ids by team + last name +
first initial, otherwise stored as -espn_id.
"""
import json
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import date, timedelta

from .ingest import toi_to_sec

BASE = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl"
SOURCE_NAME = "ESPN public NHL feed (fallback)"

# ESPN abbreviation -> NHL abbreviation (only where they differ)
ABBR = {"NJ": "NJD", "SJ": "SJS", "TB": "TBL", "LA": "LAK", "UTAH": "UTA", "UTA": "UTA", "WAS": "WSH",
        "MON": "MTL", "NAS": "NSH", "CLB": "CBJ", "VEG": "VGK", "LV": "VGK", "PHX": "ARI"}
STATE = {"pre": "FUT", "in": "LIVE", "post": "OFF"}


class EspnError(RuntimeError):
    pass


def nhl_abbr(a):
    return ABBR.get(a, a)


class EspnClient:
    def __init__(self, min_interval=0.4, retries=4, timeout=25):
        self.min_interval, self.retries, self.timeout = min_interval, retries, timeout
        self._last, self.calls = 0.0, 0

    def get(self, path):
        url = f"{BASE}/{path}"
        err = None
        for attempt in range(self.retries):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "nhl-edge-pro/0.1 (personal research)"})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    self.calls += 1
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                err = e
                if e.code == 429 or e.code >= 500:
                    time.sleep(2 ** attempt * 2)
                    continue
                raise EspnError(f"HTTP {e.code} for {url}") from e
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                err = e
                time.sleep(2 ** attempt)
        raise EspnError(f"giving up on {url}: {err}")

    def scoreboard(self, d):
        return self.get(f"scoreboard?dates={d.strftime('%Y%m%d')}&limit=50")

    def summary(self, event_id):
        return self.get(f"summary?event={event_id}")


def _last_period(comp):
    st = comp.get("status", {})
    detail = (st.get("type", {}).get("detail") or st.get("type", {}).get("shortDetail") or "").upper()
    if "SO" in detail.split("/")[-1]:
        return "SO"
    if "OT" in detail:
        return "OT"
    per = int(st.get("period") or 3)
    return "REG" if per <= 3 else "OT"


def parse_event(ev, day=None):
    """Scoreboard event -> schedule-style row (same keys as ingest.parse_schedule_game)."""
    comp = ev["competitions"][0]
    side = {c["homeAway"]: c for c in comp["competitors"]}
    h, a = side["home"], side["away"]
    year = int(ev["season"]["year"])
    stype = int(ev["season"].get("type", 2))
    state = STATE.get(comp["status"]["type"].get("state"), "FUT")
    row = {
        "game_id": -int(ev["id"]), "espn_id": int(ev["id"]), "season": (year - 1) * 10000 + year,
        "game_type": 3 if stype == 3 else 2 if stype == 2 else stype,
        "start_utc": ev["date"].replace("Z", ":00Z") if len(ev["date"]) == 17 else ev["date"],
        "game_state": state, "neutral": int(bool(comp.get("neutralSite"))),
        "home": nhl_abbr(h["team"]["abbreviation"]), "away": nhl_abbr(a["team"]["abbreviation"]),
        "home_name": h["team"].get("displayName", ""), "away_name": a["team"].get("displayName", ""),
        "home_logo": h["team"].get("logo", ""), "away_logo": a["team"].get("logo", ""),
        "home_dark_logo": h["team"].get("logo", ""), "away_dark_logo": a["team"].get("logo", ""),
        "home_score": int(h["score"]) if h.get("score") not in (None, "") else None,
        "away_score": int(a["score"]) if a.get("score") not in (None, "") else None,
        "venue": (comp.get("venue") or {}).get("fullName", ""), "date": day,
        "last_period": _last_period(comp) if state == "OFF" else None,
    }
    return row


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return s.replace(".", "").strip()


def goalie_key(team, display_name):
    parts = _norm(display_name).split()
    if not parts:
        return None
    return (team, parts[0][0], parts[-1])


def build_goalie_map(goalie_games):
    """(team, first initial, last name) -> NHL goalie id, from stored NHL rows ('I. Sorokin' style names)."""
    m = {}
    if goalie_games is None or not len(goalie_games):
        return m
    for r in goalie_games[goalie_games.goalie_id > 0][["team", "name", "goalie_id"]].drop_duplicates().itertuples(index=False):
        k = goalie_key(r.team, r.name)
        if k:
            m[k] = int(r.goalie_id)
    return m


def parse_summary(summ, sched, goalie_map):
    """ESPN summary + scoreboard row -> (game_row, goalie_rows, skater_rows) in our storage schema."""
    gid = sched["game_id"]
    shots, goalies_out, skaters_out, starters = {}, [], [], {}
    for t in summ["boxscore"]["teams"]:
        ab = nhl_abbr(t["team"]["abbreviation"])
        st = {s["name"]: s.get("displayValue") for s in t.get("statistics", [])}
        shots[ab] = int(st.get("shotsTotal") or 0)
    for p in summ["boxscore"]["players"]:
        team = nhl_abbr(p["team"]["abbreviation"])
        is_home = int(team == sched["home"])
        opp = sched["away"] if is_home else sched["home"]
        for grp in p.get("statistics", []):
            keys = grp.get("keys", [])
            for ath in grp.get("athletes", []):
                if not ath.get("stats"):
                    continue
                v = dict(zip(keys, ath["stats"]))
                name = ath["athlete"].get("displayName", "")
                if grp["name"] == "goalies":
                    toi = toi_to_sec(v.get("timeOnIce", "0:00"))
                    if toi <= 0:
                        continue
                    gk = goalie_key(team, name)
                    goalies_out.append({
                        "game_id": gid, "season": sched["season"], "date": sched["date"], "team": team, "opp": opp,
                        "is_home": is_home, "goalie_id": goalie_map.get(gk, -int(ath["athlete"]["id"])), "name": name,
                        "starter": 0, "toi_sec": toi, "shots_against": int(v.get("shotsAgainst") or 0),
                        "saves": int(v.get("saves") or 0), "goals_against": int(v.get("goalsAgainst") or 0),
                        "decision": ""})
                elif grp["name"] in ("forwards", "defenses", "skaters"):
                    g, a = int(v.get("goals") or 0), int(v.get("assists") or 0)
                    skaters_out.append({
                        "game_id": gid, "season": sched["season"], "date": sched["date"], "team": team, "opp": opp,
                        "player_id": -int(ath["athlete"]["id"]), "name": name, "pos": "D" if grp["name"] == "defenses" else "F",
                        "goals": g, "assists": a, "points": g + a, "sog": int(v.get("shotsTotal") or 0),
                        "pp_goals": None, "toi_sec": toi_to_sec(v.get("timeOnIce", "0:00"))})
    for team in (sched["home"], sched["away"]):
        mine = [x for x in goalies_out if x["team"] == team]
        if mine:
            st = max(mine, key=lambda x: x["toi_sec"])  # ESPN has no starter flag: most TOI
            st["starter"] = 1
            starters[team] = st["goalie_id"]
    game = {"game_id": gid, "season": sched["season"], "game_type": sched["game_type"], "date": sched["date"],
            "start_utc": sched["start_utc"], "home": sched["home"], "away": sched["away"],
            "home_goals": sched["home_score"], "away_goals": sched["away_score"], "last_period": sched["last_period"],
            "home_sog": shots.get(sched["home"], 0), "away_sog": shots.get(sched["away"], 0), "neutral": sched["neutral"],
            "home_starter": starters.get(sched["home"]), "away_starter": starters.get(sched["away"])}
    return game, goalies_out, skaters_out


def day_rows(client, d):
    data = client.scoreboard(d)
    rows = []
    for ev in data.get("events", []):
        try:
            r = parse_event(ev, d.isoformat())
        except (KeyError, ValueError, IndexError):
            continue
        if r["game_type"] in (2, 3):
            rows.append(r)
    return rows


def season_dates(season):
    y = season // 10000
    d, end = date(y, 9, 25), date(y + 1, 6, 30)
    while d <= end:
        yield d
        d += timedelta(days=1)
