"""Command-line entry point.

  python -m pipeline.run backfill     # historical box scores (resumable; ~1h for 10+ seasons)
  python -m pipeline.run daily        # new results + today's slate + odds -> refit -> docs/data/*.json
  python -m pipeline.run backtest     # walk-forward validation -> data/backtest_results.json (+ site rebuild)
  python -m pipeline.run demo         # synthetic end-to-end run -> docs/data/demo/ (DEMO MODE only)
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from .config import ROOT, iso, load_settings, utcnow, write_json
from .ingest import Ingestor, today_et
from .nhl_api import NHLApiError, NHLClient
from .storage import Store


def log(*a):
    print(*a, flush=True)


def load_overrides(today):
    p = ROOT / "config" / "goalie_overrides.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text()).get(today.isoformat(), {})


def cmd_backfill(settings, store, args):
    ing = Ingestor(NHLClient(), store, log)
    start = args.start or settings["backfill_start_season"]
    if args.source == "espn":
        from .espn import EspnClient
        ing.backfill_espn(EspnClient(), start, args.end)
    else:
        try:
            ing.backfill(start, args.end)
        except NHLApiError as e:
            log("NHL API failed during backfill:", e, "-> continuing with ESPN fallback")
            from .espn import EspnClient
            ing.backfill_espn(EspnClient(), start, args.end)
    log(f"backfill done: {len(ing.games)} games stored, {len(ing.errors)} errors")
    for e in ing.errors[:20]:
        log("  error:", e)


def cmd_daily(settings, store, args):
    from . import odds_api
    from .site import build_site
    now = utcnow()
    today = today_et()
    statuses = {"sources": []}
    client = NHLClient()
    ing = Ingestor(client, store, log)
    try:
        slate = ing.incremental(today)
        statuses["nhl_fetched_at"] = iso(now)
        statuses["sources"].append({"name": "NHL API (api-web.nhle.com)", "short": "NHL", "status": "ok",
                                    "fetched_at": iso(now), "used_for": "schedule, results, box scores, goalies, skaters",
                                    "detail": f"{client.calls} calls; {len(ing.errors)} box-score errors"})
        store.path("slate_cache.json").write_text(json.dumps({"date": today.isoformat(), "rows": slate}))
    except NHLApiError as e:
        log("NHL API failed:", e, "-> trying ESPN fallback")
        statuses["sources"].append({"name": "NHL API (api-web.nhle.com)", "short": "NHL", "status": "error",
                                    "fetched_at": None, "detail": str(e)[:200]})
        from . import espn
        ec = espn.EspnClient()
        try:
            slate = ing.incremental_espn(ec, today)
            statuses["nhl_fetched_at"] = iso(now)
            statuses["sources"].append({"name": espn.SOURCE_NAME, "short": "ESPN", "status": "ok", "fetched_at": iso(now),
                                        "used_for": "schedule, results, shots, goalies (NHL API unavailable)",
                                        "detail": f"{ec.calls} calls; goalie starters inferred from time on ice"})
            store.path("slate_cache.json").write_text(json.dumps({"date": today.isoformat(), "rows": slate}))
        except espn.EspnError as e2:
            log("ESPN fallback failed too:", e2)
            statuses["sources"].append({"name": espn.SOURCE_NAME, "short": "ESPN", "status": "error", "detail": str(e2)[:200]})
            cache = store.path("slate_cache.json")
            c = json.loads(cache.read_text()) if cache.exists() else {}
            slate = c.get("rows", []) if c.get("date") == today.isoformat() else []
    odds_rows = []
    key = odds_api.get_key()
    os_ = settings["odds"]
    if not key:
        statuses["odds_note"] = "ODDS_API_KEY not configured — no sportsbook prices, so no EV is computed."
        statuses["sources"].append({"name": odds_api.SOURCE_NAME, "short": "ODDS", "status": "missing_key",
                                    "used_for": "sportsbook prices", "detail": statuses["odds_note"]})
    elif not slate:
        statuses["sources"].append({"name": odds_api.SOURCE_NAME, "short": "ODDS", "status": "skipped",
                                    "detail": "no games today"})
    else:
        try:
            events, usage = odds_api.fetch_odds(key, os_["sport_key"], os_["regions"], os_["markets"], os_["bookmakers"])
            lut = odds_api.build_name_lookup(slate)
            odds_rows, unmatched = odds_api.normalize(events, iso(now), lut)
            odds_api.archive(odds_rows, store.dir, today.isoformat())
            statuses["odds_fetched_at"] = iso(now)
            statuses["odds_source"] = odds_api.SOURCE_NAME
            statuses["sources"].append({"name": odds_api.SOURCE_NAME, "short": "ODDS", "status": "ok", "fetched_at": iso(now),
                                        "used_for": "moneyline, puck line, totals prices",
                                        "detail": f"{len(odds_rows)} prices; credits remaining {usage.get('remaining')}"
                                                  + (f"; unmatched: {', '.join(unmatched)}" if unmatched else "")})
        except odds_api.OddsApiError as e:
            statuses["odds_note"] = f"Odds feed error: {e}"
            statuses["sources"].append({"name": odds_api.SOURCE_NAME, "short": "ODDS", "status": "error", "detail": str(e)[:200]})
    if not len(store.games()):
        log("No historical games stored — run backfill first. Writing status-only meta.")
        write_json(ROOT / settings["site_data_dir"] / "meta.json", {"mode": "live", "generated_at": iso(now),
                   "sources": statuses["sources"], "model": {"status": "NO DATA"}, "unavailable": []})
        return
    build_site(store, slate, odds_rows, statuses, settings, ROOT / settings["site_data_dir"], now, today, "live",
               load_overrides(today), log)


def cmd_backtest(settings, store, args):
    from .backtest import run_backtest
    games, gg = store.games(), store.goalie_games()
    ho = store.path("historical_odds.csv")
    hist_odds = pd.read_csv(ho) if ho.exists() else None
    res = run_backtest(games, gg, settings, hist_odds, log)
    write_json(store.path("backtest_results.json"), res)
    log("backtest:", json.dumps({k: res.get(k) for k in ("available", "n_test_games", "test_period", "hyper", "runtime", "reason")}))


def cmd_demo(settings, store, args):
    from .demo import run_demo
    run_demo(settings, log)


def main(argv=None):
    ap = argparse.ArgumentParser(description="NHL EDGE PRO pipeline")
    ap.add_argument("mode", choices=["backfill", "daily", "backtest", "demo"])
    ap.add_argument("--start", type=int, help="first season for backfill, e.g. 20152016")
    ap.add_argument("--end", type=int, help="last season for backfill")
    ap.add_argument("--source", choices=["nhl", "espn"], default="nhl",
                    help="backfill source (default nhl, automatic ESPN fallback if the NHL API fails)")
    args = ap.parse_args(argv)
    settings = load_settings()
    store = Store(ROOT / settings["data_dir"])
    {"backfill": cmd_backfill, "daily": cmd_daily, "backtest": cmd_backtest, "demo": cmd_demo}[args.mode](settings, store, args)


if __name__ == "__main__":
    sys.exit(main())
