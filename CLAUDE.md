# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`ffstartsit` is a CLI that tells you who to start/sit each fantasy week by blending
signals (ECR + Vegas + injury + weather) into a normalized ensemble score, and — its core
product promise — **flags the close calls instead of faking confidence**. It is
built as an ensemble + self-calibration system; the code and docs refer to that
design as **"#7"** throughout (the Signal seam, the results log, and the
`calibrate` command are all "the #7 hook"). Treat `#7` as a design term, not a
dangling issue reference.

## Dev commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # puts `ffstartsit` on PATH; installs pytest
cp .env.example .env             # then edit; the app reads .env at startup

.venv/bin/python -m pytest                        # full suite (fully offline)
.venv/bin/python -m pytest tests/test_engine.py   # one file
.venv/bin/python -m pytest -k close_call          # by name substring
```

Run the CLI as `ffstartsit <cmd>` (venv active), `.venv/bin/ffstartsit <cmd>`, or
`python -m ff_startsit <cmd>`. CI (`.github/workflows/ci.yml`) runs `pytest` on
Python 3.10/3.11/3.12; keep changes compatible with 3.10.

**Tests are offline by design** — they run against saved fixtures in
`tests/fixtures/`, never live APIs. Anything that hits the network (ECR/Vegas/
Sleeper/Discord) must be injectable or mockable so the test stays offline; follow
the existing pattern (e.g. `cmd_calibrate`'s `outcome_provider` parameter, fake
signals in `test_pipeline.py`).

## Architecture

Data flows: **roster provider → pipeline → signals → engine → recommendation →
output/log**. Two ABC "seams" keep the platform and the data sources swappable
without touching the pure engine.

- **`sources/base.py:Signal`** — the primary extension seam. A signal fetches a
  native `raw` value per player and declares `higher_is_better`. To add one:
  subclass `Signal`, register it in `pipeline.build_signals`, and give it a weight
  in **four** places that must stay in sync — the defaults in `config.load_settings`
  and `Settings.weights`, the `FF_WEIGHT_*` env parsing, and `_validate_weights`.
  Nothing in `engine/` changes.
- **`roster/base.py:RosterProvider`** — swappable roster source (espn/sleeper/
  manual), selected by `cli.build_roster_provider` (precedence: `--source` flag >
  `FF_ROSTER_SOURCE` > espn). Every provider returns canonical `Player` objects.
- **`engine/`** (`normalize.py`, `blend.py`) — **pure functions only**, no I/O.
  `normalize.to_0_100` scales each signal within the candidate set; `blend`
  weight-averages the normalized scores. `weighted_final` is the single source of
  truth shared by the live blend and the calibrator.

### Invariants to preserve

- **`Player.key` is the Sleeper player id** and the join key every signal returns
  values against. Signals/outcomes match on this id (ESPN/manual rosters fall back
  to name+position via `data/matching.py`), never on raw names.
- **Graceful degradation, never a crash.** A missing signal for a player (bye,
  unmatched, disabled) is dropped and remaining weights re-normalize — the player
  is not penalized. `pipeline.recommend` catches any signal `fetch` exception and
  marks it unavailable rather than failing the run. `publish`/`notify` likewise
  warn-and-continue on Discord failure.
- **Fail loud-but-graceful on bad config.** Invalid weights (negative / all-zero),
  bad thresholds, and corrupt learned-weights files fall back to defaults with a
  warning (`config._validate_weights`, `_warn`) — they never silently produce an
  all-`None` blend.
- **`config.Settings` is the sole owner of blend weights.** Weight precedence:
  hardcoded defaults < `learned_weights.json` (written by `calibrate --write`) <
  explicit `FF_WEIGHT_*` env. Don't read weights from anywhere else.
- **Close-call flagging is the product**, not decoration. `blend._flag_close_call`
  flags when the top two finals are within `close_call_threshold` OR when any
  signal ranks the runner-up above the leader. Preserve both conditions.

### The self-calibration loop (#7)

Every `rank`/`compare` run appends a row to `.cache/results_log.jsonl`
(`results_log.py`) capturing candidates, each signal's raw + normalized value, the
weights, and the pick. `calibrate` (`calibrate/`) reads that log back
(`log_reader.py`), joins it to **actual** weekly points from the free Sleeper
stats endpoint (`outcomes.py`), and grid-searches the weight simplex
(`learner.py`) by pairwise ranking concordance — **re-blending the logged
`normalized` values, never re-fetching**. It refuses to `--write` on thin data
(`--min-pairs`) or when current weights already tie the grid best.

`backtest` (`calibrate/backtest.py`) is the read-only companion: it replays each
logged decision under **the weights that run actually used** (`Decision.weights`),
joins the pick to the same Sleeper outcomes, and reports top-pick hit-rate plus a
**confident-vs-close-call hit-rate split** — the honesty check on close-call
flagging. It reuses `weighted_final`, the `OutcomeProvider` seam, and `load_decisions`;
it never writes weights.

### Output & delivery

`output/` renders the same `Recommendation` to a rich table, markdown, CSV/JSON
(`render.py`), a self-contained HTML dashboard (`html.py`), and a Discord webhook
payload (`discord.py`). `report.py` builds whole-roster digests and the shared
lineup builder. `publish` does one scoring pass and fans out to all three outputs
(this is what the weekly GitHub Action runs). `chatops.py` parses `/rank RB`-style
issue comments into CLI argv for the Actions bot.
