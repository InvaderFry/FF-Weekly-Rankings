# Analyst comparison progress

Status: **Blocked at Phase 0, Branch C. No production implementation.**

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

1. Resolve the source-data blocker with the owner. A revised, verified transport
   or real Yahoo responses containing the rows is required before production
   implementation. Do not invent successful fixtures, infer half-PPR scoring,
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
