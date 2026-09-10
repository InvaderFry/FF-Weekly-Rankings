# Plan: analyst comparison (Justin Boone) on the start/sit page

Status: **plan only — nothing implemented.** Written 2026-09-10.

This document is self-contained. It assumes the reader knows `CLAUDE.md` but has
not seen the conversation that produced this plan.

---

## 1. What we are building, and what we are not

**Goal.** On the published start/sit page (`index.html`), when a preferred
analyst would set the lineup differently from the blend, say so. Stay silent
otherwise.

**Decisions already taken** (do not relitigate without asking the repo owner):

| Decision | Value | Why |
|---|---|---|
| Which page | `index.html` only | The start/sit list lives there. `waivers.html` is out of scope. |
| Which analysts | **Justin Boone (Yahoo) only** | The only source that can be attributed honestly today — see §2. |
| What "compare" means | **Disagreements only** | Quiet by default. No always-on analyst columns, no new section, no third page. |
| Severity | **Note by default, warning when material** | See §6 for the exact rule. |
| Blend impact | **None** | This is an annotation layer, exactly like `sources/journalists.py` and `waivers/columns.py`. |

**Explicitly out of scope for this change** (each is a reasonable follow-on, none
is part of this work): CBS as a per-analyst source; any `FF_WEIGHT_*` entry;
Discord alerts; the waiver page; a full "your lineup vs. his lineup" slot diff;
the `backtest` hit-rate split; a standalone `analysts.html`.

---

## 2. Why Boone, and why not CBS

**CBS cannot be attributed per-analyst and must not be scraped for rankings.**
`CLAUDE.md` records a measurement from 2026-09-08: on
`cbssports.com/fantasy/football/rankings/ppr/<POS>/<slug>/`, the slugs
`jamey-eisenberg`, `dave-richard`, `consensus`, and a `not-a-real-analyst` slug
invented on the spot all return **byte-identical** data. Per-analyst filtering is
client-side JavaScript; the server ships one consensus document to every URL.

That shape fails **open**: a dead or misspelled slug is indistinguishable from a
working one, so a scraper would publish CBS house consensus under a named byline
forever and nothing in the response could catch it. Do not attempt it. If CBS
ranks are ever wanted, the only honest route is a data endpoint that names the
analyst *in its response*.

**Boone's Yahoo articles are a different shape.** They are per-week,
per-position articles whose byline, week and season are assertable from the
document body — which is what lets the fetcher fail **closed**.

### 2.1 He publishes two scoring sets, and they are not interchangeable

This is the single most important structural fact about the source. Each week he
publishes **two complete ranking sets**, side by side, presented on the page
under their own headings:

| Heading | Lists under it |
|---|---|
| *Justin Boone's Half-PPR Rankings* | QBs, RBs, WRs, TEs, FLEX, D/ST, Kickers |
| *Justin Boone's Full-PPR Rankings* | Overall, RBs, WRs, TEs, FLEX |

**QB, D/ST and Kicker are universal** — scoring does not change them, so they are
published once and apply to either set. **RB, WR, TE, FLEX and Overall are
scoring-specific** and must be read from the set matching the league.

Getting this wrong is a silent correctness bug, not a cosmetic one. This repo is
multi-league with per-league scoring (`FF_LEAGUE_SCORING`, `LeagueProfile.scoring`),
so a single run can need **both** sets at once. Serving half-PPR receiver ranks to
a full-PPR league would produce real, plausible numbers under Boone's byline that
are not the ranks he gave that format — the same class of false attribution as the
CBS problem, arriving by a different route.

Confirmed Full-PPR URLs (Week 1, 2026):

```
/fantasy/article/2026-fantasy-football-full-ppr-rankings-justin-boones-top-players-for-week-1-170728422.html          (Overall)
/fantasy/article/2026-fantasy-football-full-ppr-rankings-justin-boones-top-running-backs-for-week-1-170730179.html    (RB)
/fantasy/article/2026-fantasy-football-full-ppr-rankings-justin-boones-top-wide-receivers-for-week-1-170726544.html   (WR)
/fantasy/article/2026-fantasy-football-full-ppr-rankings-justin-boones-top-tight-ends-for-week-1-170733340.html       (TE)
/fantasy/article/2026-fantasy-football-full-ppr-rankings-justin-boones-flex-rankings-for-week-1-170735946.html        (FLEX)
```

Other observed URLs, carrying **no scoring token**:

```
/fantasy/article/2026-fantasy-football-rankings-justin-boones-flex-rankings-for-week-1-165555810.html   (FLEX, scoring unstated)
/fantasy/article/fantasy-football-justin-boones-week-1-qb-rankings-165217932.html                       (QB — universal)
/fantasy/article/fantasy-football-week-1-rankings-justin-boones-top-overall-players-165054284.html      (Overall, scoring unstated)
```

Note the two FLEX articles carry different ids (`165555810`, `170735946`), so
they are genuinely two distinct lists, not one article under two links.

**The untoken'd scoring-specific articles are probably the half-PPR set, and
"probably" is not good enough.** Phase 0 must establish this positively — see
§3. Until it does, an untoken'd RB/WR/TE/FLEX/Overall list has *unknown* scoring
and must not be used for any league.

### 2.2 Discovery is mandatory

**Note the trailing numeric id** on every URL above. It is opaque and cannot be
constructed, so article URLs **must be discovered** from the author index at
`https://sports.yahoo.com/author/justin-boone/`. This is the same discovery
problem `waivers/columns.py:find_column_url` already solves for his waiver
column, against the same index page.

**Why this feature is worth building at all:** the existing "Preferred
journalists" section is permanently dead. Per `docs/SETUP.md`, FantasyPros'
per-expert ranks require the paid API, the key returns HTTP 403, and the
decision on this repo is not to buy it. `JournalistFetcher` therefore produces
nothing. This work is a new transport for the same idea.

---

## 3. Phase 0 — measure first. This phase is blocking.

The table the repo owner sees on the page is:

```
Rank  Player            Position  Team  Opponent
1     Jahmyr Gibbs      RB        DET   vs. NO
2     Bijan Robinson    RB        ATL   @ PIT
3     Ja'Marr Chase     WR        CIN   vs. TB
```

**It is not yet known whether those rows are in the served HTML or injected by
JavaScript.** This was not verifiable from the environment where this plan was
written (the network egress proxy blocks both `sports.yahoo.com` and
`cbssports.com`). Everything downstream depends on the answer, so measure it
before writing any production code.

Run this from a machine with normal network access and save the output:

```bash
mkdir -p /tmp/boone && cd /tmp/boone
UA='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'

curl -sS -A "$UA" -o index.html "https://sports.yahoo.com/author/justin-boone/"
curl -sS -A "$UA" -o flex.html  "https://sports.yahoo.com/fantasy/article/2026-fantasy-football-rankings-justin-boones-flex-rankings-for-week-1-165555810.html"
curl -sS -A "$UA" -o qb.html    "https://sports.yahoo.com/fantasy/article/fantasy-football-justin-boones-week-1-qb-rankings-165217932.html"

# Q1: are the ranking rows server-rendered?
grep -c "Jahmyr Gibbs" flex.html
grep -o "<table[^>]*>" flex.html | head
grep -o "<tr[^>]*>" flex.html | wc -l

# Q2: is there an embedded JSON blob instead?
grep -o "root.App.main" flex.html | head
grep -o "__NEXT_DATA__" flex.html | head

# Q3: is the byline + date in ld+json, as columns.py already expects?
grep -o '"datePublished":"[^"]*"' flex.html | head
grep -o '"author":{[^}]*}' flex.html | head

# Q4: which ranking articles does the index actually link, and how are they named?
grep -o 'https://sports\.yahoo\.com/fantasy/article/[^"\\<>]*' index.html \
  | grep -iE 'rank|top-(overall-|running-|wide-|tight-)?players?|backs|receivers|ends' | sort -u

# Q5 (CRITICAL): does an article carry the two labeled scoring groups, with the
# links grouped under them? This is the server asserting which set each list
# belongs to — far better than inferring scoring from a slug.
grep -o "Half-PPR Rankings" flex.html | head
grep -o "Full-PPR Rankings" flex.html | head
# Dump the surrounding markup so the grouping structure can be read:
python3 - <<'PY'
import re, sys
html = open('flex.html', encoding='utf-8', errors='replace').read()
for m in re.finditer(r'(Half|Full)-PPR Rankings', html):
    print('---', m.group(0), 'at', m.start())
    print(html[m.start()-200:m.start()+2500])
PY

# Q6: is the untoken'd FLEX list actually the half-PPR one? Compare its top 10
# against the confirmed full-PPR FLEX list. If they differ, they are two sets;
# if identical, the untoken'd family is a duplicate and can be ignored.
curl -sS -A "$UA" -o flex_untokened.html \
  "https://sports.yahoo.com/fantasy/article/2026-fantasy-football-rankings-justin-boones-flex-rankings-for-week-1-165555810.html"
```

### The three branches

- **Branch A — server-rendered rows** (`grep -c "Jahmyr Gibbs"` > 0 and `<tr>`
  count is ~the list length). Parse the HTML table. Proceed to §4.
- **Branch B — embedded JSON** (`root.App.main` or `__NEXT_DATA__` present and
  the blob contains player names). Parse that instead. **Extract the blob with a
  bracket-matched scan, not a regex** — this is the exact lesson
  `sources/experts.py:_json_array_at` was written for: a `.*?` regex stops at
  the first `]` inside an image URL. Reuse `_json_array_at` if its shape fits;
  otherwise write the equivalent and say so in a comment.
- **Branch C — neither** (names appear nowhere in the served bytes). Then the
  page is client-side-only and **this approach cannot work honestly**. Stop, and
  report back rather than substituting a different source. Do not fall back to
  CBS. Do not fall back to a consensus list presented under Boone's name — that
  is precisely the failure §2 exists to prevent.

**Also record from Phase 0**, because §4 needs them:

1. **The scoring-group structure (Q5).** Whether the two headings — *Justin
   Boone's Half-PPR Rankings* and *Justin Boone's Full-PPR Rankings* — appear in
   the served markup with their links grouped beneath them, and what that markup
   looks like. If they do, **that grouping is the discovery mechanism** (§4.2):
   the page itself states which set each link belongs to, which beats inferring
   scoring from a slug for exactly the reason §2 gives about response-asserted
   attribution.
2. **The identity of the untoken'd family (Q6).** Whether the untoken'd FLEX
   article is the half-PPR list, a duplicate of the full-PPR one, or something
   else. Until this is answered positively, untoken'd scoring-specific lists are
   unusable.
3. Which ranking articles the index links each week and their exact slug shapes,
   for the allowlist in §4.2.
4. Whether `ld+json` carries `author.name` and `datePublished` on ranking
   articles (it does on his waiver column — `columns.py:preseason_article_verified`
   relies on it).
5. Whether the FLEX list carries a `Position` column (the sample says yes — this
   matters for the fallback in §4.4).
6. Whether **standard (non-PPR)** rankings exist anywhere. The repo supports
   `std` scoring; Boone appears to publish only half and full PPR. See §12.

**Save the fetched pages as test fixtures.** Every fixture must be a page Yahoo
was actually observed to serve, never a hand-written approximation — the same
discipline as `tests/fixtures/espn_international_2026.json`.

---

## 4. New module: `ff_startsit/sources/analysts.py`

Lives beside `journalists.py` and `experts.py`, which are also non-`Signal`
modules in `sources/`. **It is not a `Signal`.** It gets no `FF_WEIGHT_*` entry,
no `pipeline.build_signals` registration, and no `_validate_weights` change. The
"four places" rule in `CLAUDE.md` does not apply to it.

Model it closely on `waivers/columns.py:ColumnFetcher`, which solves the same
discovery/verify/parse/degrade problem against the same author index.

### 4.1 Data model

```python
@dataclass(frozen=True)
class AnalystSource:
    name: str            # "Justin Boone" — as rendered, and as matched against the byline
    index_url: str       # https://sports.yahoo.com/author/justin-boone/
    base_url: str        # https://sports.yahoo.com

#: Lists whose content does not depend on scoring. Published once, valid for
#: every league. Everything else must be read from the league's own set.
UNIVERSAL_KINDS = frozenset({"QB", "DST", "K"})

@dataclass(frozen=True)
class RankingList:
    """One published list: what it covers, for which scoring, and from where."""
    kind: str            # "QB" | "RB" | "WR" | "TE" | "FLEX" | "OVERALL" | "DST" | "K"
    url: str
    #: "ppr" (full) | "half" | "" for a universal list. NEVER "" for a
    #: scoring-specific kind — an unresolved scoring makes the list unusable.
    scoring: str
    published: Optional[date]
    rows: list[ExternalRow]   # value = the rank as printed in the list

    @property
    def universal(self) -> bool:
        return self.kind in UNIVERSAL_KINDS

@dataclass
class AnalystRanks:
    """One analyst's week for ONE scoring, joined to a roster.

    Built per scoring, because a run with mixed-scoring leagues needs more than
    one of these. Universal lists are shared between them (§8).
    """
    analyst: str
    scoring: str
    lists: list[RankingList]
    #: position -> {player.key -> positional rank}. See 4.4 for how this is derived.
    by_position: dict[str, dict[str, float]]
    #: position -> (RankingList.kind, scoring actually used), for the status line.
    provenance: dict[str, tuple[str, str]]
```

`ExternalRow` is the existing `data/matching.py` dataclass
(`name, team, position, value, extra`). Reuse it so the roster join is
`match_rows(players, rows)` — the same join ECR uses, which already handles
name normalization and the DEF/DST canonicalization via `player_match_key`.

### 4.2 Discovery — fail closed

```python
def find_ranking_urls(html: str, base_url: str, week: int,
                      season: int) -> dict[tuple[str, str], str]:
    """(kind, scoring) -> url for the ranking articles linked for ``week``.

    ``scoring`` is "" for a universal kind (QB/DST/K) and otherwise one of
    "ppr"/"half". A link whose scoring cannot be established is not returned.
    """
```

Rules, all of them load-bearing:

- **Prefer the page's own scoring groups over slug inference.** If Phase 0's Q5
  confirms the *Half-PPR Rankings* / *Full-PPR Rankings* headings with links
  grouped beneath them, resolve scoring by **which group a link sits in**. That
  is the server stating the fact; a slug token is a convention that can change.
  Parse the grouping by locating each heading and taking the links until the
  next heading — and note this is the same "read the structured block the page
  already ships" move as `experts.ExpertFinder.directory`.
- **Slug tokens are corroboration, not the primary source.** `full-ppr` in a
  slug should agree with the group the link was found under. If they disagree,
  skip the link and warn — a contradiction is not something to resolve by
  picking a side.
- **Reuse the week pattern from `find_column_url`:**
  `re.compile(rf"week[-_]?0*{int(week)}(?!\d)", re.IGNORECASE)`. It already
  handles `week-1` correctly and rejects `week-10` for week 1.
- **Reject a link naming a different season:** the same
  `(?<!\d)(20\d{2})(?!\d)` check `find_column_url` performs. An undated link is
  not evidence of the current week and must not be silently substituted — this
  is how last week's advice gets published as this week's.
- **Classify `kind` by an explicit allowlist, never by "contains the word
  rankings".** A slug that does not classify is skipped rather than guessed at.
  Allowlist, from the confirmed URLs in §2.1:

  | `kind` | slug contains | scoring-specific? |
  |---|---|---|
  | `QB` | `qb-rankings`, `quarterback` | no — universal |
  | `DST` | `dst`, `d-st`, `defense` | no — universal |
  | `K` | `kicker` | no — universal |
  | `RB` | `top-running-backs`, `rb-rankings` | **yes** |
  | `WR` | `top-wide-receivers`, `wr-rankings` | **yes** |
  | `TE` | `top-tight-ends`, `te-rankings` | **yes** |
  | `FLEX` | `flex-rankings` | **yes** |
  | `OVERALL` | `top-players`, `top-overall-players` | **yes** |

  Every positional slug begins `top-`, so a loose `top-` rule would collapse RB,
  WR, TE and OVERALL into one bucket. **Require that exactly one `kind` pattern
  matches** and skip the link when zero or two or more do, rather than taking the
  first hit in dict order. A test should pin each of the five confirmed §2.1
  URLs to exactly one `kind`.

- **A scoring-specific list with unresolved scoring is dropped, not guessed.**
  It does not become "probably half". Dropping it costs one comparison; guessing
  wrong publishes the wrong format's ranks under Boone's name.
- **Map the league's scoring to his sets:** `ppr` → Full-PPR, `half` → Half-PPR.
  `std` has no counterpart — see §12 Q1.
- Yahoo also serves `ca.sports.yahoo.com` mirrors. Pin `base_url` to
  `sports.yahoo.com`, as `columns.py` does.

### 4.3 Verification — the attribution gate

Before a single rank is read, the **article body** must assert all three:

1. `ld+json` `author.name == source.name`
2. the requested week appears in the headline
3. the requested season appears in the headline or `datePublished` falls in that
   season

`columns.py:preseason_article_verified` already does exactly this shape of check
(byline + headline + publication date) — generalize it into a shared helper
rather than writing a second one, and have `columns.py` call the shared version.

If any assertion fails → the list is **unavailable** with reason
`"byline or week could not be verified in the article"`. A URL slug naming Boone
is **not** sufficient. This is the whole defense against the CBS failure mode,
and it must not be softened because "the URL clearly says Boone".

### 4.4 Parse, validate, and derive positional ranks

Parse per the Phase 0 branch into `ExternalRow`s with `value` = printed rank.

**Validate the parse before trusting it.** A bad parse must read as unavailable,
never as a ranking:

- ranks are strictly increasing and start at 1;
- at least 10 rows for a positional list, 20 for FLEX/OVERALL (a paywalled or
  truncated parse yields a handful);
- at least 60% of rows carry a team that `data/teams.py:normalize_team`
  recognizes, and a name of 2+ words;
- for a positional list, every parsed row's position equals that position.

Any failure → unavailable, reason `"rankings table could not be parsed"`.

**Deriving positional ranks — this is the subtle part; get it right.**

The comparison in §5 needs a gap measured in *spots within a position*. Ranks
taken straight off a FLEX or OVERALL list are not on that scale: a 5-spot gap in
a 150-player cross-position list is roughly a 2-spot gap within RB, so a single
`min_gap` applied to raw list ranks would mean different things per list.

So, for each position:

1. **Prefer the positional article.** He publishes one for every position we
   care about — QB, RB, WR, TE, DST, K — so this is the normal path, and the
   FLEX-derivation below is a fallback rather than the main mechanism. Its
   printed rank *is* the positional rank; use it as-is.
2. **Otherwise derive** from FLEX (RB/WR/TE) or OVERALL: filter **the analyst's
   entire published list** to that position, then densely re-rank — his RB1,
   RB2, RB3 … — and use that.

Because the positional articles cover K and D/ST too, **every roster position
can carry a comparison**, which was not true when this plan assumed only a FLEX
list existed. Note the QB / DST / K lists are universal, so they are read once
and reused across leagues of either scoring (§8).

**Filter his whole list, not just your roster.** Restricting to your four backs
and re-ranking them 1-4 destroys the magnitude: every pair would be one spot
apart and nothing would ever be material. His full list is what says your two
backs sit at his RB6 and his RB19.

Record which source fed each position in `AnalystRanks.provenance` so the status
line can say so.

This is the same class of reasoning as `waivers/score.py:depth_ratio`: two
numbers drawn from different populations cannot be subtracted, so put them on a
common scale first.

**A missing rank is not a bad rank.** A player absent from his list is absent —
never treated as ranked last. This is the `score.has_ecr` rule, and it is why
§5 only ever compares two players he ranks *both* of.

### 4.5 Degradation — every failure is distinguishable

Mirror `ColumnFetcher.unavailable`: a `dict[str, str]` of analyst → reason, with
these distinct values. They render identically as "no analyst line", but they are
different things for the reader to do about it, and collapsing them is a bug this
repo has fixed three separate times (`no_adds_reason`, `no_trades_reason`,
`weather._compute_game`'s per-arm notes).

| Reason | What the reader should do |
|---|---|
| `author index unavailable` | Retry / check the site |
| `no Week N ranking article linked` | Wait — he has not published yet |
| `article unavailable (empty or paywalled)` | Nothing; the article is gated |
| `byline or week could not be verified in the article` | Investigate — possible layout change |
| `rankings table could not be parsed` | Investigate — likely layout change |
| `no <half/full> PPR list published for this position` | Wait, or accept the gap |
| `scoring of the linked list could not be resolved` | Investigate — the grouping markup changed |
| `league scoring (std) has no published counterpart` | Nothing; expected — see §12 Q1 |
| `parsed rankings named nobody on this roster` | Nothing; not a failure |

**Never raises.** Every path returns empty and warns to stderr, exactly as
`ColumnFetcher.fetch` does. A broken Yahoo page must not take down a publish run.

---

## 5. Conflict detection: `ff_startsit/engine/analyst.py`

A **new pure module** in `engine/`, which holds pure functions with no I/O. It
must not import anything that fetches.

```python
@dataclass(frozen=True)
class AnalystConflict:
    analyst: str
    position: str
    leader: str            # the blend's pick (player name)
    preferred: str         # the analyst's pick (player name)
    leader_rank: float     # analyst's positional rank for the blend's pick
    preferred_rank: float
    boundary: bool         # True = the last-starting-spot pair, not the top two
    material: bool         # True = warning, False = quiet note

    @property
    def gap(self) -> float:
        return self.leader_rank - self.preferred_rank


def detect_conflicts(rec: Recommendation,
                     ranks: Mapping[str, float],
                     analyst: str,
                     min_gap: float,
                     starter_count: Optional[int] = None) -> list[AnalystConflict]:
    ...
```

Algorithm, deliberately mirroring `blend._flag_close_call` /
`_flag_starter_boundary` so the two stay legible side by side:

1. `scored = [s for s in rec.scores if s.final is not None]`; return `[]` if
   fewer than 2.
2. Pairs to examine:
   - the top two: `scored[0], scored[1]`;
   - **and** the starter boundary `scored[starter_count-1], scored[starter_count]`
     when `starter_count and starter_count >= 2 and len(scored) > starter_count`
     — copy that guard exactly from `_flag_starter_boundary`. In a league
     starting N at a position, rank N vs N+1 is the pair that actually sets the
     lineup.
3. For each pair `(a, b)` with `a` the blend leader: skip unless `ranks` has a
   value for **both**. Inversion when `ranks[b] < ranks[a]` (lower rank = better).
4. `material = (ranks[a] - ranks[b]) >= min_gap`.

`starter_count` must come from `report.starter_counts(slots)` — the same value
`rank_each_position` already computes and threads into `recommend`. Do not
hardcode a second copy of the slot template; `waivers.build._lineup_keys`
records what that costs.

### Two hard constraints on this function

- **It must never set `rec.close_call`.** That flag is written into
  `results_log.jsonl` and drives `backtest`'s confident-vs-close-call hit-rate
  split — the honesty check on close-call flagging. Setting it from scraped
  article data would corrupt the measurement of whether the flagging works.
- **It must never append to `rec.notes`.** This is the single most likely
  mistake, because appending to `notes` is the obvious move and it looks
  harmless. `results_log.log_recommendation` writes `"notes": rec.notes` into
  every logged row. Analyst text in `notes` therefore lands in the append-only
  calibration corpus, where it does not belong and cannot be removed.

Conflicts live in a **new field on `Recommendation`**:

```python
analyst_conflicts: list[AnalystConflict] = field(default_factory=list)
```

`log_recommendation` builds its row from an explicit dict of named fields, so a
new field on the dataclass does **not** leak into the log. That property is what
makes this safe — preserve it by not adding the field to that dict.

---

## 6. Severity rule and rendering

### The rule

Chosen deliberately, and it mirrors the two-floor pattern already in
`blend.py` (`min_disagree_weight` + `close_call_threshold`): a disagreement has
to be both *real* and *at a decision point* before it shouts.

- **Warning** (callout, same visual slot as the close-call callout) when the
  conflict is on the top two **or** the starter boundary **and**
  `gap >= FF_ANALYST_MIN_GAP` (default **5** positional spots).
- **Note** (a quiet line under the position table, like `flat_signal_note`) when
  there is an inversion but the gap is under the floor.
- **Nothing at all** when he agrees, or when he ranks fewer than both players.

Why a gap floor is required: without it, Boone ranking your two backs RB14 and
RB15 would "disagree" every single week. A flag that fires on everything is not
a warning — the exact reasoning behind `min_disagree_weight` in `blend.py`.

Why the default is 5: a 5-spot positional gap is roughly a tier. Tune it after a
few live weeks; it is a config value precisely so tuning does not need a code
change.

### Wording

Keep the analyst's own numbers in the text — the reader can then judge for
themselves, which is the whole point.

- Top-two warning:
  `⚠️ Justin Boone disagrees: he starts Breece Hall (his RB6) over Jahmyr Gibbs (his RB19).`
- Boundary warning:
  `⚠️ Justin Boone would flip your last starting spot: Jauan Jennings (his WR22) over Rome Odunze (his WR31).`
- Quiet note:
  `Justin Boone has these two within 2 spots (Gibbs RB6, Hall RB8), edge to Hall.`

### Where to render

Four renderers exist for the start/sit path. This change touches **two**, because
Discord and the interactive terminal table are out of scope:

1. **`output/html.py:_position_section`** — the dashboard. Add the callout
   beside the existing close-call callout. Use a distinct CSS class
   (`callout analyst`) so it can be styled differently; do **not** reuse the
   close-call class, or the two become indistinguishable to a reader and the
   product's own warning loses its meaning.
2. **`output/render.py:render_markdown`** — the digest, which is what the issue
   comment and the markdown path publish.

Both must handle `rec.analyst_conflicts == []` by rendering nothing.

Do not touch `output/discord.py` in this change (follow-on), and leave
`render_table` (interactive) alone unless it is free.

---

## 7. Data status

The repo owner explicitly asked: when the rankings are not available, say so in
the **Data status** block.

The mechanism already exists and needs no change to `data_status.py`. Every
`Recommendation` carries `source_status: list[tuple[tuple, str]]` — `(identity,
line)` pairs — and `finish_status` keys a dict on the identity, keeping the
newest line. That dedupe is why the identity travels with the line; see
`CLAUDE.md` on the 18-duplicate-ECR-lines regression.

Append one entry per analyst **per league**, identity
`("analyst", "Justin Boone", league_label)` — the scoring differs per league, so
a single shared line would be wrong for at least one of them.

**The line must name the scoring set it read.** This is the one fact a reader
needs to trust the comparison, and it is the fact most likely to be silently
wrong:

- Available:
  `AndyLOT: Justin Boone Week 1 Full-PPR rankings (published 2026-09-04) → RB, WR, TE, FLEX; universal lists → QB, K, DST`
- Partial:
  `AndyLOT: Justin Boone Week 1 Full-PPR rankings → RB, WR, TE; no TE list published yet`
- Unavailable:
  `AndyLOT: no Justin Boone Week 1 rankings (author index listed no week-1 ranking article)`
- Scoring mismatch:
  `Dynasty: Justin Boone publishes no standard-scoring rankings — comparison withheld for this league`

Two rules from `data_status.py` that apply here:

- **Never a scoring input.** `finish_status` only reports what already happened.
- **Do not imply a freshness that was not measured.** Report
  `RankingList.published` only when it came from the article's own
  `datePublished`; never substitute the fetch time.

**Publication cadence.** Boone publishes roughly **Thursday, sometimes Saturday,
and Sunday**, and re-publishes rather than edits in place — so each drop is a new
article with a new id, which the discovery pass picks up naturally. A Thursday
run may find only a partial set (say FLEX but not TE), and the Sunday run picks
up the rest and the refreshed numbers. That is normal, not a failure: a position
with no list gets no line and no conflict, and the status block names which
lists were read. It also sets the cache TTL — see §8.

Because Sunday's list is the one that matters for the lineup, **the cache must
never be the reason a Sunday run serves Thursday's ranks.** That is the whole
argument for the short TTL below; do not raise it to "save requests".

---

## 8. Config, caching and request budget

### Config (`config.py`)

Two new settings. Neither goes anywhere near `_validate_weights` or the
`FF_WEIGHT_*` parsing — they carry no weight.

| Env var | `Settings` field | Default | Meaning |
|---|---|---|---|
| `FF_ANALYSTS` | `analysts: str` | `""` (off) | `boone` to enable; `off`/empty disables. Same disabled-values convention as `journalists._DISABLED`. |
| `FF_ANALYST_MIN_GAP` | `analyst_min_gap: float` | `5.0` | Positional spots a gap must clear to become a warning. |

Ship with the feature **off by default** and enable it via the repository
variable once Phase 0 and a live run have proved the parse — the same posture
`FF_COLUMN_SCRAPE` uses for the column scraper.

`load_settings` **must never raise** (`CLAUDE.md`). A non-numeric
`FF_ANALYST_MIN_GAP` or an unknown analyst name warns via `config._warn` and
falls back to the default.

Add `FF_ANALYSTS` and `FF_ANALYST_MIN_GAP` to `.env.example`, to
`docs/SETUP.md`, and to the `env:` block of all three workflows
(`weekly-report.yml`, `waivers.yml`, `chatops.yml`) as
`${{ vars.FF_ANALYSTS }}` — they are not secrets, so Variables, not Secrets.

### Caching

Two layers, both needed:

1. **Per-run memoization.** One `AnalystFetcher` shared across every league in a
   run, memoized per `(analyst, week, kind, scoring)` including failures —
   exactly `ColumnFetcher._articles`. `cli._league_bundles` builds per-league
   state in a loop, so the fetcher must be constructed *outside* that loop and
   passed in. **Universal kinds memoize under `scoring=""`** so a three-league
   run of mixed scoring fetches the QB / DST / K lists once, not once per league.
2. **Disk cache via `cache.py`, TTL 3 hours.** Keyed on
   `(analyst, season, week, kind, scoring)`, with the same `""` convention for
   universal kinds.

Why the disk layer is not redundant: each scheduled workflow runs the sibling
page rebuild as a **separate process**, so per-process memoization does not span
it. This is the same duplication that motivated `ODDS_CACHE_TTL`. Three hours is
short enough that a Thursday-cached miss does not suppress Sunday's article, and
long enough to absorb the duplication inside one run.

**Every disk cache goes through `cache.py` in both directions** —
`atomic_write_text` and `read_json_or_none`. Do not hand-roll
`path.write_text` / `json.loads`; `CLAUDE.md` explains what each of those breaks.

### Request budget

Per run, and **independent of league count**:

- 1 author index
- 3 universal lists (QB, DST, K) — fetched once regardless of how many scorings
  are in play
- 5 scoring-specific lists (RB, WR, TE, FLEX, OVERALL) **per distinct scoring**
  among the configured leagues

So a single-scoring run is **9 requests**; a run with both half- and full-PPR
leagues is **14**. Yahoo is not metered here, but the sibling-page-rebuild
multiplier doubles whatever that number is across processes — which is what the
disk cache absorbs.

Fetch OVERALL **lazily**: it is only needed as a fallback when a positional list
is missing, and skipping it when every position resolved saves a request per
scoring.

---

## 9. Tests — all offline, fixtures only

Tests never hit the network (`CLAUDE.md`). The fetcher takes an injectable
`requests.Session` exactly like `ColumnFetcher` and `JournalistFetcher`.

### Fixtures (all saved from real responses in Phase 0)

| File | Purpose |
|---|---|
| `tests/fixtures/yahoo_author_index.html` | Discovery |
| `tests/fixtures/yahoo_boone_fullppr_wr_week1.html` | Scoring-specific positional list, happy path; also carries the scoring-group markup |
| `tests/fixtures/yahoo_boone_halfppr_wr_week1.html` | **The same position, other scoring** — the fixture that makes the scoring bug testable |
| `tests/fixtures/yahoo_boone_qb_week1.html` | A universal list |
| `tests/fixtures/yahoo_boone_fullppr_flex_week1.html` | For the FLEX-derivation fallback |
| `tests/fixtures/yahoo_boone_week2.html` | Proves week gating rejects it for week 1 |

The half- and full-PPR WR pair is the most valuable fixture in the set: it is the
only way to prove the code hands a half-PPR league half-PPR numbers, and the
ranks genuinely differ between the two, so a test can assert on a real
divergence rather than a synthetic one.

Plus two hand-modified copies of a real page (acceptable here — they are
*negative* fixtures, testing that a mutation is rejected):
`yahoo_boone_bad_byline.html` (author changed) and
`yahoo_boone_truncated.html` (rows removed, to trip the parse validator).

### `tests/test_analysts.py`

- discovery finds the week-1 articles and classifies each `kind`; each of the
  five confirmed §2.1 URLs matches **exactly one** `kind`;
- discovery **rejects** a week-2 article when week 1 is asked for;
- discovery **rejects** a link naming a different season;
- discovery skips an unclassifiable slug rather than guessing;

**Scoring (the new critical group):**

- a half-PPR league gets the half-PPR WR ranks and a full-PPR league gets the
  full-PPR ones, asserted against the two real WR fixtures and their differing
  order — this is the test the whole §2.1 section exists for;
- a scoring-specific list whose scoring cannot be resolved is **dropped**, not
  defaulted to half;
- a slug token contradicting the group heading it was found under is dropped
  with a warning;
- universal kinds (QB/DST/K) are served to leagues of **both** scorings, and
  fetched only once across them;
- a `std`-scoring league yields the "no published counterpart" reason and no
  comparison, rather than half-PPR ranks;
- verification rejects the bad byline;
- the parser reproduces the first 12 rows of the real FLEX fixture exactly;
- the parse validator rejects the truncated fixture;
- each of the six `unavailable` reasons in §4.5 is reachable and distinct;
- **positional derivation**: with a FLEX fixture, two roster RBs come back at
  their true positional ranks (his RB6 / RB19), not re-ranked 1/2;
- a network error yields `[]` and a warning, never an exception.

### `tests/test_analyst_conflicts.py` (pure, no fixtures)

- an inversion on the top two is detected;
- a player the analyst does not rank is **skipped**, not treated as last;
- a gap under the floor produces `material=False`;
- a gap at or over the floor produces `material=True`;
- the starter-boundary pair is examined when `starter_count` is passed, and the
  `starter_count < 2` / `len(scored) <= starter_count` guards are honored;
- **`rec.close_call` is unchanged** by `detect_conflicts`;
- **`rec.notes` is unchanged** by `detect_conflicts`.

### Regression tests elsewhere

- **In `tests/test_results_log_roundtrip.py`**: a `Recommendation` carrying
  `analyst_conflicts` logs a row containing no analyst text. This is the §5 trap;
  pin it.
- **In `tests/test_html.py` and `tests/test_report_markdown.py`**: a material
  conflict renders a warning; an immaterial one renders the quiet note; an empty
  `analyst_conflicts` renders nothing at all.
- **In `tests/test_data_status.py`**: the analyst status line appears once, not
  once per position — the aggregation bug `CLAUDE.md` describes for ECR.

CI runs the suite on Python 3.10–3.14; keep everything 3.10-compatible.

---

## 10. Files touched

**New**
- `ff_startsit/sources/analysts.py`
- `ff_startsit/engine/analyst.py`
- `tests/test_analysts.py`, `tests/test_analyst_conflicts.py`
- 6 fixtures under `tests/fixtures/`

**Modified**
- `ff_startsit/models.py` — `Recommendation.analyst_conflicts` field
- `ff_startsit/config.py` — two settings + env parsing (no weight changes)
- `ff_startsit/report.py` — build one `AnalystRanks` **per scoring** in play;
  attach conflicts in `rank_each_position`, which already has `starter_counts`
- `ff_startsit/cli.py` — construct the fetcher outside the `_league_bundles`
  loop and thread it through, passing each league's own `lsettings.scoring`
  (the per-league `Settings` copy is already made unconditionally there)
- `ff_startsit/output/html.py` — `_position_section` callout + CSS class
- `ff_startsit/output/render.py` — `render_markdown` callout/note
- `ff_startsit/waivers/columns.py` — extract the shared byline/week verifier
- `.env.example`, `docs/SETUP.md`, `README.md`
- `.github/workflows/{weekly-report,waivers,chatops}.yml` — `env:` entries

**Untouched, deliberately** — if a diff touches any of these, something has gone
wrong: `engine/normalize.py`, `engine/blend.py`, `pipeline.build_signals`,
`config._validate_weights`, `results_log.py`, `calibrate/**`, `sources/base.py`.

Workflow edits must pass both halves of the workflow check:
`.venv/bin/python scripts/check-workflows.py` and
`actionlint -ignore 'unexpected key "queue" for "concurrency" section'`.

---

## 11. Suggested order of work

1. **Phase 0** (§3). If Branch C, stop and report. Settle the scoring-group
   question (Q5/Q6) here too — the module's shape depends on it.
2. `sources/analysts.py` + `tests/test_analysts.py`, offline against fixtures.
   Build discovery + scoring resolution **before** the parser: getting the wrong
   list parsed perfectly is worse than not parsing at all. No rendering yet.
3. `engine/analyst.py` + `tests/test_analyst_conflicts.py`. Pure, fast.
4. Wire into `report.py` / `cli.py`; add the `Recommendation` field; add the
   log regression test.
5. Render (§6) and the status line (§7).
6. Config, docs, workflow env. Ship **off**.
7. Enable via repository variable; watch one Thursday and one Sunday run; then
   tune `FF_ANALYST_MIN_GAP` from what actually fired.

---

## 12. Open questions for the repo owner

1. **Standard-scoring leagues.** Boone publishes half- and full-PPR only. A
   league configured `std` therefore has no counterpart set. The plan **withholds
   the comparison** for such a league and says so in the status block, rather
   than substituting half-PPR. Confirm that is the wanted behavior — the
   alternative (use half-PPR and label the mismatch loudly) is defensible but is
   the kind of "close enough" that this codebase generally refuses. *No action
   needed if no configured league uses `std`.*
2. **`FF_ANALYST_MIN_GAP = 5`** is a first guess. Worth revisiting after two live
   weeks, based on how often the warning actually fires.
3. **Should a partial week be visible or silent?** When Thursday's run has FLEX
   but not TE, the TE section simply carries no analyst line. The status block
   records it. Is that enough, or should the position itself say "Boone has not
   posted TE ranks yet"?

*Resolved since the first draft, kept here so the reasoning is not re-derived:*
Boone publishes positional lists for **every** position including K and D/ST, so
there is no position without coverage and no need to press the OVERALL list into
service for QB. And scoring is no longer a mismatch to be labeled — both sets are
published, so the right answer is to read the right one.
