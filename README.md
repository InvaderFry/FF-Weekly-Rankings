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
- **Weather** — free public Open-Meteo forecasts (no key): high wind and rain at
  an outdoor stadium nudge that team's players down (and surface as a flag), while
  dome and retractable-roof games score neutral. Fair-weather games are untouched.

It then **flags the close calls** — when the signals disagree or the top
options are within a hair of each other — instead of pretending it knows.

It's built as an **ensemble + self-calibration** system (#7): signals are
pluggable, blend weights are configurable, and every decision is logged — so the
`calibrate` command can re-weight the inputs from your real outcomes (see
[Self-calibration](#self-calibration-calibrate-7)).

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

> **Step-by-step walkthrough:** [docs/SETUP.md](docs/SETUP.md) covers every
> variable in detail — finding your ESPN league id and cookies, the
> preferred-journalist expert ids, and setting up the GitHub Actions
> secrets/variables for the automated weekly runs.

### Signals & tuning

| Variable | Required | Notes |
|---|---|---|
| `ODDS_API_KEY` | no | Free key from [the-odds-api.com](https://the-odds-api.com/). Without it, the app runs on ECR alone and labels Vegas "unavailable". |
| `FANTASYPROS_API_KEY` | no | Tried first; otherwise the app scrapes the public rankings page. |
| `FF_SCORING` | no | `ppr` (default), `half`, or `std`. |
| `FF_WEIGHT_ECR` / `FF_WEIGHT_VEGAS` / `FF_WEIGHT_INJURY` / `FF_WEIGHT_WEATHER` | no | Blend weights (default 0.60 / 0.18 / 0.12 / 0.10). Negative or all-zero weights are rejected and the defaults used. |
| `FF_INJURY` | no | `1` (default) to use the injury signal; `0`/`false` to disable it. |
| `FF_WEATHER` | no | `1` (default) to use the weather signal (free, no key); `0`/`false` to disable it. |
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
ffstartsit dashboard --out site/index.html   # the same, as a static HTML page
ffstartsit notify --url https://you.github.io/repo/   # push to a Discord webhook
# All three from a single scoring pass (what the weekly Action runs):
ffstartsit publish --report report.md --dashboard site/index.html --discord --url https://you.github.io/repo/
```

### Preferred journalists (optional view)

If you trust a few specific rankers — say Justin Boone, Jamey Eisenberg, and
Dave Richard — you can add a **"Preferred journalists"** section to the report
and dashboard: each journalist's own weekly rank per player, plus their
average. It's a side-by-side view only; it never enters the blended score or
calibration. Set `FF_PREFERRED_EXPERTS` to FantasyPros expert ids —
[docs/SETUP.md](docs/SETUP.md#3-preferred-journalists-ff_preferred_experts)
walks through finding them — then:

```bash
ffstartsit journalists               # just this section, on demand
ffstartsit report                    # digest now ends with the section
```

Each `rank`/`compare` run appends a row to `.cache/results_log.jsonl` capturing
the candidates, every signal's raw + normalized value, the weights used, and the
pick — the data the calibrator learns from.

### Self-calibration (`calibrate`, #7)

Once you've logged a few weeks of `rank`/`compare` runs, `calibrate` joins that
log against **actual** weekly fantasy points and learns the blend weights that
would have ranked *your* players best:

```bash
ffstartsit calibrate                 # report current vs learned weights
ffstartsit calibrate --week 5        # only weeks 5; also --season YYYY
ffstartsit calibrate --write         # persist the learned weights (auto-applied next run)
```

- **Outcomes source:** the free, no-auth Sleeper weekly-stats endpoint, which
  returns precomputed PPR/Half/Standard points keyed by the same player id the log
  uses (ESPN/manual rosters fall back to name+position matching). No key needed.
- **Learner:** a pure-Python grid search over the weight simplex. For each trial it
  re-blends the logged `normalized` scores (no re-fetching) and scores them by
  **pairwise ranking concordance** — how often the blend ordered two players the
  way their real points did. It also reports **top-pick hit-rate** (how often the
  #1 pick was the week's actual best) for the current vs learned weights.
- **Apply:** `--write` saves `.cache/learned_weights.json`; the next run picks it up
  automatically (precedence: defaults → learned file → explicit `FF_WEIGHT_*`).
  It refuses to write on thin data (`--min-pairs`, default 30) or when your current
  weights already match the best on the grid — surfacing coin-flips over false
  confidence, same as the rest of the tool.

### Backtest (`backtest`)

Where `calibrate` *searches* for better weights, `backtest` *reports how the picks
you already made turned out* — replaying each logged decision under the weights it
actually used (re-blending the stored scores, never re-fetching) and joining the
top pick to real weekly points from the same free Sleeper source:

```bash
ffstartsit backtest                  # all logged weeks
ffstartsit backtest --week 5         # one week; also --season YYYY
```

It prints two things:

- **Accuracy** — top-pick hit-rate (how often the #1 pick was the week's actual
  best) and the average points left on the bench.
- **Close-call honesty** — the product's core promise, measured. It splits your
  decisions into *confident* (not flagged) and *close call* (flagged) and compares
  hit-rates. A trustworthy flag should hit clearly more often on the confident set
  than on the coin-flips it warned you about — so you can see whether "too close to
  call" actually meant it.

Like `calibrate`, it needs a few logged weeks with posted outcomes to say anything;
with an empty log or outcomes not yet posted, it says so and exits.

## Use it from your phone (GitHub Actions)

You don't need to run anything locally to use this on the go. GitHub Actions
runners have internet access, so they run the tool for you and surface results in
the GitHub mobile app. Two workflows ship in `.github/workflows/`:

- **Weekly digest** (`weekly-report.yml`) — runs Thursday afternoon and Sunday
  morning (and on-demand via *Actions → Run workflow*). Each run:
  - posts your lineup + rankings as a **GitHub Issue** titled `Week N start/sit`
    (watch the repo → All Activity for a phone notification),
  - publishes a styled **HTML dashboard to GitHub Pages** (full lineup +
    rankings, with injury/close-call rows highlighted), and
  - **pings Discord** with the lineup, alerts, and a link to the dashboard — if a
    `DISCORD_WEBHOOK_URL` secret is set.
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

  > **These commands are GitHub issue comments — they do NOT work in Discord.**
  > The Discord integration is a one-way incoming webhook: the tool *posts* the
  > weekly summary to your channel, but nothing is listening for messages there
  > (there is no Discord bot). To use a command, open the week's
  > `Week N start/sit` issue on GitHub (or any issue) and post the command as a
  > comment — the Actions bot replies within a minute or so. The Discord embed
  > includes a **💬 Commands** link that takes you straight to the issues page.

### Preseason (before Week 1)

Before the season kicks off (the first Thursday of September) there are no
weekly ECR rankings and no Vegas lines, so a live run has nothing to score.
Instead of posting an empty lineup, runs during the preseason:

- carry a **⚠️ PRESEASON warning** on the report, dashboard, and Discord embed
  (the embed turns amber), and
- are **auto-filled with bundled, clearly-labeled sample data** so you can see
  the tool end-to-end with your real roster. Sample runs are never written to
  the results log, so they can't skew `calibrate` (#7).

Set `FF_PRESEASON_FILL=0` to disable the sample fill and show the warning with
empty slots instead.

### One-time setup

> Detailed click-by-click instructions: [docs/SETUP.md](docs/SETUP.md#6-github-actions-setup-the-twice-weekly-runs).

Add these in **repo → Settings → Secrets and variables → Actions → New repository
secret** (same values as your local `.env`):

`ESPN_LEAGUE_ID`, `ESPN_S2`, `ESPN_SWID` (private league) — and optionally
`ESPN_TEAM_ID` (public league), `ODDS_API_KEY`, `FANTASYPROS_API_KEY`.

For the **Preferred journalists** section in the scheduled runs, add
`FF_PREFERRED_EXPERTS` on the **Variables** tab (it's not sensitive) with the
same `id:Name,...` value as your local `.env`.

For the **dashboard + Discord** delivery:
- **Enable GitHub Pages once:** repo → *Settings → Pages → Build and deployment →
  Source = GitHub Actions*. After the next weekly run your dashboard lives at
  `https://<owner>.github.io/<repo>/`.
- **Discord (optional):** create an incoming webhook (Discord → *Server Settings →
  Integrations → Webhooks → New Webhook → Copy URL*) and add it as the
  `DISCORD_WEBHOOK_URL` repository secret. Leave it unset to skip Discord.

Notes:
- Only the **repo owner's** comments trigger ChatOps, and only a fixed set of
  commands runs — secrets are never echoed.
- Cron times are UTC and drift ~1h with daylight saving; edit the `cron:` lines in
  `weekly-report.yml` to taste.
- ESPN cookies expire periodically — if the digest starts erroring, re-grab
  `ESPN_S2`/`ESPN_SWID` and update the secrets.

## How scoring works

1. Each signal returns a native value per player (ECR rank; implied team total;
   injury-availability score; weather-conditions score).
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
    weather.py      Open-Meteo forecast -> conditions score (no key)
  roster/
    base.py         RosterProvider ABC  <-- add new roster sources here
    espn.py         ESPN league (cookies + team auto-detect / id fallback)
    sleeper.py      Sleeper username -> league -> roster (+ SleeperProvider)
    manual.py       hand-edited CSV roster
  data/
    teams.py        team-abbreviation normalization across sources
    stadiums.py     per-team stadium lat/lon + dome flag (for weather)
    espn_maps.py    ESPN proTeamId / positionId -> canonical codes
    matching.py     name/position matching of external rows onto the roster
  engine/
    normalize.py    raw -> 0..100 (pure)
    blend.py        weighted ensemble (weighted_final) + close-call flagging (pure)
  calibrate/        self-calibration (#7): learn weights from logged outcomes
    outcomes.py     Sleeper weekly stats -> actual points (id + name/pos join)
    log_reader.py   read results_log.jsonl back into Decisions
    learner.py      grid-search weights by ranking concordance / hit-rate
    backtest.py     replay logged picks vs outcomes; close-call honesty check
  results_log.py    append-only JSONL decision log (the #7 hook)
  output/
    render.py       rich table + markdown + CSV/JSON export
    html.py         self-contained HTML dashboard (for GitHub Pages)
    discord.py      Discord webhook payload + send (push notifications)
```

### Growing into #7 (ensemble + self-calibration)
- **Add a signal:** subclass `sources/base.py:Signal`, add it in
  `pipeline.build_signals`, give it a weight. Nothing in the engine changes.
- **Re-weight:** weights live only in `config.Settings`; a calibrator can rewrite
  them programmatically.
- **Learn:** `ffstartsit calibrate` joins `results_log.jsonl` against actual weekly
  fantasy points (Sleeper stats) and grid-searches the weights that best ordered
  your players; `--write` persists them. See
  [Self-calibration](#self-calibration-calibrate-7) above.

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
