"""Sportsbook odds via The Odds API (https://the-odds-api.com) — an authorized, documented odds aggregator.

Requires a key (free tier: 500 credits/month; each call costs (#markets x #regions) credits).
The key is read from the ODDS_API_KEY environment variable / GitHub Secret, never shipped to the browser.

Every fetch is archived to data/odds/<date>.csv so line movement, closing lines and closing-line value can
be computed later from prices that really existed. Nothing is ever back-filled or invented.
"""
import csv
import json
import os
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://api.the-odds-api.com/v4"
SOURCE_NAME = "The Odds API (the-odds-api.com)"
ODDS_COLS = ["fetched_at", "event_id", "commence_utc", "home", "away", "book", "book_title",
             "market", "side", "point", "price", "last_update"]

ALIASES = {"utah hockey club": "UTA", "utah mammoth": "UTA", "arizona coyotes": "ARI",
           "montreal canadiens": "MTL", "st louis blues": "STL", "st. louis blues": "STL"}


class OddsApiError(RuntimeError):
    pass


def norm(name):
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    return " ".join(s.replace(".", "").split())


def build_name_lookup(slate_rows):
    lut = {norm(k): v for k, v in ALIASES.items()}
    for r in slate_rows:
        lut[norm(r["home_name"])] = r["home"]
        lut[norm(r["away_name"])] = r["away"]
    return lut


def fetch_odds(api_key, sport="icehockey_nhl", regions="us", markets="h2h,spreads,totals", bookmakers=""):
    q = {"apiKey": api_key, "regions": regions, "markets": markets, "oddsFormat": "american", "dateFormat": "iso"}
    if bookmakers:
        q["bookmakers"] = bookmakers
        q.pop("regions")
    url = f"{BASE}/sports/{sport}/odds?{urllib.parse.urlencode(q)}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=30) as r:
            body = json.loads(r.read().decode())
            usage = {"remaining": r.headers.get("x-requests-remaining"), "used": r.headers.get("x-requests-used")}
            return body, usage
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="ignore")[:300]
        raise OddsApiError(f"HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise OddsApiError(f"network error: {e}") from e


def normalize(events, fetched_at, lut):
    rows, unmatched = [], set()
    for ev in events:
        h, a = lut.get(norm(ev["home_team"])), lut.get(norm(ev["away_team"]))
        if not h or not a:
            unmatched.add(f'{ev["away_team"]} @ {ev["home_team"]}')
            continue
        for bk in ev.get("bookmakers", []):
            for mk in bk.get("markets", []):
                key = mk["key"]
                if key not in ("h2h", "spreads", "totals"):
                    continue
                for o in mk.get("outcomes", []):
                    if key == "totals":
                        side = o["name"].lower()
                    else:
                        side = "home" if norm(o["name"]) == norm(ev["home_team"]) else "away"
                    rows.append({"fetched_at": fetched_at, "event_id": ev["id"], "commence_utc": ev["commence_time"],
                                 "home": h, "away": a, "book": bk["key"], "book_title": bk.get("title", bk["key"]),
                                 "market": key, "side": side,
                                 "point": float(o["point"]) if o.get("point") is not None else None,
                                 "price": float(o["price"]), "last_update": mk.get("last_update") or bk.get("last_update")})
    return rows, sorted(unmatched)


def archive(rows, data_dir, date_str):
    p = Path(data_dir) / "odds" / f"{date_str}.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    new = not p.exists()
    with p.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ODDS_COLS)
        if new:
            w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in ODDS_COLS})
    return p


def get_key():
    k = os.environ.get("ODDS_API_KEY", "").strip()
    return k or None


# ------------------------------------------------------------------ player props (per-event endpoint)
PROP_COLS = ["fetched_at", "event_id", "game_id", "home", "away", "book", "book_title", "stat", "market",
             "player", "side", "point", "price", "last_update"]


def fetch_event_props(api_key, event_id, markets, sport="icehockey_nhl", regions="us", bookmakers=""):
    """Cost = (# markets returned) x (# regions). Returns (event json, usage headers)."""
    q = {"apiKey": api_key, "regions": regions, "markets": ",".join(markets), "oddsFormat": "american", "dateFormat": "iso"}
    if bookmakers:
        q["bookmakers"] = bookmakers
        q.pop("regions")
    url = f"{BASE}/sports/{sport}/events/{event_id}/odds?{urllib.parse.urlencode(q)}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=30) as r:
            return json.loads(r.read().decode()), {"remaining": r.headers.get("x-requests-remaining"),
                                                   "last": r.headers.get("x-requests-last")}
    except urllib.error.HTTPError as e:
        raise OddsApiError(f"HTTP {e.code}: {e.read().decode(errors='ignore')[:300]}") from e
    except urllib.error.URLError as e:
        raise OddsApiError(f"network error: {e}") from e


def normalize_props(ev, fetched_at, game_id, home, away):
    from .props import PROP_MARKETS
    rows = []
    for bk in ev.get("bookmakers", []):
        for mk in bk.get("markets", []):
            stat = PROP_MARKETS.get(mk["key"])
            if not stat:
                continue
            for o in mk.get("outcomes", []):
                nm = str(o.get("name", "")).lower()
                if nm in ("over", "under"):
                    side, player, point = nm, o.get("description"), o.get("point")
                elif nm in ("yes", "no"):          # anytime goal scorer: Yes = over 0.5 goals
                    side, player, point = ("over" if nm == "yes" else "under"), o.get("description"), 0.5
                else:                               # some books put the player in `name` for Yes-only markets
                    side, player, point = "over", o.get("name"), o.get("point", 0.5)
                if player is None or point is None or o.get("price") is None:
                    continue
                rows.append({"fetched_at": fetched_at, "event_id": ev.get("id"), "game_id": game_id, "home": home,
                             "away": away, "book": bk["key"], "book_title": bk.get("title", bk["key"]), "stat": stat,
                             "market": mk["key"], "player": player, "side": side, "point": float(point),
                             "price": float(o["price"]), "last_update": mk.get("last_update")})
    return rows


def archive_props(rows, data_dir, date_str):
    p = Path(data_dir) / "prop_odds" / f"{date_str}.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    new = not p.exists()
    with p.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PROP_COLS)
        if new:
            w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in PROP_COLS})


def latest_props(data_dir, date_str):
    """Latest snapshot per game from today's prop archive (list of dicts)."""
    import pandas as pd
    p = Path(data_dir) / "prop_odds" / f"{date_str}.csv"
    if not p.exists():
        return []
    df = pd.read_csv(p)
    if not len(df):
        return []
    last = df.groupby("game_id").fetched_at.transform("max")
    df = df[df.fetched_at == last]
    return df.to_dict("records")
