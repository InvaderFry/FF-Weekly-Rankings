# Review fix plan (November 2026)

A handoff plan for the bugs and improvements found in a code review of this
repository. It is written for an agent that has not seen the review.

## How to use this plan

- Read `CLAUDE.md` first. It holds the invariants every change here must keep,
  and several items below touch them directly.
- Do one phase per branch and pull request, in the order given. Phases are
  independent unless a phase says otherwise.
- Line numbers were correct at commit `18c731b`. Find the code by the function
  name if they have moved.
- Every fix needs a regression test that fails before the change and passes
  after it. Tests are offline: use fixtures and injected fakes, never the network.
- Before each pull request run the full suite, `scripts/check-workflows.py`
  and `actionlint` (commands are in `CLAUDE.md`). Keep Python 3.10 compatible.
- Do not change `normalize.to_0_100` or `blend.weighted_final`, and do not
  rewrite the `calibration-data` branch. Logged rows must stay replayable.
- Items marked **Owner decision** have a recommended default. Stop and ask
  before implementing them if the owner is reachable; otherwise use the default
  and say so in the pull request.
- When a change alters behaviour that `CLAUDE.md` describes, update that
  section in the same pull request.

## Phase 1: small correctness fixes

### 1.1 One league's HTTP error aborts every league

- **Problem.** `ESPNProvider._fetch` (`ff_startsit/roster/espn.py`) converts
  connection errors to `RosterError`, but an HTTP 5xx leaves through
  `resp.raise_for_status()` as `requests.HTTPError`, and a non-JSON body leaves
  through `resp.json()`. `cli._league_bundles` (`cli.py:845`) and
  `cli._waiver_bundles` (`cli.py:980`) catch only `RosterError` and
  `SleeperError`, so one league's 502 ends the whole multi-league run.
  `SleeperClient._get` has the same shape.
- **Fix.** Wrap the status and JSON failures in `_fetch` as `RosterError`, with
  the status code in the message. Do the equivalent in the Sleeper path
  (`SleeperError`). Also widen the two per-league `except` clauses to include
  `requests.RequestException`, so a provider that forgets to wrap still costs
  one league.
- **Done when.** A test with two leagues, where the first league's session
  returns 502, yields one bundle and a `DataStatus.skipped` entry naming the
  first league and the status code. Same for the waiver path.

### 1.2 The date fallback returns last week on Tuesdays

- **Problem.** `season.date_week` (`season.py:106`) counts 7-day blocks from the
  kickoff date. Fantasy weeks turn over on Tuesday. With the 2026 Wednesday
  kickoff, Tuesday 2026-09-15 returns Week 1; with the 2025 Thursday kickoff,
  both 2025-09-09 and 2025-09-10 return Week 1. The Tuesday waiver pass uses
  this whenever Sleeper's `/state/nfl` is unavailable, and
  `ECRSignal._warn_if_week_mismatch` uses it when the page states no week.
- **Fix.** Anchor the week boundary to the Tuesday on or before kickoff: Week 1
  runs from kickoff to the following Monday, and each later week runs Tuesday
  to Monday. Before kickoff still returns 1; the clamp at 18 stays.
- **Done when.** Tests pin, for both 2025 and 2026 kickoffs: the Monday after
  kickoff is Week 1, the Tuesday after is Week 2, kickoff day is Week 1.

### 1.3 `normalize_team` guesses on partial strings

- **Problem.** The loose substring fallback in `data/teams.py:84` returns the
  first team whose full name contains the input or is contained by it. `"FA"`
  returns ATL (FantasyPros stamps free agents `FA`), `"New York"` returns NYG,
  `"Los Angeles"` returns LAC, `"Bay"` returns GB.
- **Fix.** Return a loose match only when exactly one team matches. Add an
  explicit set of non-team values (`FA`, `FREE AGENT`) that return `None`.
  Add nickname aliases (`Giants`, `Chiefs`) if tests rely on them through the
  loose path. An ambiguous city (`New York`, `Los Angeles`) returns `None`.
- **Check.** `matching._defense_key` tries `team` before `name`, so defenses
  that carry a team are unaffected. Confirm the DEF tests in
  `tests/test_matching.py` still pass, and that a manual-roster defense named
  only "New York" is now unmatched (reported as missing) instead of the Giants.
- **Done when.** Tests pin the four inputs above plus the existing aliases.

### 1.4 An unreachable FantasyPros page is silent

- **Problem.** `ECRSignal._rows_uncached` (`sources/ecr.py:330`) returns `[]`
  on a `RequestException` with no warning and no status entry. Every player
  then reads "no ECR rank", identical to being unranked.
- **Fix.** Warn once on stderr and record a source-status entry through
  `_record_source` (or a sibling) saying the page was unreachable, with the
  exception class or HTTP status. Give affected players a distinct note
  (for example "ECR page unreachable") instead of "no ECR rank".
- **Done when.** A test with a failing session shows the Data status line and
  the distinct note. Follow the `(identity, line)` contract described under
  "Run status" in `CLAUDE.md`.

## Phase 2: start/sit uses the league's real lineup slots

- **Problem.** `cli._score_league` (`cli.py:1499`) never passes `slots` to
  `report.score_week`, and `report.lineup_from` (`report.py:500`) always builds
  the hardcoded `LINEUP_SLOTS`. Superflex, 3-WR, two-flex and no-kicker leagues
  get the wrong lineup, the wrong last-starting-spot flag and the wrong lineup
  marker. The waiver pass already does this correctly.
- **Fix.**
  1. Extract the slot-list construction from `waivers/build._lineup_keys` into
     one shared function (suggested: `report.league_slots(rules)`), and call it
     from both places. Order: position slots first, flex slots last.
  2. In `_score_league`, read `LeagueRules` from the provider when it is a
     `LeagueViewProvider` and the run is live. Reuse the provider instance
     `_availability_view` already builds so ESPN's memoized league payload
     serves it. Check how many requests a pass makes before and after; the
     goal is at most one extra per league.
  3. Pass the slots to `score_week` and store them on `WeekScores` so
     `lineup_from` builds the same lineup the boundary flag saw.
  4. Fall back to `LINEUP_SLOTS` when rules are empty, the source is manual,
     the run is offline or preseason, or the rules read fails. Never raise.
- **Watch for.**
  - `build_lineup` fills `SUPER_FLEX` from per-position scores, by design.
    Keep that; do not draw it from the pooled FLEX list.
  - `availability.lineup_holes` treats an empty K or DEF slot as "not a hole"
    because the template always had one. With real slots that rule can be
    narrowed, but leave it unchanged in this phase.
  - The first pass after this ships will differ from the previous `ff-lineup`
    marker for any league whose shape is not the template. That one-time
    "lineup changed" post is expected; mention it in the pull request.
  - The four lineup renderers (digest, dashboard, Discord, `cmd_lineup`) must
    all show the same slots.
- **Done when.** Tests cover a superflex league (second QB starts), a two-flex
  league, and a league with no kicker slot, through `_score_league` with a fake
  provider; and a manual source still gets the template.

## Phase 3: Vegas for team defenses

- **Problem.** `VegasSignal.assign` (`sources/vegas.py:157`) gives every
  player his own team's implied total, higher is better. For a team defense
  the direct reading is the opponent's implied total, lower is better.
- **Owner decision.** How to represent it, since a `Signal` has one
  `higher_is_better`. Recommended default: for `DEF` players the raw value is
  the opponent's implied total negated, with a note such as
  "opp implied 24.5" so the table explains the number. Defenses are only ever
  normalized against other defenses, so the sign is safe for the blend, and
  `close_call_raw_gaps["vegas"]` still works because it compares absolute
  differences.
- **Fix.** Build `{team: opponent implied total}` alongside
  `implied_totals_by_team`, and use it in `assign` when the player is a
  defense (`matching.is_defense`). Check every renderer that prints the raw
  Vegas value so a negative number is not shown bare.
- **Corpus note.** This changes what is logged for defenses' `vegas` values
  from this point on. Record the date in `CLAUDE.md` next to the existing
  note about the injury and weather scale change, for the same reason.
- **Done when.** A test with two defenses shows the one facing the lower
  opponent total ranked higher on Vegas, and non-defense players are unchanged.

## Phase 4: calibration and backtest honesty

### 4.1 Persist ChatOps decisions

- **Problem.** `chatops.yml` neither restores nor persists
  `results_log.jsonl`, so `/rank` and `/compare` rows, the decisions the owner
  actually asked about, never reach the `calibration-data` branch.
- **Fix.** Add the restore step and `scripts/persist-decision-log.sh` to
  `chatops.yml`, as `weekly-report.yml` has them, with `contents: write`. The
  two workflows are in different concurrency groups, so the push can race:
  make the script fetch and re-apply on a rejected push (append this run's new
  rows to the fetched file, retry a few times). Extend the script's existing
  bare-remote test for the race.
- **Done when.** The script test covers a concurrent push, and both workflow
  checks pass.

### 4.2 A pick who did not play is not promoted away

- **Problem.** `calibrate/backtest.py:100` drops a candidate with no Sleeper
  points, then takes the pick as the best of the remainder. A late-scratch top
  pick is scored as if the runner-up had been chosen.
- **Fix.** Determine the pick (highest blended final) before joining outcomes.
  If the pick has no outcome while the week's stats exist, count the decision
  in a separate "pick did not play" bucket, reported on its own line and
  excluded from the hit-rate rather than counted as a hit or as the
  runner-up's result. Leave `learner.join_outcomes` unchanged.
- **Done when.** A test with a three-candidate decision whose top pick has no
  points shows it in the new bucket and not in `hits`.

### 4.3 Separate the top-two flag from the boundary flag

- **Problem.** `close_call` is one boolean (`results_log.py:37`), also set by
  `blend.flag_starter_boundary_pair`. `backtest` buckets top-pick hits on it
  (`backtest.py:116`), so a confident top pick with a tight last starting spot
  counts as a flagged pick.
- **Fix.** Record which check fired, for example a `close_call_kinds` list on
  `Recommendation` (`"top"`, `"boundary"`), written to the log row and read by
  `log_reader`. `backtest` buckets on `"top"` when the field is present. Rows
  without the field keep today's behaviour. `close_call` itself stays as is
  for every renderer.
- **Done when.** Round-trip tests cover a new row and an old row, and a
  boundary-only decision lands in the confident bucket.

### 4.4 Guard `calibrate --write` against overfitting

- **Problem.** Four signals at step 0.05 is 1,771 trial vectors against a
  30-pair floor, and any in-sample gain above zero allows a write
  (`cli.py:1266`). The best vector will usually beat the current weights by
  chance, and a learned file replaces the whole weight set.
- **Owner decision.** Recommended default: leave-one-week-out validation. For
  each week, fit on the other weeks and score the held-out week; write only if
  the mean held-out concordance beats the current weights by a minimum margin
  (suggested flag `--min-gain`, default 0.02). Report the held-out number
  beside the in-sample one, and add a shortfall message when it fails.
- **Done when.** A test with pure-noise outcomes refuses to write, and a test
  with a consistent signal across weeks writes.

## Phase 5: operations

### 5.1 HTTP retries

- No request in the package retries, and nothing is cached between Actions
  runs. Add one session factory in `ff_startsit/net.py` that mounts a
  `urllib3` `Retry` (about 3 attempts, short backoff, on 429 and 5xx, GET
  only) and sends `net.HEADERS`. Use it wherever `requests.Session()` is
  constructed today (17 places). Keep every `session=` injection point so
  tests stay offline. Do not retry the Discord POST.
- Keep `tests/test_net.py`'s User-Agent rules intact.

### 5.2 Season gate for scheduled runs

- No workflow checks the calendar. Add a `workflow.py` subcommand (suggested
  `in-season`) that scheduled runs call first. It should allow: the rehearsal
  window (`season.is_rehearsal_window`), and kickoff through the end of
  Week 18. Outside that, scheduled runs exit successfully without publishing.
  `workflow_dispatch` always runs. Put the decision in `workflow.py` with
  tests, per the rule in `CLAUDE.md` that workflow logic is not left in YAML.
- **Owner decision.** Whether the waiver refusal banner should still post once
  in preseason. Recommended default: no scheduled posts outside the window.
- GitHub disables schedules after 60 days without repository activity. Code
  cannot fix that; note it in `docs/SETUP.md` as a September checklist item.

### 5.3 Close last week's issues

- When a workflow creates a new week's issue, close the previous open issue of
  the same kind (start/sit, waivers, values) for an earlier season or week.
  Put the "which issue is stale" decision in `workflow.py` with tests. Check
  first that no step relies on older issues staying open (`--since` reads only
  the newest open one).

### 5.4 Pin what scheduled runs install

- Add a constraints file with exact versions of `requests`, `python-dotenv`
  and `rich`, used by the four non-CI workflows' install step. Leave the CI
  matrix unconstrained so new releases are still tested.
- **Owner decision.** Pinning actions to commit SHAs. Recommended, since the
  workflows hold ESPN cookies and `contents: write`, but it adds upkeep.

## Phase 6: optional

- **Position-aware weather.** Wind and rain currently penalise running backs
  and team defenses as much as quarterbacks and kickers. **Owner decision**
  on the model; a minimal version applies the penalty only to QB, WR, TE and
  K and scores RB and DEF neutral. This also changes logged weather values,
  so it needs the same corpus note as Phase 3.
- **Unicode in `normalize_name`.** Fold curly apostrophes to straight ones
  and strip accents (NFKD) before the existing rules, so `Ja’Marr` matches
  `Ja'Marr`. `data/stadiums._venue_key` already does the accent half.
- **Smaller files.** Split `cli.py` (1,707 lines) by command group, and move
  the incident history in `CLAUDE.md` to `docs/`, leaving the invariants.
  Pure moves, no behaviour change, each in its own pull request.

## Out of scope

- The waiver, boards and HTML/Discord renderer code was only skimmed in the
  review. Nothing here should be read as saying it is free of bugs.
