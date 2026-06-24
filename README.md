# FF Weekly Start/Sit

A command-line tool that tells you **who to start and who to sit** each week. It
blends three signals:

- **ECR (#4)** — FantasyPros Expert Consensus Rankings, the robust "wisdom of the
  crowd" backbone.
- **Vegas (#5)** — implied team totals derived from betting spreads + totals (via
  The Odds API), used to nudge players in better/worse scoring environments.
- **Injury** — availability from the free public Sleeper player data (no key): a
  healthy player scores full marks while a Questionable/Doubtful/Out designation
  drags the score down and is shown as a flag. Injured players sink but still
  appear, so you decide.

It then **flags the close calls** — when the signals disagree or the top
options are within a hair of each other — instead of pretending it knows.

This is a v1 deliberately shaped as the skeleton for an **ensemble +
self-calibration** system (#7): signals are pluggable, blend weights are
configurable, and every decision is logged so a future learner can re-weight the
inputs from your real outcomes.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env               # then edit .env
```

Activating the venv is what puts the `ffstartsit` command on your `PATH` (along
with `pip` and `python`). If you skip activation, the command won't be found —
see the note under [Use](#use) for how to run it anyway.

## Configure (`.env`)

### Signals & tuning

| Variable | Required | Notes |
|---|---|---|
| `ODDS_API_KEY` | no | Free key from [the-odds-api.com](https://the-odds-api.com/). Without it, the app runs on ECR alone and labels Vegas "unavailable". |
| `FANTASYPROS_API_KEY` | no | Tried first; otherwise the app scrapes the public rankings page. |
| `FF_SCORING` | no | `ppr` (default), `half`, or `std`. |
| `FF_WEIGHT_ECR` / `FF_WEIGHT_VEGAS` / `FF_WEIGHT_INJURY` | no | Blend weights (default 0.65 / 0.20 / 0.15). Negative or all-zero weights are rejected and the defaults used. |
| `FF_INJURY` | no | `1` (default) to use the injury signal; `0`/`false` to disable it. |
| `FF_CLOSE_CALL_THRESHOLD` | no | 0–100 gap under which a matchup is "too close to call" (default 5). |

### Roster source

The roster comes from one of three sources, set by `FF_ROSTER_SOURCE` (default
`espn`) and overridable per command with `--source {espn,sleeper,manual}`,
`--league <id>`, and `--team <id>`.

| Source | Variables | Notes |
|---|---|---|
| **espn** (default) | `ESPN_LEAGUE_ID` (req), `ESPN_TEAM_ID`, `ESPN_S2`, `ESPN_SWID` | **Private** league: paste the `espn_s2` + `SWID` cookies from your logged-in browser (DevTools → Application → Cookies); the app then auto-detects *your* team via the SWID. **Public** league: no cookies, but set `ESPN_TEAM_ID` (from `.../teams/<ID>`). |
| **sleeper** | `SLEEPER_USERNAME` (req), `SLEEPER_LEAGUE_ID` | Free, no auth. League defaults to your first NFL league this season. |
| **manual** | `FF_MANUAL_ROSTER` (CSV path) | A hand-edited CSV with headers `name,team,position`. Copy `manual_roster.csv.example` to start. Team accepts abbreviations or full names; `DEF`/`DST` both work. |

> The ESPN read API is unofficial and cookies expire periodically — re-grab them
> if `sync` starts returning 401/403.

## Use

> **`ffstartsit: command not found`?** The command lives in `.venv/bin/`, so it's
> only on your `PATH` once the venv is activated. Any of these work:
> - `source .venv/bin/activate` (Windows: `.venv\Scripts\activate`), then `ffstartsit ...`
> - run it directly without activating: `.venv/bin/ffstartsit ...`
> - run it as a module: `python -m ff_startsit ...`

```bash
ffstartsit sync                              # pull & cache your roster (default: ESPN)
ffstartsit rank --pos RB                     # rank your RBs for the current week
ffstartsit rank --pos WR --week 5 --csv out.csv
ffstartsit compare "Player A" "Player B"     # head-to-head, with close-call flag
ffstartsit lineup                            # best starter at each standard slot

# Source overrides (one default league in .env, switch per command):
ffstartsit rank --pos RB --league 778899 --team 4   # a different ESPN league/team
ffstartsit rank --pos WR --source manual            # use your manual CSV
```

There's also a whole-roster digest:

```bash
ffstartsit report                            # lineup + every position, as markdown
```

Each `rank`/`compare` run appends a row to `.cache/results_log.jsonl` capturing
the candidates, every signal's raw + normalized value, the weights used, and the
pick — the data a #7 calibrator will learn from.

## Use it from your phone (GitHub Actions)

You don't need to run anything locally to use this on the go. GitHub Actions
runners have internet access, so they run the tool for you and surface results in
the GitHub mobile app. Two workflows ship in `.github/workflows/`:

- **Weekly digest** (`weekly-report.yml`) — runs Thursday afternoon and Sunday
  morning (and on-demand via *Actions → Run workflow*), then posts your lineup +
  rankings as a **GitHub Issue** titled `Week N start/sit`. Watch the repo
  (Watch → All Activity) to get a phone notification each time.
- **ChatOps** (`chatops.yml`) — comment a slash command on any issue and the bot
  reply-comments the answer:

  | Command | Does |
  |---|---|
  | `/lineup` | suggested starter at each slot |
  | `/rank RB` | rank your RBs (any position) |
  | `/compare Josh Allen \| Jalen Hurts` | head-to-head with close-call flag |
  | `/report` | the full digest on demand |

  Inline options work on any command: `week N`, `source espn\|sleeper\|manual`,
  `league ID`, `team ID` — e.g. `/rank WR week 5`.

### One-time setup
Add these in **repo → Settings → Secrets and variables → Actions → New repository
secret** (same values as your local `.env`):

`ESPN_LEAGUE_ID`, `ESPN_S2`, `ESPN_SWID` (private league) — and optionally
`ESPN_TEAM_ID` (public league), `ODDS_API_KEY`, `FANTASYPROS_API_KEY`.

Notes:
- Only the **repo owner's** comments trigger ChatOps, and only a fixed set of
  commands runs — secrets are never echoed.
- Cron times are UTC and drift ~1h with daylight saving; edit the `cron:` lines in
  `weekly-report.yml` to taste.
- ESPN cookies expire periodically — if the digest starts erroring, re-grab
  `ESPN_S2`/`ESPN_SWID` and update the secrets.

## How scoring works

1. Each signal returns a native value per player (ECR rank; implied team total;
   injury-availability score).
2. Values are scaled to **0–100 within the candidate set** — lower ECR rank,
   higher implied total, and higher availability all map toward 100.
3. `final = weighted average of available signals` (a missing signal, e.g. a bye,
   is dropped and the rest re-normalized, so nobody is penalized for missing data).
4. The top two are compared: within the threshold, or signals disagreeing on the
   order → flagged **close call**.

## Architecture

```
ff_startsit/
  cli.py            entry point: sync / rank / compare / lineup / report
  config.py         .env-driven Settings (weights, threshold, scoring)
  models.py         Player, Game, SignalValue, PlayerScore, Recommendation
  pipeline.py       assemble signals -> fetch -> blend -> log
  report.py         whole-roster markdown digest + shared lineup builder
  chatops.py        parse "/rank RB" style comments -> CLI argv (for Actions)
  sources/
    base.py         Signal ABC  <-- add new signals here (the #7 seam)
    ecr.py          FantasyPros ECR (API key + scrape fallback)
    vegas.py        The Odds API -> implied team totals
    injury.py       Sleeper injury status -> availability score
  roster/
    base.py         RosterProvider ABC  <-- add new roster sources here
    espn.py         ESPN league (cookies + team auto-detect / id fallback)
    sleeper.py      Sleeper username -> league -> roster (+ SleeperProvider)
    manual.py       hand-edited CSV roster
  data/
    teams.py        team-abbreviation normalization across sources
    espn_maps.py    ESPN proTeamId / positionId -> canonical codes
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
metadata parsing, ESPN roster parsing (team auto-detect by SWID + id, D/ST),
manual-CSV parsing, the roster-provider factory, and an end-to-end pipeline run
with fake signals.

> Note: live API calls require outbound network; in restricted environments run
> the unit tests (offline) and do the live end-to-end run on your own machine.
