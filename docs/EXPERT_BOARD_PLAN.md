# Expert board plan

Status: **agreed; Phases 0–1 in progress.**
Site findings below were verified against live pages on 2026-11-02.

## Goal

Put the sources the owner trusts side by side, on the days each one publishes,
for every league (ESPN today; Sleeper supported; Yahoo deferred):

| When (Central) | Question | Sources, side by side |
|---|---|---|
| Thu, Sat night, Sun morning | Who do I start? | FantasyPros consensus, Justin Boone (Yahoo), CBS staff consensus |
| Tue evening | Who do I pick up, and for how much? | Boone's waiver column, Jamey Eisenberg's waiver column, Dave Richard's trade chart / ROS, FantasyPros ROS |
| Wed evening | Re-value my teams, and which trades make sense? | Richard's trade chart, Boone's trade value charts, FantasyPros ROS |

Delivery: Discord **and** GitHub Pages. Tuesday and Wednesday post their full
tables to Discord, one message per league.

Expert sources are **side by side only**. They never become a blend `Signal`,
never carry a weight, and never reach `results_log.jsonl`. The blend and
close-call flag stay as they are.

## What the sites actually serve (verified 2026-11-02)

Every page below returns server-rendered HTML to a plain request: no browser,
no JavaScript, no login, no disguised User-Agent needed.

### CBS weekly rankings: `cbssports.com/fantasy/football/rankings/<scoring>/<POS>/weekly/`

- `<scoring>` is `ppr` or `standard`. **There is no half-PPR list.** The
  `half-ppr` URL returns a 200 with a different order and *no analyst columns*,
  so it must be treated as unavailable, never parsed.
- `<POS>` is `QB`, `RB`, `WR`, `TE`, `K`, `DST`, or `flex`. Depth: 24 QB/TE/K/DST,
  48 RB, about 150 on flex. The un-suffixed `/rankings/` is the PPR flex list.
- Each row is `RK`, player link (`/nfl/players/<cbs id>/<slug>/`), opponent rank,
  then one column per analyst. The header names each analyst in order (currently
  Jamey Eisenberg, Dave Richard, Heath Cummings), with "Updated Nd ago".
- **`RK` is the analysts' average, sorted.** Checked on RB (43 fully ranked rows)
  and flex (131 rows): ordering by the mean of the three columns matches `RK` with
  no inversions. That makes it the "CBS staff consensus" the owner wants.
- **This corrects a conclusion in `CLAUDE.md`.** The per-analyst *slug* pages
  (`/jamey-eisenberg/` and so on) do serve identical data, so that trap is real.
  But the consensus page itself carries each analyst's rank in a column whose
  header names him, so Eisenberg's and Richard's weekly ranks **are** attributable.
  Attribution still fails closed: header count must equal column count, and each
  header must name the expected analyst.

### Dave Richard's trade chart / ROS, on Tuesday

- Example: `/fantasy/football/news/dave-richards-week-8-trade-chart-and-rest-of-season-rankings-.../`.
  Published Tue 2026-10-27 19:00 UTC. The JSON-LD `author` is Dave Richard.
- Headline and slug wording vary by week ("2026-week-6-trade-chart",
  "week-7-trade-chart-and-rest-of-season-rankings"). Discovery uses
  `/writers/dave-richard/` (which lists them) plus the headline, week and JSON-LD
  byline, **not** the slug. Exclude his separate "Trade to win now" piece.
- Four `<h2>Position trade values</h2><table class="TableBuilder">` tables:
  - QB: `Player, tm, 1QB-4, 1QB-6, 2QB` (passing-TD points and superflex)
  - RB, WR, TE: `Player, tm, non, 0.5, PPR`, so **half-PPR is covered**
  - About 33 QB, 44 RB, 47 WR, 17 TE. `--` means no value in that format.
- The article says the list doubles as rest-of-season rankings and that anyone not
  on it is worth at most 4. No K or DST.

### Jamey Eisenberg's waiver column, on Tuesday

- Example: `/fantasy/football/news/fantasy-football-2026-week-8-waiver-wire/`.
  Published Tue 2026-10-27 20:36 UTC. Listed on `/writers/jamey-eisenberg/`.
- Covers players rostered in **under 65% of CBS leagues**. Must-adds above that
  line appear only in the intro prose, so match those against the league's
  free-agent pool, as `waivers/columns.py` does today.
- Per position (QB/RB/WR/TE): "Add in this order", then a sequence of
  `PlayerObjectV4` cards in priority order. Each card carries the CBS player link
  and team, and its write-up ends with a FAAB figure ("5-10 percent", "at least 10
  percent", "1 percent of your remaining FAAB"). Parsed cleanly for 7 QB, 6 RB,
  9 WR and 8 TE players in the example.
- Also per position: "On a bye", "Injuries", "Check to see if available in
  shallow leagues", and "Drop candidates".

### Justin Boone's waiver column (Yahoo), posted **Monday** afternoon

- Example: `.../week-9-fantasy-football-waiver-wire-...-182552018.html`. Published
  Mon 2026-11-02 18:25 UTC. Listed on `/author/justin-boone/`.
- The body is server-rendered HTML with semantic headings:
  - `Top-10 overall pickups`: an ordered list of `Name, POS, Team`
  - `<h2>` per position. Each featured player is an `<h3>Name, Team</h3>` followed
    by a small table: `Next Up | Rostered | FAB`, e.g. `at SF | 27% | $29`.
  - Tiered lists under bold labels: Flex/streaming options, Risky, Dart-throw,
    Upside stashes, Deep stashes, Bye-week stashes, Injured-reserve stashes. Each
    item reads `Name vs. OPP (N% rostered)`.
  - `Drop candidates` (prose bullets), plus DST and K streamers.

### Justin Boone's trade value charts (Yahoo), posted **Wednesday** afternoon

- Four articles per week, one each for QB, RB, WR and TE. Example:
  `.../2026-trade-value-charts--justin-boones-fantasy-football-quarterback-breakdown-for-week-8-154235381.html`.
  Published Wed 2026-10-28 21:13 UTC. JSON-LD author is Justin Boone.
- One `<table class="content-table">` each. QB is `Rk, Player, 1QB, 2QB`; RB is
  `Rk, Player, HALF, PPR` (WR and TE expected to match). No team column, so match
  on name plus the article's position.
- **Discovery:** his author page does *not* list the charts. His weekly rankings
  hub (`fantasy-football-week-N-rankings-justin-boone-*.html`, which the author
  page does list) links all four, and each chart links its siblings. Risk: on
  Wednesday evening the hub for the new week may not link the new charts yet, so
  keep a manual override (a CLI flag or a ChatOps link).

### Scales differ, so compare ranks and judge trades per chart

Richard puts Gibbs at 56 in PPR; Boone puts him at 104. The values aren't on a
shared scale, so the board shows each writer's own number **plus** a rank within
position computed from it. Trade checks are judged **separately per chart**
("Richard: +6, Boone: −3") rather than averaged. A split verdict is exactly what
the owner wants to see.

### Request etiquette

Plain requests work everywhere. `analysts.py` and `waivers/columns.py`
currently send a spoofed desktop-Chrome User-Agent. Replace both with one honest
UA (`ff-startsit (+repo URL)`) in a shared helper, and keep volume low by caching
every article and ranking page through `cache.py`.

## Design: `ff_startsit/boards/`

A third extension point beside `Signal` and `RosterProvider`, for display data.

```python
@dataclass(frozen=True)
class BoardEntry:
    player_key: str          # matched via data/matching (name + position + team)
    pos_rank: Optional[int]  # rank within position, derived if the source gives values
    value: Optional[float]   # chart value, if any
    bid: Optional[str]       # "$29", "at least 10%" — the writer's own wording
    tier: Optional[str]      # "Top-10", "Dart-throw flex", "IR stash", ...
    note: Optional[str]

@dataclass
class ExpertBoard:
    source_id: str           # "cbs_consensus", "eisenberg_waivers", ...
    label: str               # always names where it came from
    kind: str                # "weekly" | "ros" | "waiver"
    url: Optional[str]
    published: Optional[datetime]   # article date, or None plus fetched_at for live pages
    fetched_at: datetime
    entries: dict[str, BoardEntry]
    unavailable: Optional[str]      # its own reason, never a generic one

class ExpertSource(ABC):
    id: str; label: str; kind: str
    def fetch(self, week, season, scoring, players) -> ExpertBoard: ...
```

Rules carried over from the existing code:

- **Fail closed on attribution:** check the JSON-LD byline, the week in the
  headline, and the season. A mismatch gives `unavailable`, never someone else's
  numbers.
- **"Not posted yet" stays distinct from "broke"** (like `analysts.not_published_yet`).
  Only the first warrants a retry run.
- **Every page cache goes through `cache.py`.**
- **Nothing reaches `results_log.jsonl`.**

The renderer is `output/board.py`: one function per target (Markdown, HTML,
Discord), each taking a list of boards plus the candidate players. The Discord
version uses one embed **field per player** rather than a monospace table,
because code-block tables wrap badly on a phone.

## Phases

### 0. Request hygiene
- One honest User-Agent in `ff_startsit/net.py`, used by every outbound request,
  replacing the spoofed desktop-Chrome strings in `analysts.py` and `columns.py`.
- Fixtures land **with the phase whose parser reads them**, in
  `tests/fixtures/boards/`, trimmed to the few rows, headings, bylines and
  JSON-LD each parser needs. No full articles go in the repo.

### 1. The `boards` package, wrapping what exists
- `ExpertBoard`, `ExpertSource`, and the matching helpers.
- Sources: `fantasypros_weekly` (built from the ECR signal's raw positional
  ranks, so it costs no extra request) and `boone_weekly` (wraps
  `AnalystFetcher`; still enabled by `FF_ANALYSTS=boone`). `fantasypros_ros`
  moves to Phase 3, where it is first used.
- `output/board.py` holds the shared column helpers. Markdown and HTML position
  tables gain one column per board; Discord gets its compact form in Phase 2.
- Sample (preseason fill) runs show no expert columns, because sample ranks
  aren't anyone's real opinion.
- Done when the digest, dashboard, `rank` and ChatOps `/rank` show FP | Boone
  beside the blend for every position, with the existing tests still passing.

### 2. Start/sit with CBS (Thu, Sat, Sun)
- `cbs_consensus` source: per-position pages, average plus per-analyst columns,
  and a header check that fails closed. `half` scoring gives `unavailable`, or a
  labelled PPR fallback depending on the open question below.
- Position sections show **FP | Boone | CBS | Blend | Pick**. The Boone
  "disagreement" block becomes a marker in that table.
- Discord shows the lineup, plus a one-line three-source comparison for each
  close call. Pages shows the full tables.
- Add a Saturday cron to `weekly-report.yml`.

### 3. Tuesday waiver board
- Sources: `boone_waivers` (Top-10, featured players with FAB $ and rostered %,
  tiers, drops), `eisenberg_waivers` (priority order and FAAB %, drop candidates,
  intro must-adds matched to the pool), and `richard_chart` (per-position values;
  the scoring column chosen by league; for QBs, passing-TD points read from ESPN
  league settings, falling back to 4).
- One board **per league**, restricted to that league's free agents:
  **Player | Boone (rank · $) | Eisenberg (rank · FAAB %) | Richard value | FP ROS
  | suggested drop**. Sorted by how many writers list him, then their best rank.
  The existing add/drop and bid logic stays as its own section.
- Discord: the full board, one message per league.
- Move `waivers.yml` to Tuesday afternoon, plus a retry slot (schedule below).

### 4. Wednesday values pass
- `boone_trade_values` source: discovered through the rankings hub, with a
  manual URL override.
- New `ffstartsit values` command, `values.yml` workflow, `values.html` page,
  and ChatOps `/values`.
- Per league, your roster shows **Richard | Boone | FP ROS** values, positional
  ranks under each, and a "charts disagree" flag. Large gaps are buy-low or
  sell-high candidates.
- The trade finder judges each idea per chart and shows both verdicts.
- Discord: the full table, one message per league.

### 5. Plumbing and cleanup
- **Pages:** each workflow commits its own page to a `site` branch and deploys
  the branch, so a workflow no longer rebuilds the other pages. That removes the
  sibling rebuild (and its duplicate Odds API calls) instead of adding a third.
- **Rehearsal window:** `season.is_rehearsal_window` reads the **Central** date,
  so a Tuesday-afternoon cron delayed past UTC midnight still counts as Tuesday.
- **ChatOps:** `/values` and an article-URL override for each source.
- **Retire superseded paths:** the "Preferred journalists" section (its
  FantasyPros paid path stays available but dormant), the standalone Boone
  disagreement block, and the quote-only column blocks (quotes become a "why"
  line under each board row).
- Correct the CBS section of `CLAUDE.md` with the finding above. Remove
  `docs/ANALYST_COMPARE_*` once this plan supersedes them.

## Schedule (UTC crons; Central Standard Time shown)

GitHub has delayed runs on this repo by up to about 8 hours, so each slot is
set by when it starts. Retry slots run only when the earlier run reported a
source as "not posted yet". They re-post to Discord only if they found more
than the first run did.

| Run | Cron (UTC) | Starts (CST) | Why then |
|---|---|---|---|
| Thu start/sit | `0 15 * * 4` (existing) | 9:00 am | before Thursday night's game |
| **Sat start/sit** | `0 16 * * 6` | 10:00 am | worst case ~6 pm Sat (owner OK with that) |
| Sun start/sit ×2 | `0 11 * * 0`, `0 13 * * 0` (existing) | 5:00 / 7:00 am | before kickoff |
| **Tue waivers** | `30 21 * * 2` | 3:30 pm | after Richard (~19:00 UTC) and Eisenberg (~20:40 UTC) |
| Tue retry | `30 1 * * 3` | 7:30 pm Tue | if a column was late |
| **Wed values** | `30 22 * * 3` | 4:30 pm | after Boone's charts (~21:15 UTC) |
| Wed retry | `30 2 * * 4` | 8:30 pm Wed | if the charts were late or the hub lagged |

The sites appear to publish on Eastern time, and both they and these UTC crons
shift together at daylight saving, so the gaps hold year-round.

## Decisions so far

- Expert sources are side by side only, with no blend weight.
- Yahoo leagues are deferred.
- Central time; delivered to Discord and Pages.
- Tuesday and Wednesday: one board per league, with full tables on Discord.
- In redraft, ROS value and trade value are treated as the same thing.
- "CBS average" means the staff consensus on `/fantasy/football/rankings/`.

## Answered questions

1. **Start/sit columns.** Discord shows the CBS average only; Pages shows all
   five (FP | Boone | CBS | Eisenberg | Richard).
2. **Half-PPR leagues.** CBS has no half-PPR weekly list, so those leagues show
   CBS's PPR list with the column labelled **"CBS (PPR)"**. It's never blank and
   never passed off as half-PPR.
3. **Retry runs.** Yes: the Tuesday and Wednesday retries post a second Discord
   message when they find something the first run missed.
