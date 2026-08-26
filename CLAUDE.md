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
  to name+position via `data/matching.py`), never on raw names. **Team defenses are
  the exception**: every source names them differently (ESPN `Chiefs D/ST`, Sleeper
  `Kansas City`, FantasyPros `Kansas City Chiefs` under position `DST`, not `DEF`),
  so `player_match_key` keys them on the canonical team abbreviation via
  `normalize_team` and folds `DEF`/`DST` into one position. Don't reintroduce a
  name-based DEF key — the name never matched, which silently cost the DEF slot
  ECR's 0.60 of blend weight.
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
  explicit `FF_WEIGHT_*` env. Don't read weights from anywhere else. A learned
  file *replaces* the weight set rather than patching it (`_apply_learned`):
  `calibrate --write` only writes the signals it observed, so merging that subset
  into the defaults left the un-learned defaults standing and delivered a learned
  70/30 as 57/25. A signal the file never names goes to 0; one it names with an
  unusable value keeps its default, since that is a corrupt entry, not a verdict.
- **Close-call flagging is the product**, not decoration. `blend._flag_close_call`
  flags when the top two finals are within `close_call_threshold` OR when a
  signal ranks the runner-up above the leader. Preserve both conditions. The
  disagreement condition is deliberately *qualified*, not unconditional: the
  signal must carry at least `min_disagree_weight` (`FF_MIN_DISAGREE_WEIGHT`,
  default 0.15) of total blend weight, and the normalized gap must be at least
  `close_call_threshold`. Without those floors a 0.10-weight — or 0-weight —
  signal flips the flag as readily as ECR, and a flag that fires on everything
  is not a warning.

### The self-calibration loop (#7)

Every `rank`/`compare` run appends a row to `.cache/results_log.jsonl`
(`results_log.py`) capturing candidates, each signal's raw + normalized value, the
weights, and the pick. `publish`/`report` do the same under an explicit `--log`;
whole-roster passes stay opt-in because a scheduled run scores every position
every week and would otherwise swamp the corpus with decisions nobody acted on.
`calibrate` (`calibrate/`) reads that log back
(`log_reader.py`), joins it to **actual** weekly points from the free Sleeper
stats endpoint (`outcomes.py`), and grid-searches the weight simplex
(`learner.py`) by pairwise ranking concordance — **re-blending the logged
`normalized` values, never re-fetching**. It refuses to `--write` when current
weights already tie the grid best, or when the corpus is too thin.

**"Thin" is three floors, not one** (`--min-pairs` 30, `--min-decisions` 5,
`--min-weeks` 3), because pairs alone cannot tell one week from ten: a single
nine-player ranking yields 36 correlated pairs off one slate, one injury report,
one weather system, and used to clear a pairs-only gate on its own. `min_weeks`
is the floor carrying the meaning — a `publish --log` pass adds decisions fast but
only ever one week at a time. `CalibrationResult.shortfalls` names which floor
fell short, since "gather more data" doesn't say what to gather. This matters
more than it looks: because a learned file now *replaces* the weight set, a
premature `--write` zeroes every signal the log happened not to contain rather
than merely diluting it.

`dedupe_decisions` (`log_reader.py`) collapses rows sharing `(season, week,
scoring)` and the same candidate keys — the Thursday and Sunday passes over one
week are one piece of evidence, not two — keeping the latest, since a repeat run
has fresher injury and weather reads. Both `calibrate` and `backtest` apply it;
duplicates would otherwise inflate the write gate and the honesty numbers alike.

**Three kinds of run are never logged**, all for the same reason — the row would
not mean what it claims, and the log is append-only: preseason sample runs
(`pipeline.py`), the pooled FLEX pass (`rank_pooled`), and any run where a signal
served a week other than the one requested. That last one is `ECRSignal.
served_wrong_week`: the keyless scrape has no week selector, so `--week 5` returns
current-week ranks. Show the ranking, warn, withhold the row — a warning the user
scrolls past is enough for a table they're reading now, not for a row that would
mislead every future calibration.

The corpus survives scheduled runs on a `calibration-data` orphan branch
(`weekly-report.yml`), since `.cache/` dies with the runner. Note
`results_log.jsonl` is gitignored as cache everywhere else, so that push needs
`git add -f` — without it the step silently succeeds and persists nothing. Nothing
runs `calibrate --write` from CI; fitting weights on a cron is what the floors
above exist to prevent.

`backtest` (`calibrate/backtest.py`) is the read-only companion: it replays each
logged decision under **the weights that run actually used** (`Decision.weights`),
joins the pick to the same Sleeper outcomes, and reports top-pick hit-rate plus a
**confident-vs-close-call hit-rate split** — the honesty check on close-call
flagging. It reuses `weighted_final`, the `OutcomeProvider` seam, and `load_decisions`;
it never writes weights.

`sources/experts.py` is a **setup helper, not a signal** — it resolves analyst
names to the FantasyPros expert ids `FF_PREFERRED_EXPERTS` wants. It is split by
reliability on purpose: *discovery* (`ExpertFinder.find`) reads an id off the
per-expert page whose slug is the analyst's name, so it is markup-dependent and
returns `None` rather than a guess, because a wrong-but-valid id returns a real
ranking and would label another analyst's numbers with your journalist's name.
*Verification* (`verify_experts`) parses no markup at all — it re-fetches through
`ecr.fetch_scrape_rows` and compares rankings numerically, which is what catches
that mislabel case, plus a dead id and an ignored `filters` parameter. Only
discovery can tie a number to a name, and the command says so.

### Game context (schedule)

`sources/schedule.py:ScheduleProvider` resolves the week's fixtures once (keyless
ESPN scoreboard) and is **shared** by every signal needing game context — it is
not a `Signal`, has no blend weight, and is exempt from the "four places" rule.
`venue_for` decides where a game is played: the feed's per-game `indoor` flag
wins, then a known neutral-site venue by name, then the home team's stadium, then
`None`. It returns `None` rather than guessing, because a wrong forecast presented
as fact is worse than a missing signal — a missing one just re-weights the rest.

`WeatherSignal` therefore scores *games*, not teams: both sides of a matchup share
one forecast and one lookup, read at the actual kickoff hour (`timezone=UTC` end
to end, so there is no local-time or DST arithmetic anywhere). With no schedule,
no kickoff, an unknown venue, or a forecast that doesn't reach the game, it is
unavailable. There is deliberately **no** fallback to "the windiest day in the
horizon" — that invented risk from weather unrelated to the game.

`VegasSignal` uses the same provider to filter events: the odds endpoint takes no
week parameter and returns every upcoming game, so once next week's lines post a
team appears twice. `games_for_week` matches on the (home, away) pair, falling
back to the kickoff window and then to `implied_totals_by_team`'s
first-occurrence-wins, which given the API's kickoff ordering keeps the sooner
game. Unlike weather, Vegas still works without a schedule — it just filters less
precisely.

### The lineup builder and the FLEX slot

`report.score_week` does one scoring pass per roster: `rank_each_position` ranks
each position group, and `rank_flex_pool` ranks all RB/WR/TE candidates **in one
candidate set** using FantasyPros' cross-position FLEX list (`ECRSignal.pooled()`,
pseudo-position `FLX`). That pooled pass is what makes the FLEX pick valid —
`normalize.to_0_100` is min-max *within* the candidate set, so per-position scores
put every position's leader at 100 and are not comparable across positions.

Three rules hold here. The pooled rec is **never** folded into `recs` (the Discord
renderer emits an alert per `recs` entry and would duplicate them). It is **never**
logged (the calibrator scores pairwise concordance within one decision, so it would
double-weight those players). And it is **refused** below `MIN_FLEX_ECR_COVERAGE`
— without ECR the pooled blend runs on Vegas/injury/weather alone, which is a worse
pick than the fallback and an invisible one. On refusal `build_lineup` degrades to
comparing per-position scores and says so via `Lineup.caveat`, which every renderer
surfaces.

`report.rank_pooled` is the shared implementation of that pass, because `compare`
faces the identical problem: a mixed RB/WR/TE comparison scored per-position puts
each position's leader at 100 and is meaningless. So `compare` pools when every
candidate is flex-eligible, and **refuses** otherwise (QB vs RB) or when the
pooled ranking fails its coverage guard — there is no honest fallback, and the
per-position blend would answer with a number that doesn't mean what it says.

### The waiver/trade pass (`waivers/`)

The Tuesday-evening companion to the start/sit pass: `ffstartsit waivers` (and
`.github/workflows/waivers.yml`) suggests adds, drops, bids, stashes, bye-week
holes and concrete trades, per league. Five rules hold, each of which is the
reason a piece of it is shaped the way it is.

- **It adds no `Signal`, and therefore no weight.** The "four places" rule above
  does not apply to it and `_validate_weights` is untouched — no start/sit blend
  changes. The preferred journalists' ranks and the writers' column mentions are
  an *annotation* layer, exactly as `sources/journalists.py` is.
- **Nothing here is ever written to `results_log.jsonl`.** A waiver row would
  pack a hundred players — most of them on other people's teams — into a single
  "decision", and the calibrator scores pairwise concordance *within* one
  decision; it would also count toward the `calibrate --write` floors with
  evidence nobody acted on. Every `recommend` call passes `log=False`, the
  `waivers` command has no `--log` flag, and `waivers.yml` has none of
  `weekly-report.yml`'s calibration-data steps.
- **Free agents are scored in the *same* candidate set as your roster.**
  `score.score_positions` runs one blend pass per position over your roster,
  *every other team's* roster, and the free-agent pool together. `normalize.
  to_0_100` is min-max within the candidate set, so a pool scored on its own puts
  its best player at 100 whoever he is — "he beats your WR4" is only a true
  sentence when both were normalized together. Same trap `rank_pooled` solves for
  FLEX. It is also why `trades.py` needs no scoring pass of its own.
- **A missing ECR is not a bad ECR.** FantasyPros ranks 40-75 per position; most
  of a waiver pool is below that line and a bye-week player falls off it
  entirely. Without ECR the blend runs on injury alone and returns a healthy
  anonymous backup looking excellent — which recommended adding him and dropping
  your bye-week RB1. So `score.has_ecr` gates both adds and drops; everything
  else is reported as unranked rather than ranked last.
- **Preseason is refused, not filled.** `pipeline.build_signals` serves bundled
  sample values before Week 1 so a start/sit *table* has something to
  demonstrate with. Dealt to a real roster those values name real players to
  claim and real players to cut — a Discord message once opened with "Nick Folk
  (K) — drop Malik Nabers" in August. So `build_bundle` takes a `preseason` flag
  (injectable like `build_signals`'s) and returns an empty bundle carrying
  `season.WAIVER_BANNER`, before any provider call, so the refusal also costs no
  requests. `WaiverBundle.banner` is deliberately **not** `caveat`: caveat means
  "here is what this report could not see", and the two must stay
  distinguishable to a renderer that colors on either.

Relatedly, `SleeperClient.current_week` reads `/state/nfl`'s `season_type`
before its `week`: that endpoint counts *preseason* weeks through August, so
`week: 3` there is the third preseason game, not Week 3 of the season, and
reporting it labeled a whole report off the wrong calendar. Only a regular-season
reading is a fantasy week; everything else defers to `season.date_week`.

`waivers/base.py:LeagueViewProvider` is a **second, optional ABC**, not new
abstract methods on `RosterProvider` — a manual CSV has no league behind it and
never will. `ESPNProvider` and `SleeperProvider` implement both; `cli.
_waiver_bundles` probes with `isinstance` and skips a source that can't see a
pool. ESPN's existing `mRoster`+`mTeam` response already carried every team's
roster (the old `parse_roster` discarded it), so trades cost no extra request;
only the free-agent pool needs a second call (`kona_player_info` +
`x-fantasy-filter`). Sleeper has no free-agent endpoint at all — its pool is the
cached `/players/nfl` blob minus every rostered id, ordered by `search_rank`.

Two guards that look redundant and are not: `droppable`'s `protected` carries
`report.build_lineup`'s **actual** FLEX pick, so `keep_counts` must *not* also
reserve a spare at every flex position — doing both held three bodies back for
one slot and made nothing droppable. And `trades.suggest_trades` is deliberately
called **without** `protected`: offers are drawn from surplus only, so no
starting slot can be traded away, and passing a lineup's worth of protected keys
blocked every idea (surplus depth *is* the FLEX).

`columns.py` (CBS for Eisenberg/Richard, Yahoo for Boone) inverts the usual
scrape: it searches the article for names **already in the league's free-agent
pool**, rather than parsing each site's structure. A layout change costs quotes,
not correctness, and an invented name can't survive because it isn't in the pool.
Team defenses are excluded outright — a defense is named after its team, and
prose says team names constantly. One `ColumnFetcher` is shared across every
league in a run and memoizes per `(author, week)`, including failures.

Both scheduled workflows publish the **whole** `./site` (index.html +
waivers.html) and share `concurrency: group: ff-startsit-pages`. A Pages deploy
replaces the site wholesale, so two workflows deploying their own page would each
silently delete the other's.

### Output & delivery

`output/` renders the same `Recommendation` to a rich table, markdown, CSV/JSON
(`render.py`), a self-contained HTML dashboard (`html.py`), and a Discord webhook
payload (`discord.py`). `report.py` builds whole-roster digests and the shared
lineup builder. `publish` does one scoring pass and fans out to all three outputs
(this is what the weekly GitHub Action runs). The waiver pass renders the same
three ways from `WaiverBundle` (`waivers/render.py`, `html.build_waivers_html`,
`discord.build_waiver_payload`), reusing the Discord budgeting helpers so a
multi-league message still fits Discord's 10-embed / 6000-char limits.
`chatops.py` parses `/rank RB`-style issue comments into CLI argv for the Actions
bot (`/waivers` and `/waivers all` included).
