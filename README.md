# NHL EDGE PRO

A quantitative NHL betting **research** platform: calibrated game probabilities, fair prices, expected value against real sportsbook prices, walk-forward validation, and a correlation-aware +600 parlay lab. Static website (GitHub Pages) + Python pipeline (GitHub Actions). No server, no database to run, no API keys in the browser.

> Nothing here guarantees profit. By default the site will say **NO QUALIFIED EDGE FOUND TODAY** until the model has *earned* it with out-of-sample closing-line value.

---

## Put it on GitHub (10 minutes)

1. **Create a repo** and push this folder:
   ```bash
   git init && git add . && git commit -m "NHL EDGE PRO"
   git branch -M main
   git remote add origin https://github.com/<you>/nhl-edge-pro.git
   git push -u origin main
   ```
2. **Enable Pages:** Settings → Pages → *Deploy from a branch* → `main` / `/docs`. Your site: `https://<you>.github.io/nhl-edge-pro/`
3. **Allow the workflow to commit:** Settings → Actions → General → Workflow permissions → *Read and write*.
4. **Odds — pick one (or both):**
   * **Easiest:** open your site → *Odds API Key* in the sidebar → paste your key. Odds load in your browser (cached 15 min). The key is stored only in that browser, never in the repo.
   * **For the validation record:** also add the key as a GitHub Secret (below). Only pipeline-fetched odds are archived and scored against closing lines, and parlays use them.

   GitHub Secret: get a key at [the-odds-api.com](https://the-odds-api.com) (free tier = 500 credits/month), then Settings → Secrets and variables → Actions → New secret `ODDS_API_KEY`.
5. **Load history:** Actions → *NHL EDGE PRO pipeline* → Run workflow → mode `backfill` (≈1–1.5 h, ~11 seasons of box scores; resumable if it times out — just run it again).
6. **Validate:** run the workflow with mode `backtest` (a few minutes).
7. Done. Scheduled runs (4×/day + weekly backtest) keep everything current.

Until step 5 finishes the live site shows a "no pipeline output" page. To see every page working right away, open **`/?demo=1`** — DEMO MODE runs the real pipeline on a fictional league with synthetic odds and is bannered everywhere.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q tests                 # 28 tests: odds math, probability engine, MC vs analytic, parsing, leakage
python -m pipeline.run backfill           # optional: --start 20152016 --end 20262027
python -m pipeline.run backtest
ODDS_API_KEY=xxxx python -m pipeline.run daily
python -m pipeline.run demo               # synthetic end-to-end run -> docs/data/demo/
python -m http.server -d docs 8000        # open http://localhost:8000  (or /?demo=1)
```

## Data sources

| Role | Source | Notes |
|---|---|---|
| Primary | NHL API (`api-web.nhle.com`) | League's own feed: schedule, scores, OT/SO, shots, goalie starters, skater logs |
| Automatic fallback | ESPN public NHL feed (`site.api.espn.com`) | Used when the NHL API fails (daily or backfill). Unofficial/undocumented. Starters inferred from time on ice; no skater power-play goals. ESPN rows get negative ids and are replaced by the NHL version of the same game once the NHL API is back — never double-counted |
| Odds | The Odds API | GitHub Secret (archived, used for validation) and/or a key typed into the site (browser only) |

Force the fallback for history: `python -m pipeline.run backfill --source espn`.

## Architecture

```
GitHub Actions (cron)                                        GitHub Pages
┌───────────────────────────────────────────────────┐        ┌───────────────────────┐
│ pipeline/ingest.py    NHL API → data/*.csv        │        │ docs/index.html       │
│ pipeline/odds_api.py  The Odds API → data/odds/   │  JSON  │ docs/assets/app.js    │
│ pipeline/engine.py    fit + predict w/ uncertainty│ ─────► │ (reads docs/data/*.json│
│ pipeline/edges.py     EV, no-vig, gate            │        │  never invents data)  │
│ pipeline/parlays.py   +600 parlay search          │        └───────────────────────┘
│ pipeline/backtest.py  walk-forward validation     │
│ pipeline/market_eval  CLV, tiers, track record    │
│ pipeline/site.py      → docs/data/*.json          │
└───────────────────────────────────────────────────┘
```

**Storage** (committed CSV, diff-friendly): `games.csv`, `goalie_games.csv`, `skaters_<season>.csv.gz`, `odds/<date>.csv` (every price ever fetched), `predictions_log.csv` (locked at puck drop), `edge_log.csv`, `parlay_log.csv`, `backtest_results.json`.

**Site data contract** (`docs/data/`): `meta`, `slate`, `edges`, `parlays`, `teams`, `goalies`, `backtest`, `market_eval`, `odds_history`, `model_params` (.json). Every timestamp and source is carried through to the UI.

## The model

* **Regulation goals:** Poisson regression per team-game — attack, defence, home ice, back-to-back (own and opponent), opposing goalie rating. Recency-weighted (half-life tuned, default 120 d) and ridge-shrunk toward league average (no hot-streak over-reaction). Fit by Newton/IRLS; Laplace covariance → posterior draws.
* **Scoring correlation & overdispersion:** shared gamma "pace" factor φ, integrated exactly by Gauss–Laguerre quadrature.
* **Empty net:** fraction θ of one-goal regulation results become two-goal results — estimated from data.
* **Regulation ≠ final:** ties go to OT/shootout (playoffs: OT only) with data-estimated P(shootout | tie), home OT/SO rates and a strength slope. The winner gets +1 goal, exactly how full-game totals and puck lines settle. A final is never a tie.
* **Goalies:** shrunk, time-decayed save % → `log((1−sv)/(1−sv_league))` feature. It's GSA**A** (vs average), not GSA**x** — the NHL box score has no shot quality. Starters are **PROJECTED** from usage + rest unless you add a sourced confirmation in `config/goalie_overrides.json`. Every game shows the effect of the other goalie starting.
* **Shots:** same machinery fit on shots on goal (projected SOG).
* **Uncertainty:** 200 posterior draws → 80% bands, and **pessimistic EV (10th percentile)** used for ranking.
* **Monte Carlo:** seeded simulator with identical assumptions (tests assert it matches the analytic engine); the Simulation Center re-implements it in the browser.

## Expected value

```
EV (per 1u)        = p_win × (decimal − 1) − p_loss       (push returns stake)
Break-even p_win   = (1 − p_push) / decimal
Probability edge   = p_win − break-even                    (percentage points)
No-vig             = multiplicative over both sides at one book, median across books
                     (no precise no-vig claimed when the other side isn't offered)
+120 at 50%  →  0.50×1.20 − 0.50 = +10.0%
```

## Validation tiers (what "qualified" means)

| Tier | Requirement |
|---|---|
| EXPERIMENTAL | default |
| BEATS_BASELINE | walk-forward backtest beats rolling empirical baseline, 95% date-block bootstrap CI above 0 |
| MARKET_COMPETITIVE | ≥300 live games with log loss no worse than the closing no-vig market (ML) / non-negative CLV (others) |
| EDGE_SUPPORTED | ≥200 logged candidates whose average closing-line value has a 95% CI above 0 |

`edges.gate_tier` (default `EDGE_SUPPORTED`) decides what can be called a qualified edge. Everything below the gate is shown as an **unvalidated discrepancy** and logged so the evidence accumulates honestly. Change thresholds in `config/settings.json`.

**Backtest design:** burn-in season (train only) → tuning seasons (choose half-life & ridge) → test seasons (reported only). Model refit before every game date on prior games only; goalies are the rule-projected starters (actual starters are reported separately as a hindsight bound). Market benchmark and historical ROI are computed **only** if you supply real licensed historical odds as `data/historical_odds.csv` (see `config/historical_odds_TEMPLATE.csv`).

## Parlay Intelligence Lab

Legs must individually have positive EV (no weak legs added to hit a target), one leg per game, priced at a single book. Joint probability is averaged over posterior draws (legs share parameter uncertainty), pushes reduce the payout, and the naive product is shown for comparison. Ranked by pessimistic EV; buckets +600–999 / +1000–1499 / +1500–2499 / +2500+. Same-game parlays are **not priced**: books quote them with their own correlation adjustment and the odds feed does not supply those quotes. Every surfaced parlay is logged and settled for a track record that includes the selection-bias check (realized vs expected hits).

## What's not available (shown as unavailable, never estimated)

Injuries, lineups and starter confirmations (no licensed feed connected) · xG/GSAx/Corsi/danger chances (MoneyPuck, Natural Stat Trick, Evolving-Hockey — check their terms before integrating) · first-period, team-total, alternate and player-prop odds (need the per-event odds endpoint, extra credits) · true opening lines · travel/time-zone effects.

## What remains (next modules)

1. **Player props** (deliberately deferred until the game models prove themselves): skater logs are already being collected. Plan: NB shot model × projected TOI × opponent suppression; goals via shrunk shooting talent × goalie; saves via projected opponent SOG.
2. Per-event odds (first period, team totals, alternates, props) behind a credit budget.
3. Licensed advanced-stats feed → xG-based team ratings and true GSAx.
4. Injury/lineup/starter feeds → automatic recalculation on changes (the goalie-swap machinery is already in place).
5. Push/email alerts (browser alert rules exist on the Parlay page today).

## Responsible use

This is a research tool. Model probabilities have real error, sportsbook closing lines are hard to beat, and staking formulas assume your probabilities are right. Never increase stakes to chase losses. If gambling stops being fun: 1-800-GAMBLER.
