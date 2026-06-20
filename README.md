# FF Weekly Start/Sit

A command-line tool that tells you **who to start and who to sit** each week. It
blends two signals:

- **ECR (#4)** — FantasyPros Expert Consensus Rankings, the robust "wisdom of the
  crowd" backbone.
- **Vegas (#5)** — implied team totals derived from betting spreads + totals (via
  The Odds API), used to nudge players in better/worse scoring environments.

It then **flags the close calls** — when the two signals disagree or the top
options are within a hair of each other — instead of pretending it knows.

This is a v1 deliberately shaped as the skeleton for an **ensemble +
self-calibration** system (#7): signals are pluggable, blend weights are
configurable, and every decision is logged so a future learner can re-weight the
inputs from your real outcomes.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env   # then edit .env
```

## Configure (`.env`)

| Variable | Required | Notes |
|---|---|---|
| `SLEEPER_USERNAME` | yes | Roster is pulled from Sleeper (free, no auth). |
| `SLEEPER_LEAGUE_ID` | no | Defaults to your first NFL league this season. |
| `ODDS_API_KEY` | no | Free key from [the-odds-api.com](https://the-odds-api.com/). Without it, the app runs on ECR alone and labels Vegas "unavailable". |
| `FANTASYPROS_API_KEY` | no | Tried first; otherwise the app scrapes the public rankings page. |
| `FF_SCORING` | no | `ppr` (default), `half`, or `std`. |
| `FF_WEIGHT_ECR` / `FF_WEIGHT_VEGAS` | no | Blend weights (default 0.75 / 0.25). |
| `FF_CLOSE_CALL_THRESHOLD` | no | 0–100 gap under which a matchup is "too close to call" (default 5). |

## Use

```bash
ffstartsit sync                              # cache your roster + player metadata
ffstartsit rank --pos RB                     # rank your RBs for the current week
ffstartsit rank --pos WR --week 5 --csv out.csv
ffstartsit compare "Player A" "Player B"     # head-to-head, with close-call flag
ffstartsit lineup                            # best starter at each standard slot
```

Each `rank`/`compare` run appends a row to `.cache/results_log.jsonl` capturing
the candidates, every signal's raw + normalized value, the weights used, and the
pick — the data a #7 calibrator will learn from.

## How scoring works

1. Each signal returns a native value per player (ECR rank; implied team total).
2. Values are scaled to **0–100 within the candidate set** — lower ECR rank and
   higher implied total both map toward 100.
3. `final = weighted average of available signals` (a missing signal, e.g. a bye,
   is dropped and the rest re-normalized, so nobody is penalized for missing data).
4. The top two are compared: within the threshold, or signals disagreeing on the
   order → flagged **close call**.

## Architecture

```
ff_startsit/
  cli.py            entry point: sync / rank / compare / lineup
  config.py         .env-driven Settings (weights, threshold, scoring)
  models.py         Player, Game, SignalValue, PlayerScore, Recommendation
  pipeline.py       assemble signals -> fetch -> blend -> log
  sources/
    base.py         Signal ABC  <-- add new signals here (the #7 seam)
    ecr.py          FantasyPros ECR (API key + scrape fallback)
    vegas.py        The Odds API -> implied team totals
  roster/sleeper.py Sleeper username -> league -> roster -> Players
  data/
    teams.py        team-abbreviation normalization across sources
    matching.py     name/position matching of external rows onto the roster
  engine/
    normalize.py    raw -> 0..100 (pure)
    blend.py        weighted ensemble + close-call flagging (pure)
  results_log.py    append-only JSONL decision log (the #7 hook)
  output/render.py  rich table + CSV/JSON export
```

### Growing into #7 (ensemble + self-calibration)
- **Add a signal:** subclass `sources/base.py:Signal`, add it in
  `pipeline.build_signals`, give it a weight. Nothing in the engine changes.
- **Re-weight:** weights live only in `config.Settings`; a calibrator can rewrite
  them programmatically.
- **Learn:** join `results_log.jsonl` against actual weekly fantasy points to fit
  weights that work in *your* leagues. (The learner itself is future work.)

## Tests

```bash
.venv/bin/python -m pytest
```

Tests run fully offline against saved fixtures (`tests/fixtures/`): implied-total
math, ECR API + scrape parsing, name/team matching incl. unmatched players,
normalization edge cases, blend weighting + close-call/disagreement flags, Sleeper
metadata parsing, and an end-to-end pipeline run with fake signals.

> Note: live API calls require outbound network; in restricted environments run
> the unit tests (offline) and do the live end-to-end run on your own machine.
