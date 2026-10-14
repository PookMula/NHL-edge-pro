"""DEMO MODE: a synthetic league run through the REAL pipeline end to end.

Fictional teams (no real names/logos), a generative process that is deliberately DIFFERENT from the
model's own assumptions (shots x shooting/save talent, not team-goal Poissons), and synthetic sportsbook
prices = true probability + noise + vig. Output goes to docs/data/demo/ and is flagged mode="demo";
the site shows a DEMO banner and never treats it as live.
"""
import shutil
from datetime import date, datetime, time, timedelta, timezone

import numpy as np
import pandas as pd

from .config import ROOT, iso, write_json
from .odds_math import decimal_to_american
from .storage import GAME_COLS, GOALIE_COLS, Store

CITIES = ["Northport", "Ashford", "Kingsbay", "Redmere", "Silverton", "Larkfield", "Coldwater", "Brightwell",
          "Eastvale", "Harrowgate", "Ironridge", "Westhaven", "Stonebridge", "Fairmont", "Glenrock", "Duskwood",
          "Highmoor", "Lakeshire", "Pinecrest", "Riverton", "Summit Bay", "Thornbury", "Valecross", "Wolfsburrow",
          "Amberly", "Brookhollow", "Cedarport", "Driftwood", "Elmstead", "Frostholm", "Granitefall", "Hollowmere"]
NICK = ["Pilots", "Owls", "Comets", "Foxes", "Rams", "Herons", "Lynx", "Storm", "Anchors", "Ravens", "Bison", "Gulls",
        "Pines", "Wolves", "Hawks", "Otters", "Kodiaks", "Sabres", "Falcons", "Mariners", "Badgers", "Vipers", "Tides",
        "Cougars", "Ospreys", "Moose", "Stags", "Knights", "Puffins", "Yetis", "Barons", "Wardens"]
BOOKS = [("demobook_a", "DemoBook A"), ("demobook_b", "DemoBook B"), ("demobook_c", "DemoBook C")]


class Truth:
    def __init__(self, rng):
        n = 32
        self.abbr = [f"D{i + 1:02d}" for i in range(n)]
        self.name = {a: f"{CITIES[i]} {NICK[i]}" for i, a in enumerate(self.abbr)}
        self.shot_for = rng.normal(0, 0.07, n)
        self.shot_against = rng.normal(0, 0.07, n)
        self.finish = rng.normal(0, 0.07, n)
        self.goalies = {a: [(9_000_000 + i * 10 + k, rng.normal(0.906, 0.007)) for k in range(2)] for i, a in enumerate(self.abbr)}
        self.gname = {gid: f"G. Demo{gid % 1000:03d}" for a in self.abbr for gid, _ in self.goalies[a]}
        self.idx = {a: i for i, a in enumerate(self.abbr)}

    def rates(self, h, a, b2b_h, b2b_a, gh, ga, neutral=0):
        hi, ai = self.idx[h], self.idx[a]
        sh = 29.5 * np.exp(self.shot_for[hi] - self.shot_against[ai] + 0.035 * (1 - neutral) - 0.04 * b2b_h + 0.03 * b2b_a)
        sa = 29.5 * np.exp(self.shot_for[ai] - self.shot_against[hi] - 0.04 * b2b_a + 0.03 * b2b_h)
        ph = (1 - ga) * np.exp(self.finish[hi])   # home goal prob per shot vs away goalie sv
        pa = (1 - gh) * np.exp(self.finish[ai])
        return sh, sa, ph, pa

    def sim(self, rng, h, a, b2b_h, b2b_a, gh, ga, n=1, neutral=0):
        sh, sa, ph, pa = self.rates(h, a, b2b_h, b2b_a, gh, ga, neutral)
        pace = rng.gamma(30, 1 / 30, n)
        s_h = rng.poisson(sh * pace)
        s_a = rng.poisson(sa * pace)
        g_h = rng.binomial(s_h, min(ph, 0.5))
        g_a = rng.binomial(s_a, min(pa, 0.5))
        en = rng.random(n) < 0.22
        g_h = np.where(en & (g_h - g_a == 1), g_h + 1, g_h)
        g_a = np.where(en & (g_a - g_h == 1), g_a + 1, g_a)
        tie = g_h == g_a
        so = tie & (rng.random(n) < 0.38)
        d = (sh * ph - sa * pa) / (sh * ph + sa * pa)
        q = np.where(so, 0.5, 0.52 + 0.6 * d)
        hw = rng.random(n) < q
        fh = np.where(tie & hw, g_h + 1, g_h)
        fa = np.where(tie & ~hw, g_a + 1, g_a)
        last = np.where(~tie, "REG", np.where(so, "SO", "OT"))
        return fh, fa, last, s_h, s_a


def _season_days(y):
    d0, d1 = date(y, 10, 8), date(y + 1, 4, 14)
    return [d0 + timedelta(days=k) for k in range((d1 - d0).days + 1) if k % 7 != 3]


def generate(rng, truth, store, today):
    games, goalies = [], []
    last_played = {}
    starter_streak = {}
    gid = 2022020000
    seasons = [(2022, None), (2023, None), (2024, None), (2025, None), (2026, today)]
    for y, stop in seasons:
        days = _season_days(y)
        if stop:
            days = [d for d in days if d < stop]
        sid = y * 10000 + y + 1
        for d in days:
            teams = list(rng.permutation(truth.abbr))[:int(rng.integers(10, 19))]
            for k in range(0, len(teams) - 1, 2):
                h, a = teams[k], teams[k + 1]
                gid += 1
                rows = {}
                for t in (h, a):
                    b2b = last_played.get(t) == d - timedelta(days=1)
                    (g1, sv1), (g2, sv2) = truth.goalies[t]
                    st = g2 if (b2b and rng.random() < 0.8) or rng.random() < 0.15 else g1
                    rows[t] = (b2b, st, sv1 if st == g1 else sv2)
                fh, fa, last, sh, sa = truth.sim(rng, h, a, rows[h][0], rows[a][0], rows[h][2], rows[a][2])
                games.append({"game_id": gid, "season": sid, "game_type": 2, "date": d.isoformat(),
                              "start_utc": f"{d.isoformat()}T23:00:00Z", "home": h, "away": a,
                              "home_goals": int(fh[0]), "away_goals": int(fa[0]), "last_period": str(last[0]),
                              "home_sog": int(sh[0]), "away_sog": int(sa[0]), "neutral": 0,
                              "home_starter": rows[h][1], "away_starter": rows[a][1]})
                ga_h = int(fa[0]) - (1 if last[0] in ("OT", "SO") and fa[0] > fh[0] else 0)
                ga_a = int(fh[0]) - (1 if last[0] in ("OT", "SO") and fh[0] > fa[0] else 0)
                for t, opp, is_home, shots, ga in ((h, a, 1, int(sa[0]), ga_h), (a, h, 0, int(sh[0]), ga_a)):
                    goalies.append({"game_id": gid, "season": sid, "date": d.isoformat(), "team": t, "opp": opp,
                                    "is_home": is_home, "goalie_id": rows[t][1], "name": truth.gname[rows[t][1]],
                                    "starter": 1, "toi_sec": 3600, "shots_against": shots,
                                    "saves": max(0, shots - min(ga, shots)), "goals_against": min(ga, shots),
                                    "decision": ""})
                last_played[h] = last_played[a] = d
    store.write("games.csv", pd.DataFrame(games, columns=GAME_COLS))
    store.write("goalie_games.csv", pd.DataFrame(goalies, columns=GOALIE_COLS))
    return games, last_played


def market_prices(rng, p_true, vig=0.045, noise=0.03):
    """Two-way prices from a noisy 'market' belief plus vig, per book."""
    lo = np.log(p_true / (1 - p_true)) + rng.normal(0, noise * 2)
    pm = 1 / (1 + np.exp(-lo))
    out = []
    for _ in BOOKS:
        pb = np.clip(pm + rng.normal(0, 0.008), 0.02, 0.98)
        a, b = pb * (1 + vig / 2), (1 - pb) * (1 + vig / 2)
        out.append((round(decimal_to_american(1 / a)), round(decimal_to_american(1 / b))))
    return out


def true_markets(rng, truth, h, a, b2b_h, b2b_a, sv_h, sv_a, n=4000):
    fh, fa, last, _, _ = truth.sim(rng, h, a, b2b_h, b2b_a, sv_h, sv_a, n)
    m, t = fh - fa, fh + fa
    return {"ml": float(np.mean(m > 0)), "hm15": float(np.mean(m >= 2)), "am15": float(np.mean(m <= -2)),
            "o55": float(np.mean(t > 5.5)), "o65": float(np.mean(t > 6.5))}


def run_demo(settings, log=print):
    from .backtest import run_backtest
    from .site import build_site
    rng = np.random.default_rng(42)
    data_dir = ROOT / ".demo_data"
    if data_dir.exists():
        shutil.rmtree(data_dir)
    store = Store(data_dir)
    truth = Truth(rng)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    today = (now + timedelta(days=1)).date()
    log("demo: generating synthetic league")
    games, last_played = generate(rng, truth, store, today)
    g = store.games()
    log(f"demo: {len(g)} synthetic games")

    # historical odds for the test season (synthetic market) -> exercises market benchmark + ROI
    test_season = sorted(g.season.unique())[-2]
    ho = []
    for r in g[g.season == test_season].itertuples(index=False):
        tm = true_markets(rng, truth, r.home, r.away, 0, 0, 0.906, 0.906, 600)
        p = market_prices(rng, np.clip(tm["ml"], 0.05, 0.95))[0]
        ho.append({"date": r.date, "home": r.home, "away": r.away, "home_ml": p[0], "away_ml": p[1]})
    pd.DataFrame(ho).to_csv(store.path("historical_odds.csv"), index=False)

    s = dict(settings)
    s["backtest"] = dict(settings["backtest"], grid_half_life_days=[90, 180], grid_ridge=[10, 30], bootstrap_reps=300,
                         burn_in_seasons=1, tuning_seasons=1)
    s["model"] = dict(settings["model"], posterior_draws=120)
    log("demo: walk-forward backtest (reduced grid)")
    games_bt = g[g.season < sorted(g.season.unique())[-1]]
    bt = run_backtest(games_bt, store.goalie_games(), s, pd.read_csv(store.path("historical_odds.csv")), log)
    write_json(store.path("backtest_results.json"), bt)

    # today's slate + synthetic odds
    teams = list(rng.permutation(truth.abbr))[:16]
    slate, odds_rows = [], []
    base = datetime.combine(today, time(23, 0), tzinfo=timezone.utc)
    for k in range(8):
        h, a = teams[2 * k], teams[2 * k + 1]
        start = base + timedelta(minutes=30 * (k % 4))
        gid = 2026029000 + k
        slate.append({"game_id": gid, "season": 20262027, "game_type": 2, "start_utc": iso(start), "game_state": "FUT",
                      "neutral": 0, "home": h, "away": a, "home_name": truth.name[h], "away_name": truth.name[a],
                      "home_logo": "", "away_logo": "", "venue": f"{truth.name[h].rsplit(' ', 1)[0]} Arena (demo)",
                      "date": today.isoformat()})
        b2b_h = float(last_played.get(h) == today - timedelta(days=1))
        b2b_a = float(last_played.get(a) == today - timedelta(days=1))
        tm = true_markets(rng, truth, h, a, b2b_h, b2b_a, truth.goalies[h][0][1], truth.goalies[a][0][1])
        fetched = [now - timedelta(hours=6), now - timedelta(hours=3), now - timedelta(minutes=20)]
        for fi, ft in enumerate(fetched):
            for bi, (bk, bt_) in enumerate(BOOKS):
                def add(market, side, point, price):
                    odds_rows.append({"fetched_at": iso(ft), "event_id": f"demo{gid}", "commence_utc": iso(start), "home": h,
                                      "away": a, "book": bk, "book_title": bt_, "market": market, "side": side,
                                      "point": point, "price": float(price), "last_update": iso(ft)})
                ml = market_prices(rng, np.clip(tm["ml"], .05, .95))[bi]
                add("h2h", "home", None, ml[0]); add("h2h", "away", None, ml[1])
                pl = market_prices(rng, np.clip(tm["hm15"], .05, .95))[bi]
                add("spreads", "home", -1.5, pl[0]); add("spreads", "away", 1.5, pl[1])
                line = 6.5 if tm["o55"] > 0.62 else 5.5
                tt = market_prices(rng, np.clip(tm["o65" if line == 6.5 else "o55"], .05, .95))[bi]
                add("totals", "over", line, tt[0]); add("totals", "under", line, tt[1])
    from .odds_api import archive
    archive(odds_rows, data_dir, today.isoformat())
    latest = [r for r in odds_rows if r["fetched_at"] == iso(now - timedelta(minutes=20))]
    overrides = {}
    t0 = slate[0]["home"]
    overrides[t0] = {"goalie_id": truth.goalies[t0][0][0], "status": "CONFIRMED", "source": "DEMO synthetic confirmation"}
    statuses = {"nhl_fetched_at": iso(now), "odds_fetched_at": iso(now - timedelta(minutes=20)), "odds_source": "DEMO synthetic odds",
                "sources": [{"name": "DEMO synthetic league", "short": "DEMO", "status": "ok", "fetched_at": iso(now),
                             "used_for": "everything on this page", "detail": "fictional teams, games and goalies"},
                            {"name": "DEMO synthetic odds", "short": "ODDS", "status": "ok",
                             "fetched_at": iso(now - timedelta(minutes=20)), "used_for": "prices",
                             "detail": "true probability + noise + 4.5% vig, 3 fake books"}]}
    out = ROOT / settings["site_data_dir"] / "demo"
    build_site(store, slate, latest, statuses, s, out, now, today, "demo", overrides, log)
    log(f"demo: done -> {out}")
