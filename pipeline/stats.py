"""Descriptive team and goalie statistics for the research pages (all from stored box scores)."""
import numpy as np
import pandas as pd

from .goalies import ordinal

UNAVAILABLE_TEAM = ("Expected goals (xGF/xGA), Corsi/Fenwick, high-/medium-danger chances, slot/rush/rebound chances, "
                    "zone time, faceoff zone splits and power-play opportunities are not in the NHL box score feed and "
                    "are shown nowhere rather than estimated. Add a licensed advanced-stats source to enable them.")


def team_game_rows(games, skaters=None):
    g = games
    home = pd.DataFrame({"team": g.home, "opp": g.away, "date": g.date, "season": g.season, "is_home": 1,
                         "gf": g.home_goals, "ga": g.away_goals, "sf": g.home_sog, "sa": g.away_sog, "game_id": g.game_id})
    away = pd.DataFrame({"team": g.away, "opp": g.home, "date": g.date, "season": g.season, "is_home": 0,
                         "gf": g.away_goals, "ga": g.home_goals, "sf": g.away_sog, "sa": g.home_sog, "game_id": g.game_id})
    t = pd.concat([home, away], ignore_index=True)
    if skaters is not None and len(skaters):
        pp = skaters.groupby(["game_id", "team"])["pp_goals"].sum(min_count=1).rename("ppg").reset_index()
        t = t.merge(pp, on=["game_id", "team"], how="left")
    else:
        t["ppg"] = np.nan
    return t.sort_values(["date", "game_id"])


def _agg(df):
    if not len(df):
        return {"gp": 0}
    sf, sa, gf, ga = df.sf.sum(), df.sa.sum(), df.gf.sum(), df.ga.sum()
    sh = gf / sf if sf else np.nan
    sv = 1 - ga / sa if sa else np.nan
    return {"gp": int(len(df)), "w": int((df.gf > df.ga).sum()), "gf": gf / len(df), "ga": ga / len(df), "sf": sf / len(df), "sa": sa / len(df),
            "sf_pct": sf / (sf + sa) if sf + sa else None, "sh_pct": sh, "sv_pct": sv, "pdo": sh + sv,
            "ppg": float(df.ppg.mean()) if df.ppg.notna().any() else None}


def teams_json(games, skaters, ratings, name_logo, current_season):
    t = team_game_rows(games, skaters)
    out = []
    rmap = {r["team"]: r for r in ratings}
    teams = sorted(set(t.team[t.season == current_season]) or set(t.team[t.season == t.season.max()]))
    prev = current_season - 10001
    for team in teams:
        d = t[t.team == team]
        cur = d[d.season == current_season]
        if not len(cur):
            cur = d[d.season == d.season.max()]
        w = {f"L{n}": _agg(d.tail(n)) for n in (3, 5, 10, 15, 20)}
        w["season"] = _agg(cur)
        w["prev_season"] = _agg(d[d.season == prev])
        w["home"] = _agg(cur[cur.is_home == 1])
        w["away"] = _agg(cur[cur.is_home == 0])
        nl = name_logo.get(team, {})
        r = rmap.get(team, {})
        out.append({"abbr": team, "name": nl.get("name", team), "logo": nl.get("logo", ""),
                    "rating": {k: r.get(k) for k in ("att", "att_sd", "def", "def_sd")}, "windows": w})
    return {"teams": out, "unavailable_note": UNAVAILABLE_TEAM}


def goalies_json(goalie_games, tracker, today_ord, current_season):
    gg = goalie_games.copy()
    if not len(gg):
        return {"goalies": [], "league_sv": None, "season": current_season}
    gg["ord"] = [ordinal(d) for d in gg.date.astype(str)]
    cur = gg[gg.season == current_season]
    if not len(cur):
        current_season = int(gg.season.max())
        cur = gg[gg.season == current_season]
    lg = 1 - cur.goals_against.sum() / max(cur.shots_against.sum(), 1)
    out = []
    for gid, d in cur.groupby("goalie_id"):
        allg = gg[gg.goalie_id == gid].sort_values("ord")
        starts = allg[allg.starter == 1]
        sa, ga, toi = d.shots_against.sum(), d.goals_against.sum(), d.toi_sec.sum()
        gsaa = (sa - ga) - sa * lg

        def last(n):
            s = starts.tail(n)
            return {"sv_pct": 1 - s.goals_against.sum() / s.shots_against.sum()} if s.shots_against.sum() else None
        sv_r, _, _ = tracker.rating(int(gid), today_ord)
        last_ord = int(allg.ord.max())
        out.append({
            "id": int(gid), "name": d.name.iloc[-1], "team": d.sort_values("ord").team.iloc[-1],
            "season": {"gp": int(len(d)), "gs": int(d.starter.sum()), "sv_pct": 1 - ga / sa if sa else None,
                       "gaa": ga * 3600 / toi if toi else None, "gsaa": gsaa, "gsaa60": gsaa * 3600 / toi if toi else None,
                       "sa_per_start": float(d[d.starter == 1].shots_against.mean()) if d.starter.sum() else None},
            "l3": last(3), "l5": last(5), "l10": last(10),
            "career": {"sv_pct": 1 - allg.goals_against.sum() / allg.shots_against.sum() if allg.shots_against.sum() else None,
                       "gp": int(len(allg))},
            "rating_sv": sv_r, "last_start": allg.date.iloc[-1],
            "rest_days": max(0, today_ord - last_ord - 1) if today_ord > last_ord else 0,
            "starts_last7": int(((today_ord - starts.ord) <= 7).sum())})
    out.sort(key=lambda x: -x["season"]["gs"])
    return {"goalies": out, "league_sv": lg, "season": current_season}
