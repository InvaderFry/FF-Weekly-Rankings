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
Python 3.10 through 3.14; keep changes compatible with 3.10.

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
- **Every disk cache goes through `cache.py`, both directions.** A cache is an
  optimization, so no cache operation may be what fails a run — and the two ways
  it can be are easy to hand-roll wrong, which is why the helpers exist rather
  than the four-line idiom at each call site. `atomic_write_text` names its temp
  file with `mkstemp` **per process**: the obvious `path.name + ".tmp"` gives
  every writer the same path, so two commands sharing one `FF_DATA_DIR` race and
  the loser raises `FileNotFoundError` out of an otherwise valid command.
  `read_json_or_none` treats a corrupt file as a miss, because a write
  interrupted by Ctrl-C or a full disk leaves a truncated file, and an unguarded
  `json.loads` then raises on *every* later run until someone finds and deletes
  it by hand — a transient interruption turned into an outage that outlives its
  own TTL (the Sleeper players blob caches for 24h). Don't reintroduce a bare
  `write_text`/`json.loads` pair for cached data.
- **Fail loud-but-graceful on bad config.** Invalid weights (negative / all-zero),
  bad thresholds, and corrupt learned-weights files fall back to defaults with a
  warning (`config._validate_weights`, `_warn`) — they never silently produce an
  all-`None` blend. **`load_settings` must never raise**, and
  `_apply_scoring_overrides` is why that is stated rather than assumed:
  `FF_LEAGUE_SCORING` used to `raise ValueError` on an entry naming a league
  that isn't configured. That variable exists precisely so the non-sensitive
  half of a league's config can be edited without touching the secret carrying
  its ids — in the workflows one is a repository *variable* and the other a
  *secret*, edited on different screens — so drift between them is the expected
  state, and a rename took every command in all three workflows down on a
  traceback before it read a single ranking. Entries are independent: a bad one
  warns, names the configured leagues, and leaves that league's scoring alone.
- **`config.Settings` is the sole owner of blend weights.** Weight precedence:
  hardcoded defaults < `learned_weights.json` (written by `calibrate --write`) <
  explicit `FF_WEIGHT_*` env. Don't read weights from anywhere else. A learned
  file *replaces* the weight set rather than patching it (`_apply_learned`):
  `calibrate --write` only writes the signals it observed, so merging that subset
  into the defaults left the un-learned defaults standing and delivered a learned
  70/30 as 57/25. A signal the file never names goes to 0; one it names with an
  unusable value keeps its default, since that is a corrupt entry, not a verdict.
- **Close-call flagging is the product**, not decoration. `blend._flag_close_call`
  flags on three conditions and all three must be preserved: the top two finals
  are within `close_call_threshold`; OR a signal ranks the runner-up above the
  leader; OR nothing that carries weight separates the two in its own **raw**
  units (`_flag_raw_dead_heat`). The disagreement condition is deliberately
  *qualified*, not unconditional: the signal must carry at least
  `min_disagree_weight` (`FF_MIN_DISAGREE_WEIGHT`, default 0.15) of total blend
  weight, and the normalized gap must be at least `close_call_threshold`. Without
  those floors a 0.10-weight — or 0-weight — signal flips the flag as readily as
  ECR, and a flag that fires on everything is not a warning.

  The third condition exists because the first two are blind exactly where the
  product needs them most. `normalize.to_0_100` is min-max *within* the candidate
  set and has no minimum-span floor, so with two candidates any nonzero difference
  becomes 0-vs-100: ECR 12.0 against 12.1 renders as a 100-point blowout, and
  `close_call_threshold`, living in that same normalized space, can never trip.
  `compare` is the two-player command. So `close_call_raw_gaps`
  (`FF_CLOSE_RAW_GAP_ECR` 3.0 ranks, `FF_CLOSE_RAW_GAP_VEGAS` 1.5 implied points)
  reads the raw values instead — the only scale that knows a tenth of a rank from
  twenty. It is deliberately **unanimous**, not any-of: a signal with a real
  separation vetoes the flag, because that is an edge and flagging it would be the
  false alarm the other floors exist to prevent. A signal absent from the mapping
  (injury) is a bucketed status with no meaningful continuous scale and
  abstains — it can neither flag nor veto. Weather carries a gap too
  (`FF_CLOSE_RAW_GAP_WEATHER`, 12.0 points of its own 0-100 conditions score) but
  it exists for `flat_signals` below, not for this method, and it is named in
  **`Settings.presentational_gaps`** — the list of signals barred from voting
  here whatever their weight. That list is the point: weather's exclusion used to
  fall out of the weight floor alone, 0.10 against a 0.15 `min_disagree_weight`,
  which made a coincidence between two independently configurable numbers
  load-bearing. Raising `FF_WEIGHT_WEATHER` or lowering `FF_MIN_DISAGREE_WEIGHT`
  silently promoted it to a voter, and the dangerous direction is the **veto**,
  not the flag: this function returns the moment one voter reads a real
  separation, so a 12-point gap on a scale quantized to 5-point steps would
  suppress the warning entirely on the strength of two forecasts one step apart.
  Naming it means the gap can be tuned for the note it actually drives without
  moving what the flag does. Do *not* "fix" this in `normalize.to_0_100` instead: the
  calibrator re-blends logged `normalized` values through `weighted_final`, so
  changing the transform makes every historical row in `results_log.jsonl`
  unreplayable.

  Two more findings from a 2026-09-09 review of live Week 1 output shaped this
  further, both worth not rediscovering. First, `_flag_close_call`'s
  disagreement loop used to let `disagree_exempt` (injury) skip the weight floor
  entirely, so *any* Questionable-vs-healthy split on the top two tripped the
  flag regardless of whether anything else backed it up — 5 of 9 multi-candidate
  groups flagged in one run, most not real coin flips. The exemption now only
  changes what an exempt-but-under-floor disagreement *does*: it still appends
  its note (so it reaches `results_log.jsonl` for later analysis), but it no
  longer sets `close_call` on its own. A signal that also clears the real weight
  floor is untouched — this only demotes the exemption's own bypass. The
  designation itself is never hidden: it is already on the player's own `flags`,
  rendered in every table regardless of `close_call`, and reaches Discord's
  starter alerts whenever that player is the pick. Second, the flag's first
  condition only ever compared the top two overall — rank 1 vs 2 — but in a
  league starting N at a position, the decision that actually sets the lineup is
  rank N vs N+1. `_flag_close_call` now takes an optional `starter_count` and
  additionally evaluates that boundary pair with its own wording ("Last starting
  spot is a coin flip: X vs Y"). Purely additive to the engine: `weighted_final`
  is untouched, so logged rows stay replayable, and every other caller
  (`compare`, the waiver pass) passes no `starter_count` and is unaffected.

  Two things about that boundary check are load-bearing. It runs **both**
  conditions the top two get, the raw one included — the normalized threshold
  alone would reproduce the exact blindness this whole section exists to cover,
  since `to_0_100` is min-max *within the candidate set* and so whether the
  boundary pair reads as close depends on the spread of the whole group rather
  than on the two players. Four receivers at ECR 18 / 20 / 20.5 / 21 put the
  WR2/WR3 boundary — half a rank apart, the tightest call on the roster — 16.7
  normalized points apart and silent. And `starter_count` comes from
  **`report.starter_counts(slots)`**, derived from the slot list actually in use
  rather than hardcoded beside it, threaded through `score_week` and
  `rank_each_position`. `waivers.build._lineup_keys` is why: computing one half
  of a guard from the hardcoded template while the other half read the league's
  real `roster_slots` left a superflex league's second quarterback both
  unprotected and surplus. The consequence here is milder — the count only
  decides which *pair* is examined, so a mismatch misses a warning rather than
  cutting a starter — but a second copy of the template is exactly how that bug
  started. The start/sit path has no league-rules fetch and keeps the default
  (2 RB, 2 WR, 1 each of QB/TE/K/DEF), as the rest of that path already does;
  a caller that knows the league's real slots passes them to `score_week` and
  `build_lineup` together and a 3-WR league checks WR3/WR4.

  The same min-max blindness has a second, purely *presentational* consequence,
  and `Recommendation.flat_signals` is the answer to it. A column can look
  decisive purely because `to_0_100` stretched a trivial raw spread across the
  full range: Week 1 put the #1 back at `VEGAS 0` in two leagues, and nothing in
  the table said whether that was a real fade or half an implied point. So a
  signal that carries a configured raw gap, reads a raw value for at least two
  *scored* candidates, and spans no more than that gap across all of them gets a
  "read with care" line under the table (`render.flat_signal_note`, mirrored in
  `html.py`). It reuses `close_call_raw_gaps` rather than adding a threshold, and
  it asks about the whole candidate set rather than the top two — the dead-heat
  condition already owns that pair. Injury has no configured gap and abstains
  here exactly as it does in `_flag_raw_dead_heat`.

  Weather's own raw values used to make this worse than a blind spot: a 2026
  review found `weather.score_conditions` returning a continuous value rounded
  to 2dp, which contradicts the "bucketed status" premise the flag logic relies
  on to justify weather's abstention there — and, unguarded by a raw gap here,
  let a few mph of forecast noise (Caleb Williams 93.7 vs Ja'Marr Chase 95.5, both
  clear football weather) blend into a ~29-point spread with no warning. The fix
  is two-sided: `score_conditions` now quantizes its result to a 5-point step, so
  near-identical forecasts produce an *identical* raw value and stop moving
  `final` at all rather than moving it by up to weather's full blend weight; and
  weather gained the `close_call_raw_gaps` entry described above so that when a
  real (unquantized-away) spread remains, this method can still call it out.
  Both halves route around `normalize.to_0_100` on purpose — see above.

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

Rows carry a **`league`** (`Settings.league_label`, written per league by `cli`),
but it is provenance and deliberately **not** part of that identity tuple. Two
leagues at the same scoring putting the same players against each other in one
week saw one slate, one injury report, one weather system — counting them twice
is the exact inflation dedupe exists to prevent. Season is still inferred from the
row timestamp (`season_from_ts`); `league` is stored because the log is
append-only, so a field not written is one no later analysis can recover.

The label reaches the log through the **per-league `Settings` copy**, and that
copy is now made *unconditionally* in `cli._league_context`, `_league_bundles` and
`_waiver_bundles`. It used to be `lsettings = settings` unless a league's scoring
differed from the global — fine while scoring was the only per-league field, and
wrong the moment a second one rides along: every same-scoring league would share
one `Settings` object and log whichever label was written last.

**Four kinds of run are never logged**, all for the same reason — the row would
not mean what it claims, and the log is append-only: preseason sample runs
(`pipeline.py`), the pooled FLEX pass (`rank_pooled`), any run where a signal
served a week other than the one requested, and a lone candidate
(`Recommendation.unranked`). The week mismatch is `ECRSignal.
served_wrong_week`: the keyless scrape has no week selector, so `--week 5` returns
current-week ranks. Show the ranking, warn, withhold the row — a warning the user
scrolls past is enough for a table they're reading now, not for a row that would
mislead every future calibration.

The lone-candidate case is the one where it is worth being precise about *what*
goes wrong, because the obvious answer is wrong. Both consumers already refuse
these rows on their own — `learner.join_outcomes` and `backtest` each require two
joined candidates — so they never inflated `--min-decisions` or the hit-rate, and
`tests/test_calibrate_floors.py` pins that, including for the 30-of-62 such rows
already sitting on the `calibration-data` branch, which no later guard can
retract. Not logging them is about the **corpus meaning what it says**: a log
whose row count is twice its evidence misleads the human reading it to decide
whether there is enough data to calibrate on. Don't "fix" the old rows by
rewriting that branch — the readers already handle them, and the log is
append-only on purpose.

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
names to the FantasyPros expert ids `FF_PREFERRED_EXPERTS` wants.

*Discovery* (`ExpertFinder.directory`) reads `expertGroupsData.expert_data` off
the ranking page `ecr.py` already fetches: a JSON array of every expert the site
can serve, each `{"id", "name", "site"}`. Extraction is bracket-matched
(`_json_array_at`), not regexed — the array holds 80-odd objects whose strings
contain brackets, and `.*?` stops at the first `]` inside an image URL. It
replaced a name-as-URL-slug scheme that read an id off
`/nfl/rankings/<name>-consensus-rankings.php`: that went quiet when the markup
moved, and because every failure path returns `None` rather than a guess, the
command degraded into printing manual instructions and nothing else. Prefer a
*ranking* page over a staff directory — an expert listed there is by
construction one whose ranks can be asked for.

The directory is also what makes three failures distinguishable, and they render
to a user as the same missing section: **a dead id** (absent from the
directory — no key and no retry will help), **a mislabeled id** (present, but
another analyst's — it returns real, plausible numbers under your journalist's
byline, which no ranking comparison can catch), and **a live id this transport
cannot filter to**. That last one is the normal case: the public rankings page
applies its expert filter in the browser and serves the same consensus whatever
`filters` asks for, so per-journalist ranks need `FANTASYPROS_API_KEY`. The old
`verify_experts` called it "the id is wrong", which sent the user to re-derive
the one thing that was already correct — so `ExpertCheck.id_ok` now carries
whether the directory vouched for the id, and the *remedy* the command prints
keys off that rather than off the mere fact of failure.

Two live findings worth not rediscovering: as of the 2026 season FantasyPros
lists ~80 weekly NFL rankers (the count moves week to week, which is why the
directory is read rather than pinned), Justin Boone is 317, and **no CBS analyst
is in the list at all** — Jamey Eisenberg and Dave Richard cannot feed this section by any
id. Their Tuesday waiver *columns* come from `waivers/columns.py`, a different
scrape entirely, and are unaffected.

**Do not "solve" the CBS pair by scraping CBS's own rankings pages.** They look
like the obvious answer — `/fantasy/football/rankings/ppr/RB/jamey-eisenberg/`
exists and returns 200 — and they are a trap, measured 2026-09-08: every slug
serves *byte-identical* data. Eisenberg, Dave Richard, `consensus`, and a
`not-a-real-analyst` slug invented on the spot all return the same ranking with
the same fingerprint, because the per-analyst filtering happens client-side in
JavaScript and the server ships one consensus document to every URL. Only a
handful of rows are even server-rendered. The page also links internally to
*different* slugs (`jameye`, `drichard`) that behave the same way.

That shape fails **open**, which is what makes it worse than having no source:
a dead or misspelled slug is indistinguishable from a working one, so the
scraper would publish CBS house consensus under a named byline forever and
nothing in the response could catch it. It is the identical failure to the
`44`/`45` ids this section already removed, and the exact case `_matches_filters`
fails closed to prevent. A `columns.py`-style name-matching inversion does not
rescue it either: that trick guards against *invented* names, and here every
name is real — it is the attribution that is false. If CBS ranks are ever
wanted, the only honest route is a data endpoint that names the analyst in its
response, so the fetcher can fail closed the way both ECR transports do.

`ecr._matches_filters` is the guard underneath all of that, and it **fails
closed on both transports**, including when the response names no `filters` at
all. A response that doesn't say which experts it covers is not evidence that it
covered the one requested, and the cost is asymmetric: consensus served in place
of one analyst is real, plausible numbers under the wrong byline, while a false
negative costs one omitted section. The API path used to skip the check whenever
the key was absent (`"filters" in payload and not _matches_filters(...)`) while
the scrape path failed closed — and `JournalistFetcher._warn_if_filter_ignored`
cannot cover that gap, because it needs two experts returning identical ranks to
compare, which is exactly the single-journalist config that is left once the
dead ids are dropped.

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
no kickoff, an unknown venue, a failed fetch, or a forecast that doesn't reach the
game, it is unavailable — and each of those arms carries **its own note**
(`_compute_game`; "no schedule" and "not scheduled" come one step earlier, in
`is_available` and `fetch`). They all render as the same blank column, but they
are different things to do about it: file a stadium, wait for the schedule,
retry, or wait for the week. Week 1 showed both sides of one matchup reading "no
forecast" with nothing to say which it was — the same "distinguishable failures
rendering identically" bug `no_trades_reason` and the lone-candidate note each
fixed elsewhere. Note a roofed venue is *not* one of these: it scores
`DOME_SCORE` without a network call, so a dome never reads as a failure. There is
deliberately **no** fallback to "the windiest day in the horizon" — that invented
risk from weather unrelated to the game.

`VegasSignal` uses the same provider to filter events: the odds endpoint takes no
week parameter and returns every upcoming game, so once next week's lines post a
team appears twice. `games_for_week` matches on the (home, away) pair, falling
back to the kickoff window and then to `implied_totals_by_team`'s
first-occurrence-wins, which given the API's kickoff ordering keeps the sooner
game. Unlike weather, Vegas still works without a schedule — it just filters less
precisely.

The odds payload is the one fetch here that is **disk-cached** (`ODDS_CACHE_TTL`,
30 minutes) rather than memoized per instance, because it is the one that varies
by neither week nor league. `cli._league_bundles` builds a fresh signal set per
league and every scheduled workflow scores each league *twice* — once for its own
pass and once for the sibling page rebuild that keeps a Pages deploy from
dropping the other page — so three leagues meant six identical calls per run. The
Odds API bills **two credits per call**, one per market, against a 500/month free
tier: the duplication, not the feed, was the running cost. The TTL is short on
purpose; the only duplication it has to absorb happens minutes apart inside one
run.

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

- **Across positions, only `score.depth_ratio` is a number.** That one pass is per
  *position*, so a WR's 78 and a TE's 78 came from separate min-max populations and
  subtracting them yields something that looks like points and is not. Ordering all
  adds by `final`, ordering all drops by `final`, and pricing a FAAB bid off the
  difference all did exactly that — the best of fifteen defenses scores 100 by
  arithmetic and led the add list. `depth_ratio` is the scale-free replacement: a
  player's ECR positional rank over `starter_demand` (`team_count x starting
  slots`). Below 1.0 is a startable player, above is bench depth, and it reads the
  same for a QB as for a TE. Add ordering, `droppable` ordering, `_worth_adding`,
  bid conviction and `trades.FAIRNESS_BAND_FACTOR` all use it. `WaiverTarget.margin`
  is filled in **only** when an add and his drop share a position, where the
  subtraction is real; renderers must tolerate `None`. For the same reason the
  drop table shows **`ROS rank`, not `Score`**: `build_bundle` orders drops by
  `season_rank` and selects them on it, and a table sorted by a column it does
  not display reads as unsorted (61.7, 58.6, 72.0) — while the weekly `final`
  it used to show was a cross-position comparison of separately normalized
  sets. Renderers must tolerate a `None` rank there too. The add table had the
  identical defect and is fixed the identical way: `pick_adds` orders and gates
  on `depth_ratio`, but the table showed `final`, so AndyLOT's Week 1 adds read
  76.5, 91.9, 84.5 — visibly unsorted, because the column and the ordering
  disagreed. `WaiverTarget.depth_ratio` carries the value `pick_adds` actually
  used, and the add table shows **`Depth`** instead of `Score`; renderers must
  tolerate `None` there too, the same contract as the drop table's `ROS rank`.
  The `"ranks POSn against the N POS the league starts"` clause that used to be
  in `add_reasons`' "why" text is gone for the same reason: once the Depth
  column carries that reading numerically, restating it in prose said the same
  thing twice in one row. Trade fairness is a *multiple* (`hi / lo`), not a
  difference — a 0.3 gap separates a league-winner from a starter at ratio 0.1
  and two bench bodies at 2.0.

  Two honest limits. `depth_ratio` ranks by starter scarcity, not points over
  replacement, so within the skill positions a genuine DEF1 still outranks a
  fringe RB; fixing that properly needs projections the tool deliberately
  doesn't have. Across the streaming boundary it is patched rather than fixed:
  `pick_adds` sorts on `(position in STREAM_POSITIONS, ratio, -final)`, so
  kickers and defenses fall below every skill player. A league starts one of
  each, which makes almost every rostered K/DEF read as scarce — DEF2 scores
  2/8 = 0.25 against a useful TE17's 17/12 = 1.42 — and every league's table
  opened with a defense and a kicker above the players who decide the week. The
  key is **ordering only**: `_worth_adding`, the bid caps and the drop pairing
  are untouched, and the streamers are still listed. And `team_count` comes from
  `len(teams)` in `build_bundle`, floored by `MIN_LEAGUE_TEAMS` (4) — a partial
  team parse understates every position's demand, which reads the whole wire as
  filler and empties the report, turning an outage into "nothing worth adding".
- **A missing ECR is not a bad ECR.** FantasyPros ranks 40-75 per position; most
  of a waiver pool is below that line and a bye-week player falls off it
  entirely. Without ECR the blend runs on injury alone and returns a healthy
  anonymous backup looking excellent — which recommended adding him and dropping
  your bye-week RB1. So `score.has_ecr` gates both adds and drops; everything
  else is reported as unranked rather than ranked last.

  A note carrying a league's own numbers goes in **`WaiverBundle.league_notes`**
  and renders inside that league's section; `notes` is only for text that is
  identical across every league in the run and renders once in a shared footer.
  The footer dedupes by exact string, which is exactly what makes a per-league
  fact wrong to put there: three leagues each reporting "Season-long ranks cover
  N/M players" produced three near-identical unattributed sentences at the bottom
  of the page, interleaved with the run-wide notes and readable as belonging to
  nobody. For the same reason `sources` labels carry their scoring — the footer
  dedupes on `(label, url)`, so two scorings rendered two links reading exactly
  "FantasyPros rest-of-season rankings" side by side.

  That gate is also why an empty adds list is ambiguous, and why
  **`WaiverBundle.no_adds_reason()`** exists: `has_ecr` gates adds *and* drops, so
  an ECR outage empties the report, and all three renderers used to answer that
  with their own wording of "nothing beats anyone you could drop" — a confident
  claim about a comparison that never ran. The method is the single definition all
  three now call, the same rule `roster_by_position` follows, and it reads
  `WaiverBundle.coverage` / `pool_size` (from `score.signal_coverage`, populated on
  **every** run rather than only the rehearsal) to pick between an outage, a thin
  read that names its own thinness, and a genuinely quiet wire. `None` means a
  banner is standing and already explains the silence.

  **`no_adds_reason` only speaks when the table is *entirely* empty**, which left
  the commonest real case unexplained: a table listing a kicker and a defense and
  nothing else. Because `pick_adds` sorts streamers last, that is exactly what a
  shallow league produces, and every Week 1 league produced it — no RB or WR add,
  and no way to tell a bare wire from a bar set too high.
  **`WaiverBundle.no_adds_at_positions()`** is the per-position companion, same
  contract and same three renderers: it names each *starting* position (streamers
  excluded — a league starts one of each and their absence is not news) that
  produced no add, and separates "candidates were ranked and compared, none beat
  anyone you could drop" from "no ranked free agent was available there, so
  nothing was compared". Its denominator is `score.viable_adds_by_position`,
  which counts through `add_candidate_ratio` — the gate `pick_adds` itself
  applies, **extracted rather than copied**, because "N were compared" is only
  honest while the two agree. `STREAM_POSITIONS` moved to `waivers/models.py` for
  the same reason: `WaiverBundle` reasons about it now, and two copies would let
  a renderer and the scorer disagree about which empty rows are worth explaining.

  **`WaiverBundle.no_stashes_reason()`** is the third of these, and exists
  because `find_stashes`' gates are tight by design: a shelved player needs an
  NFL team *and* a rest-of-season rank before "stash while he's cheap" is an
  honest sentence, since this app has no return-date model and a missing ROS
  rank is not evidence anyone plays again. Tight gates empty the section on a
  normal week, and an empty stash section rendered as **nothing at all** — the
  digest and the dashboard both dropped the heading — so "nobody on the wire is
  shelved or on bye" and "four were weighed and none could be vouched for"
  arrived as the same blank space. Its denominator is `score.stash_candidates`,
  which walks the same `_stash_seen` the finder walks, extracted rather than
  copied for the same reason `add_candidate_ratio` was. Unlike the two above
  this is **two** renderers, not three: Discord has never carried a stash
  section.
- **Preseason is refused, not filled.** `pipeline.build_signals` serves bundled
  sample values before Week 1 so a start/sit *table* has something to
  demonstrate with. Dealt to a real roster those values name real players to
  claim and real players to cut — a Discord message once opened with "Nick Folk
  (K) — drop Malik Nabers" in August. So `build_bundle` takes a `preseason` flag
  (injectable like `build_signals`'s) and returns an empty bundle carrying
  `season.WAIVER_BANNER`, before any provider call, so the refusal also costs no
  requests. `WaiverBundle.banner` is deliberately **not** `caveat`: caveat means
  "here is what this report could not see", and the two must stay
  distinguishable to a renderer that colors on either. The refusal does carry one
  real thing: `WaiverBundle.roster`, your drafted team, listed under the banner by
  all three renderers. `cli._get_roster` fetched it before `build_bundle` was
  called, so it costs no request and keeps the zero-request property; it is names
  only, since the run that shows it is the one that scored nothing. Empty before
  the draft — which is exactly when there is no team to show — and populated only
  on the refusal, since an in-season or rehearsal report has real adds and drops
  and the listing would be noise.

  The **dress rehearsal** is the third path through that gate, and the reason
  the switch is "use live signals despite the calendar" rather than "run anyway":
  a run that only bypasses the refusal still gets the sample fill, which
  rehearses the demo rather than the system. So past the gate `build_bundle`
  asks `build_signals` for `preseason=False` unconditionally. It fires on request
  (`--rehearse`, a `workflow_dispatch` checkbox) or by itself inside
  `season.is_rehearsal_window` — the final 7 days before kickoff, *not* the first
  preseason week, which is late July when there are no weekly rankings to fetch
  and a run proves the least. The window is exactly one week wide so precisely
  one of `waivers.yml`'s weekly crons lands in it: one rehearsal a season, with
  no arithmetic tying the window to the schedule. Nothing is invented if the data
  isn't there yet — `score.has_ecr` already empties the report — so the banner
  carries `score.signal_coverage` counts, because an empty rehearsal must not
  read the same as a broken one. Those counts ride in the *banner*, not `notes`:
  `notes` reach the digest and the dashboard but never the Discord embed, and the
  Discord message is the thing being rehearsed.

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

`build._lineup_keys` passes the **league's** slots into `build_lineup` (which takes
an optional `slots`, defaulting to `LINEUP_SLOTS`), **flex slots included**.
Protecting against the hardcoded 1QB/2RB/2WR template while `droppable` counted
surplus against the real `roster_slots` made the two halves of one guard disagree.
The template went first; the hardcoded single `FLEX` beside it went next, because
it left the same starter unprotected by a different route. `droppable` counts
surplus against `roster_slots` alone, which has no entry a flex body can occupy —
so every flex starter is surplus by that count and `protected` is the only thing
between him and the drop list. One ordinary `FLEX` takes RB/WR/TE, so a superflex
league's second quarterback fell through it, and a two-flex league's second flex
body did too: a recommendation to cut a starter either way.

So `LeagueRules.flex_slots` (`{"FLEX": 2}`, `{"SUPER_FLEX": 1}`) is parsed by both
platforms — `espn.FLEX_SLOT_ID_TO_NAME` maps 23/7, `sleeper._FLEX_SLOTS` matches
the names — and kept deliberately **out of** `roster_slots`: nobody plays "FLEX",
and `starter_demand` divides a rank by a position's demand, which a flex slot has
none of. `report.SLOT_POSITIONS` says what each accepts. Two consequences worth
keeping: the pooled FantasyPros ranking fills `FLEX` and **only** `FLEX`, since it
is an RB/WR/TE list that doesn't rank quarterbacks and a `SUPER_FLEX` pick drawn
from it would come off a list its best candidate isn't on; and `starter_demand`
still ignores flex slots, so a superflex league's QB depth ratios read against a
one-QB field. Splitting a FLEX across RB/WR/TE to fix that would invent exactly
the kind of number the `depth_ratio` change removed.

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
silently delete the other's. The sibling rebuild is `continue-on-error`, which is
how `./site` ends up with one page in it, so a `Verify the site is complete` step
gates both the upload and the deploy on *both* files existing — skipping the
deploy leaves the live site standing, which beats replacing it with half of one.

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

**`Recommendation.lone_candidate` collapses a section that carries no reading.**
A position with one rostered player rendered as a header, a table whose every
signal column was blank, a note explaining why they were blank, and a verdict
that was never in doubt — nine of the eighteen sections in a three-league digest,
burying the RB and WR tables that decide the week. The digest and the dashboard
now emit the one sentence that was ever in it, keeping the player, his team and
his **flags** (an injury designation or a weather warning is the one real reading
on a lone candidate). It is deliberately stricter than `unranked`, which is also
true for one healthy body beside two on IR: those rows carry "not startable:
ruled out this week", which is exactly what a roster owner needs to see, so that
table stays. The interactive `render_table` keeps its table too — someone who
typed `rank TE` asked for it.

That collapse fixed the position table; it did not reach the lineup table one
level up, which is `render.lineup_unscored_keys`' fix. `report.build_lineup`
fills a slot from `PlayerScore.final` alone, with no `Recommendation` in hand to
ask `unranked` of — so for an `unranked` position (a lone candidate, or one
scored body beside a ruled-out one) it printed the fabricated midpoint
`weighted_final` produces from an all-50 `normalized` row, unconditionally, as
though it were a real score: 10 of 27 Week 1 lineup rows read exactly `50.0`
while the position section directly below said no signal could be scored — one
report contradicting itself. `lineup_unscored_keys` takes the `recs` dict every
lineup renderer already has beside the `Lineup` and returns the player keys
drawn from an `unranked` recommendation; each renderer blanks `final` to `—` for
exactly those picks and adds one footnote. `final` on the `PlayerScore` itself is
untouched — `build_lineup` is still greedy on it, and the player still has to be
startable — this is a presentation fact, the same kind `unranked` and
`lone_candidate` already are, just one hop further from the `Recommendation`
that knows it.
