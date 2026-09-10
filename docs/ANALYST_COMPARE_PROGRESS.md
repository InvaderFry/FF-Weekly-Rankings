# Analyst comparison progress

Status: **Implemented and verified live; awaiting repository-variable enablement.**

Follow-up: [verified widget transport proposal](ANALYST_COMPARE_TRANSPORT.md).
Yahoo embeds a public FantasyPros partner feed with response-asserted Boone
attribution, week and scoring. Twelve offline evidence tests pass. A browser
check found that the Full-PPR widget initially requests HALF, so the proposed
adapter must select and verify scoring explicitly. The Phase 0 findings below
are preserved as the historical checkpoint, superseded where noted by the
transport proposal.

The implementation plan is saved in [ANALYST_COMPARE_PLAN.md](ANALYST_COMPARE_PLAN.md).
Follow its settled scope and safety requirements when resuming.

## Completed

- Read the plan and repository guidance in `CLAUDE.md`.
- Measured live Yahoo responses on 2026-09-10 UTC (2026-09-09 Chicago).
- Retrieved the author index, untokened FLEX, QB, Full-PPR FLEX, and Full-PPR WR
  pages. All returned HTTP 200 with substantial HTML bodies (671–848 KB).
- Saved exact response bodies as deterministic gzip files in
  `tests/fixtures/yahoo_phase0/`, with byte lengths, SHA-256 hashes and measured
  counts in `manifest.json`. These are evidence fixtures, not successful parser
  fixtures. Decompress with Python `gzip.decompress` or `gzip -dc`.

## Phase 0 findings

The requested FLEX page, QB page, Full-PPR FLEX page and Full-PPR WR page all
contain **zero `<table>` tags, zero `<tr>` tags and none of the checked expected
player names** (Jahmyr Gibbs, Josh Allen, Ja'Marr Chase). No `root.App.main` or
`__NEXT_DATA__` marker appears. Escaped table markup was also absent from the
Full-PPR FLEX response. Yahoo sends Next.js streaming page data, but the expected
player names are absent from the entire response, including its scripts.

The untokened FLEX page does contain both scoring headings. Its Half-PPR list
contains plain labels (QBs, RBs, WRs, TEs, FLEX, D/ST, Kickers), **without links**.
The Full-PPR list contains article links. This does not positively identify the
untokened FLEX ranking set, and no rows are available to compare scoring orders.
The author index exposes all five full-PPR URLs from plan section 2.1 in embedded
data; it does not expose a complete usable half-PPR family.

Article JSON-LD does assert Justin Boone as author and Week 1 in each headline.
Publication dates are September 8, 2026 (untokened FLEX 16:55:55Z, QB 16:52:17Z,
Full-PPR FLEX 17:07:35Z, Full-PPR WR 17:07:26Z). Attribution metadata is available;
rankings are not. A Position column and standard-scoring coverage could not be
established from these responses.

Per section 3, Branch C: “Stop, and report back rather than substituting a
different source.” The transport as planned cannot extract rankings from the
observed responses. This is not a network-access failure. The measurements do
not establish which browser-side mechanism supplies the visible table.

## Remaining work / resume instructions

1. Implement the verified partner-widget transport described in the follow-up
   proposal, including response-level identity and scoring checks. Do not invent successful fixtures, infer half-PPR scoring,
   substitute consensus, or fall back to CBS.
2. Obtain real full/half WR, universal QB, FLEX and week-mismatch fixtures;
   positively verify scoring groups and positional columns.
3. Implement discovery, verification, parsing, positional derivation and caching
   with offline tests (plan section 4).
4. Implement pure conflict detection and tests, preserving notes and close_call.
5. Wire report/CLI per-scoring fetches and per-league data status; pin results-log
   isolation with a regression test.
6. Implement HTML/Markdown rendering; config, docs and workflow variables, off
   by default. Run offline suite and both workflow checks.
7. Prove a live parse before repository-variable enablement; observe Thursday
   and Sunday runs before tuning the gap floor.

No runtime files, workflow variables, blend weights, logs, or published pages
were changed. No runtime test run is warranted for this documentation/evidence
checkpoint; fixture integrity was checked against the manifest.

## Implementation checkpoint 1

- Added `sources/analysts.py`: verified Yahoo hub/article discovery, explicit
  scoring buckets, response-level single-contributor attribution, individual
  ranks, full-list positional fallback, shared per-run and three-hour disk
  caches, graceful failures and per-league status text.
- Extracted shared article metadata checks; existing waiver-column verification
  retains its preseason date-window requirement.
- Captured the real weekly hub: its two widgets explicitly cover RB/WR/TE/FLEX
  in both scorings, plus universal QB/DST/K. Normal discovery needs only the
  author index and hub, then one data request per position/scoring.
- Validation: 48 source/evidence tests and 23 waiver-column regression tests pass.
  Live adapter smoke test used a synthetic three-player roster, produced the
  observed Taylor/Achane RB5/RB6 reversal and reused the universal QB fetch.
  No external writes or repository-variable changes.
- Remaining: conflict engine/model, report/CLI wiring and log isolation tests;
  HTML/Markdown and status regressions; settings, docs, workflow variables; full
  offline suite and both workflow checks. Keep feature off by default.

## Implementation checkpoint 2

- Added pure top-pair and actual starter-boundary disagreement detection and
  the display-only `Recommendation.analyst_conflicts` field.
- Report/CLI share a fetcher across leagues and pass each league's scoring.
  Sample-data runs withhold comparisons and explain why in Data status.
- HTML warnings use a distinct analyst style; small inversions are quiet notes
  below the table. Markdown renders both severities with the same wording.
- Settings accept `FF_ANALYSTS=boone` and a finite non-negative gap (default 5),
  with warned fallbacks for invalid input; off by default.
- Tests cover multi-league sharing, custom slots, no score/close-call/notes
  changes, log isolation, escaping, severity, silence and status deduplication.
- Validation: full offline suite passed (797 tests) before final cosmetic and
  sample-status adjustments. Remaining: docs/env/workflow wiring, focused checks
  for final changes, workflow checks and a final live rendering smoke test.

## Implementation checkpoint 3

Picked up after the previous session stopped mid-step-6 with everything
uncommitted. Nothing was broken; the work was green as left.

- Confirmed the working tree state: docs/env/workflow wiring (`.env.example`,
  `README.md`, `docs/SETUP.md`, and the `env:` blocks of all three workflows) is
  written, plus a hardening pass on `sources/analysts.py` (per-week page cache
  key, cached-value shape check, scoring-heading contradiction rejection,
  lazy OVERALL fallback that resolves the original kind) and ten new edge-case
  tests in `tests/test_analysts.py` with their real-response fixtures.
- Full offline suite: **807 passed**. Fixture manifest verified — all 43
  entries match their recorded SHA-256 and byte length (hashes are of the
  *decompressed* body; `.gz` files will not match on their compressed bytes).
- Both workflow checks pass: `scripts/check-workflows.py` clean, and
  `actionlint -ignore 'unexpected key "queue" for "concurrency" section'` clean
  with shellcheck on PATH.
- **Live rendering smoke test done** (step 7's first half). Local `.env` carries
  no league credentials, so a full `report` run is not possible here; instead a
  synthetic roster was run end-to-end against the live site. Real fetch →
  `detect_conflicts` → `analyst_conflict_text` produced, for Week 1 2026:
  `⚠️ Justin Boone would flip your last starting spot: Jahmyr Gibbs (his RB1)
  over Breece Hall (his RB15).` Status line named the scoring set and the
  article publication date correctly for both a half- and a full-PPR league.
  `rec.notes` and `rec.close_call` were asserted unchanged.
- **Verified the plan's named silent-correctness risk (§2.1) does not bite.**
  The live half- and full-PPR sets are genuinely different documents, not one
  document served twice: RB half n=102 / full n=50 with Taylor and Achane
  swapped at 5/6; FLEX half n=149 / full n=155 with the receivers rising in
  full PPR exactly as the format implies. The adapter's provenance reported the
  requested scoring in each case.
- Open question §12 Q1 (standard scoring) is **moot for this repo**: the only
  configured league is `half`. No `std` league exists to withhold from.

## Remaining work

1. Enable via the repository variable `FF_ANALYSTS=boone` (and optionally
   `FF_ANALYST_MIN_GAP`). Ships off; nothing renders until this is set.
2. Watch one Thursday and one Sunday run, then tune `FF_ANALYST_MIN_GAP` from
   how often the warning actually fires (§12 Q2).
3. ~~Decide §12 Q3~~ — **resolved: say so in the section.** Implemented as
   `Recommendation.analyst_note`, a second display-only field set in
   `rank_each_position` and rendered as a quiet line by both the digest and the
   dashboard. It fires **only** on the not-posted-yet reason
   (`analysts.not_published_yet`), because that is the one `unavailable` reason
   that is position-specific; every other reason reads identically at every
   position, so repeating it per section would be the duplication
   `_merge_source_status` exists to prevent, and Data status already carries it
   once. Pinned by tests in `test_analyst_integration.py`,
   `test_report_markdown.py`, `test_html.py`, and — for the log-isolation trap —
   `test_results_log_roundtrip.py`. Suite: **813 passed**.

Out of scope and deliberately not done: Discord rendering, the waiver page,
CBS as a per-analyst source, any `FF_WEIGHT_*` entry.
