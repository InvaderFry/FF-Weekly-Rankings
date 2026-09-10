# Verified alternative: Yahoo's FantasyPros partner widget

Measured 2026-09-10 UTC. This is a transport proposal and evidence checkpoint;
production implementation and enablement are still outstanding.

## Result

A usable public transport exists. Yahoo's article embeds a FantasyPros partner
widget. Its data response explicitly identifies Justin Boone, season, week,
position, scoring, and his individual rank for each player. No API key or login
was used. The existing paid FantasyPros API's 403 does not apply to this endpoint.

The original Phase 0 conclusion remains correct for direct article parsing:
there are no ranking rows in its HTML. However, its Next.js stream contains a
`rankingPro` node with the widget URL and configuration. Following that reference
reaches the data. This is the article's own external data source.

## Observed chain

1. The saved Yahoo article has verified JSON-LD byline, headline and date.
2. JSON-decoding its `self.__next_f.push(...)` strings reveals `rankingPro` nodes.
   Use a JSON decoder with end offsets (or bracket matching), not a regex ending
   at the first bracket, and never execute the JavaScript.
3. The node points at `https://partners.fantasypros.com/external/widget/fp-widget.php`.
   The response contains a `#fp-widget` div and loads
   `https://cdn.fantasypros.com/js/fp-widget-2.0.js?v=1`.
4. The widget's `buildRankingsURL` requests the partner endpoint below via JSONP.
   Chromium independently rendered 149 half-PPR FLEX rows and 32 QB rows using
   this exact request path. These were direct navigations to the article's
   embedded widget URL. An additional full Yahoo article navigation did not
   issue a FantasyPros request during the observation window, so it is not
   evidence that the whole article rendered its widget in this environment.

Example full-PPR FLEX data request (derived from the widget's request builder,
with PPR selected explicitly):

```
https://partners.fantasypros.com/api/v1/consensus-rankings.php?callback=FPW.rankingsCB&position=FLX&sport=NFL&year=2026&week=1&experts=show&id=1663&type=ST&scoring=PPR&filters=317&widget=ST
```

Despite the endpoint filename, this response has exactly one expert:
`expert_names = {"317": "Justin Boone"}`, `total_experts = 1`, `filters = "317"`.
Rows carry `experts["317"]`. Those are the values to consume; never use
`rank_ecr`, `rank_ave`, or other aggregate fields as a substitute.

**The two IDs have different roles.** `1663` is the widget configuration's
`expert` / request `id`; `317` is the requested rank contributor (`proFilters` /
`filters`) and the response's named expert key. Changing `id` to an invalid value
still returned Boone's rows. Changing `filters` to an invalid value returned
zero rows and no named expert. Thus checking the URL's `id` alone repeats the
false-attribution failure the original plan was designed to avoid.

## Scoring issue found in the real embed

The untokened FLEX node has `half_positions="FLX"`, `ppr_positions=""`.
The full-PPR FLEX node has `ppr_positions="FLX"`, `half_positions=""`.
Both have `scoring="HALF"`. A real browser opening the exact full-PPR widget URL
initially requested HALF and rendered the same 149-row half-PPR table. Do not
claim the displayed default on that article proves full-PPR scoring.

Explicit PPR at the data endpoint returned 155 rows with `scoring="PPR"`, while
HALF returned 149 with `scoring="HALF"`. Their ordering differs:

| Player | Half-PPR FLEX | Full-PPR FLEX |
| --- | ---: | ---: |
| Jahmyr Gibbs | 1 | 1 |
| Bijan Robinson | 2 | 3 |
| Ja'Marr Chase | 3 | 2 |
| Jonathan Taylor | 8 | 10 |
| De'Von Achane | 10 | 9 |

The RB order also differs: Taylor is RB5 and Achane RB6 in half-PPR; those ranks
reverse in full-PPR. This pair can prove correct scoring after positional
rank derivation. The half/full WR responses have different coverage (146 vs 75)
but **identical ranks for all 75 shared players**. The original plan's claim
that the WR pair necessarily differs in order is unproven and must not be
encoded as a fabricated test expectation.

## Proposed production contract

Keep the existing annotation-only design, disabled default, renderers, conflict
rules, log isolation, per-league status, and three-hour cache TTL. Replace only
the article-table transport and the assumptions that measurements disproved:

1. Discover current articles from Yahoo's index/links, retaining byline, week and
   season checks. Decode article data structurally to find the actual widget.
2. Require one unambiguous widget with expected NFL/weekly/year/week metadata.
   Allowlist the HTTPS widget host and path; reject contradictory metadata,
   redirects to unexpected hosts, multiple conflicting embeds or unknown kinds.
3. Resolve the supported position/scoring from nonempty `ppr_positions` and
   `half_positions`, corroborated by the article's labeled section. Do not treat
   its generic `scoring=HALF` default as the scoring-specific list's identity.
   An explicitly selected PPR request is justified by the PPR bucket and must
   return PPR. Continue withholding std comparisons. QB/DST/K can use the
   universal bucket, shared between leagues.
4. Build the partner request using the observed widget contract. Request only
   the one contributor named by its filter, not a default expert pool. Pin the
   endpoint host/path; no browser or downloaded JavaScript is needed at runtime.
5. Decode only the exact expected JSONP wrapper with `JSONDecoder.raw_decode`;
   reject trailing executable text. Require response sport, ranking type,
   year, week, position and scoring to match the request. Require exactly one
   expert, that expert's name to equal Justin Boone, and its key to equal the
   requested single filter. Missing metadata means unavailable.
6. Read only each row's `experts[filter]`. Apply the plan's rank, minimum length,
   team/name and position validators, reject duplicate player identities, and
   derive fallback positional ranks across the full list before roster joining.
7. Keep separate article publication and rank update provenance. The response
   carries `expert_pub` and `last_updated_ts`; do not label either as Yahoo's
   article publication. `datePublished` remains the article publication source.
8. Memoize and disk-cache validated responses and failures by analyst, season,
   week, kind and resolved scoring. Include the transport schema in cache keys.
   Revise the old 9/14 request budget: cold reads now require an article and a
   data request per list, plus discovery. Fetching the widget HTML or script is
   unnecessary once its contract is implemented. Keep OVERALL lazy.

A Yahoo partial index can still lack half-PPR positional links. Use an explicitly
identified half-PPR FLEX article as the RB/WR/TE fallback where discoverable;
never synthesize an opaque Yahoo article ID or manufacture a missing link.
Current end-to-end discovery coverage is still an implementation prerequisite.

## Verification and artifacts

`tests/fixtures/boone_transport/` contains actual widget HTML, partner JSONP,
request URLs/statuses, the downloaded widget script, extracted article nodes,
and focused browser observations. Raw responses are compressed losslessly;
`manifest.json` records decoded byte sizes and SHA-256 hashes. Browser files are
observations, and node files are extracted data, not raw response fixtures.

Offline tests in `tests/test_boone_transport_evidence.py` pin:

- response attribution, scoring, week, position and individual ranks;
- real FLEX and positional scoring differences;
- the observed absence of shared-WR rank differences;
- invalid-filter and unpublished-week empty responses;
- why publisher ID cannot verify attribution;
- the full-PPR widget's initial HALF request;
- integrity of every saved evidence artifact.

Validation: **12 tests passed**. These verify the transport findings, not the
future production fetcher. Browser tooling was installed only in a temporary
venv under `/tmp`; repository dependencies are unchanged.

## Remaining implementation

Build the fail-closed source adapter and its malformed/mismatched response tests;
complete discovery coverage; implement conflict detection and report/CLI wiring;
render status and conflicts; add settings, docs and workflows; run the full
suite and workflow checks; prove a live run before enablement. The original
plan's protected blend/log files remain protected.
