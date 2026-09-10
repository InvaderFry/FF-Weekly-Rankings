"""Whole-roster markdown digest + the shared lineup builder.

Both the `lineup` and `report` CLI commands (and the weekly GitHub Action) use
``build_lineup`` so there's one definition of "best starter per slot". ``build_digest``
assembles the full phone-friendly report posted as a GitHub Issue.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

from .config import Settings
from .data_status import DataStatus, single_status
from .models import Player, PlayerScore, Recommendation
from .output.render import LINEUP_UNSCORED_NOTE, lineup_unscored_keys, md_cell, render_markdown
from .engine.blend import flag_starter_boundary_pair
from .pipeline import build_signals, log_deferred, recommend
from .season import preseason_banner, season_year
from .engine.analyst import detect_conflicts
from .sources.analysts import AnalystFetcher, not_published_yet
from .sources.journalists import (Expert, JournalistFetcher, JournalistRow,
                                  JournalistView, parse_experts)

# A common 1QB/PPR-ish starting set used for the suggested lineup.
LINEUP_SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"]
# A tuple, not a set: this order is the FLEX tie-break, and set iteration order
# for strings varies per process under hash randomization — which made the FLEX
# pick differ between runs on identical data.
FLEX_POSITIONS: tuple[str, ...] = ("RB", "WR", "TE")
# A superflex ("OP" on ESPN) slot takes a quarterback too, which is the whole
# point of it — and why it cannot be spelled "FLEX" anywhere: the pooled FantasyPros
# flex ranking below is an RB/WR/TE list and does not rank quarterbacks at all.
SUPER_FLEX_POSITIONS: tuple[str, ...] = ("QB",) + FLEX_POSITIONS
# Which positions each flex slot will accept. A slot absent from here is an
# ordinary position slot and only accepts itself.
SLOT_POSITIONS: dict[str, tuple[str, ...]] = {
    "FLEX": FLEX_POSITIONS,
    "SUPER_FLEX": SUPER_FLEX_POSITIONS,
}
# Order positions appear in the digest.
POSITION_ORDER = ["QB", "RB", "WR", "TE", "K", "DEF"]
# Position precedence for FLEX tie-breaks. Arbitrary but fixed, and derived from
# POSITION_ORDER so the file keeps one ordering convention rather than two.
_FLEX_ORDER = {pos: i for i, pos in enumerate(FLEX_POSITIONS)}

#: Fraction of the flex pool that must carry an ECR value for the pooled ranking
#: to be trusted. Below this the pooled blend is running on Vegas/injury/weather
#: alone — a worse FLEX pick than the positional fallback, and an invisible one.
MIN_FLEX_ECR_COVERAGE = 0.5

FLEX_CAVEAT_POSITIONAL = (
    "FLEX is a standard-template suggestion: the cross-position FantasyPros FLEX "
    "ranking was unavailable, so candidates are compared on scores normalized "
    "within their own position group. Treat it as a tie-break hint, not a ranking."
)
FLEX_NOTE_POOLED = (
    "FLEX is scored against the pooled RB/WR/TE candidate set, so its score is "
    "not comparable to the other slots' positional scores."
)
POOLED_COMPARE_NOTE = (
    "Scored against FantasyPros' cross-position RB/WR/TE ranking, because a "
    "per-position rank of 1 means \"RB1\" and \"WR1\" indistinguishably. These "
    "scores are not comparable to a single-position rank or compare."
)


@dataclass
class Lineup:
    """Slot assignments plus how the FLEX slot was decided.

    Iterable and indexable as ``[(slot, pick), ...]`` so every renderer and test
    that predates the pooled FLEX pass keeps working untouched.
    """

    slots: list[tuple[str, Optional[PlayerScore]]]
    flex_basis: str = "positional"          # "pooled" | "positional"
    caveat: Optional[str] = None

    def __iter__(self):
        return iter(self.slots)

    def __len__(self) -> int:
        return len(self.slots)

    def __getitem__(self, index):
        return self.slots[index]


@dataclass
class WeekScores:
    """One scoring pass over a roster: per-position ranks plus the flex pool.

    ``flex`` is deliberately *not* folded into ``recs`` — the Discord renderer
    iterates ``recs`` emitting a close-call alert per entry, so a "FLEX" key
    would duplicate alerts for players already listed under RB/WR/TE, while the
    digest and dashboard iterate POSITION_ORDER and would never render it.
    """

    recs: dict[str, Recommendation]
    flex: Optional[Recommendation] = None
    flex_note: Optional[str] = None


@dataclass
class LeagueBundle:
    """One league's fully-scored week, ready to render.

    The shared unit passed to every multi-league renderer (digest, dashboard,
    Discord) so a single scoring pass per league feeds all three outputs.
    """

    label: str
    scoring: str
    recs: dict[str, Recommendation]
    lineup: "Lineup"
    banner: Optional[str] = None
    journalists: Optional[JournalistView] = None
    data_status: Optional["DataStatus"] = None


def rank_each_position(settings: Settings, players: Sequence[Player], week: int,
                       log: bool = False,
                       signals: Optional[Sequence] = None,
                       analyst_fetcher: Optional[AnalystFetcher] = None) -> dict[str, Recommendation]:
    """Rank each position group once. One scoring pass = one set of API calls.

    The signal instances are built once and reused across positions so each
    signal can memoize its fetch — Vegas pulls every game regardless of
    position, and ECR caches per position — keeping a whole-roster pass cheap on
    network calls and API quota. ``signals`` lets a caller share one set across
    this pass and the pooled FLEX pass, so the two together still cost a single
    Odds API credit.

    This pass takes no ``slots``: nothing in it depends on the league's shape
    any more. The close-call boundary flag is **not** set here — a positional
    count cannot see a flex slot, and the flex pick is by construction the rank
    N+1 player it would name, so ``score_week`` resolves that pair from the
    built lineup instead (``flag_starter_boundaries``). Logging is deferred with
    it — the logged row carries ``close_call``, so it has to be written after
    the flag is final, not before.
    """
    signals = list(signals) if signals is not None else build_signals(settings)
    recs: dict[str, Recommendation] = {}
    for pos in {p.position for p in players}:
        cands = [p for p in players if p.position == pos]
        recs[pos] = recommend(settings, cands, week, signals=signals,
                              command="report", log=log,
                              exclude_unavailable=True,
                              defer_log=True)
    sample = any(getattr(signal, "is_sample", False) for signal in signals)
    if settings.analysts == "boone" and sample:
        for rec in recs.values():
            rec.source_status.append((("analyst", "Justin Boone", settings.league_label),
                f"{settings.league_label or 'This league'}: Justin Boone comparison withheld for a sample-data run"))
    if settings.analysts == "boone" and not sample:
        fetcher = analyst_fetcher or AnalystFetcher(season_year(), settings.data_dir)
        ranks = fetcher.fetch(players, week, settings.scoring)
        status = ranks.status(week, settings.league_label)
        for pos, rec in recs.items():
            # Ranks are kept on the rec so `flag_starter_boundaries` can redo
            # this against the real boundary pair once the lineup exists; the
            # count cannot see a flex slot, so no boundary is judged here.
            rec.analyst_ranks = dict(ranks.by_position.get(pos, {}))
            rec.analyst_name = ranks.analyst
            rec.analyst_conflicts = detect_conflicts(
                rec, rec.analyst_ranks, ranks.analyst,
                settings.analyst_min_gap)
            # Only the not-posted-yet reason is position-specific. Every other
            # reason is identical at every position, so repeating it per section
            # would be the noise Data status already carries once.
            if not_published_yet(ranks.unavailable.get(pos)):
                rec.analyst_note = (
                    f"{ranks.analyst} has not posted his Week {week} "
                    f"{'full' if settings.scoring == 'ppr' else 'half'}-PPR "
                    f"{'DST' if pos in {'DEF', 'DST'} else pos} rankings yet.")
            rec.source_status.append(status)
    return recs


def rank_pooled(settings: Settings, cands: Sequence[Player], week: int,
                signals: Optional[Sequence], command: str,
                ) -> tuple[Optional[Recommendation], Optional[str]]:
    """Score a mixed RB/WR/TE set against one cross-position candidate pool.

    Returns ``(recommendation, None)`` on success, or ``(None, reason)`` when the
    pooled ranking can't be trusted. Shared by the FLEX slot and by
    ``compare``, which face the same problem: ``normalize.to_0_100`` is min-max
    *within* the candidate set, so per-position scores put every position's
    leader at 100 and cannot be compared across positions. Only FantasyPros'
    cross-position FLEX list makes such a comparison meaningful.

    Never logged: the calibrator scores pairwise concordance within a single
    logged decision, so a pooled row would re-log these players under a second
    normalization frame and double-weight them in the grid search.
    """
    from .pipeline import flex_signals

    if len(cands) < 2:
        return None, "too few flex-eligible players to pool"
    if signals is None:
        return None, "no live signals for this run"
    pooled_signals = flex_signals(signals)
    if pooled_signals is None:
        return None, "no live ECR signal to pool"

    rec = recommend(settings, cands, week, signals=pooled_signals,
                    command=command, log=False, exclude_unavailable=True)

    covered = sum(1 for s in rec.scores
                  if (v := s.raw.get("ecr")) is not None and v.available)
    if covered / len(cands) < MIN_FLEX_ECR_COVERAGE:
        # Without ECR the pooled blend is running on implied team total, health
        # and weather alone — a worse pick than the positional fallback, and one
        # the user would never see. Refuse it.
        return None, "FantasyPros FLEX ranking returned too few matches"
    return rec, None


def rank_flex_pool(settings: Settings, players: Sequence[Player], week: int,
                   signals: Optional[Sequence] = None,
                   ) -> tuple[Optional[Recommendation], Optional[str]]:
    """Rank every flex-eligible player in one candidate set.

    Returns ``(recommendation, None)`` on success, or ``(None, reason)`` when the
    pooled ranking can't be trusted — the caller then falls back to comparing
    per-position scores and surfaces the reason.

    The pool is ranked once over *all* flex-eligible players even though FLEX is
    filled after RB/RB/WR/WR/TE. Min-max is order-preserving per signal, but a
    weighted blend of several independently-rescaled signals is not invariant to
    shrinking the candidate set, so this is an approximation — a stable "flex
    value" for one fetch, rather than a re-rank per remaining candidate.
    """
    cands = [p for p in players if p.position in FLEX_POSITIONS]
    return rank_pooled(settings, cands, week, signals, "report:flex")


def score_week(settings: Settings, players: Sequence[Player], week: int,
               log: bool = False,
               slots: Optional[Sequence[str]] = None,
               analyst_fetcher: Optional[AnalystFetcher] = None) -> WeekScores:
    """One signal set, one per-position pass, one pooled FLEX pass.

    ``log`` appends the per-position decisions to the results log, so the
    whole-roster commands can feed the #7 calibrator the same way ``rank`` and
    ``compare`` do. It stays opt-in and defaults off: a scheduled run that
    scores every position every week would otherwise dominate the corpus with
    rows nobody acted on. The pooled FLEX pass is never logged either way —
    see ``rank_pooled``.

    ``slots`` is the league's starting-slot list, forwarded to ``build_lineup``
    -- which is also what decides the close-call boundary now, so the two cannot
    disagree about the shape of the league by construction.

    Order matters here. The starter-boundary flag is only correct once the
    lineup exists — a positional starter count cannot see a flex slot, and the
    flex pick is exactly the rank N+1 player a count would flag — so the pass
    runs score -> pool -> lineup -> flag -> log. The log write comes last
    because ``results_log`` captures ``close_call`` and ``backtest`` buckets its
    confident-vs-close-call honesty split on it: writing the row before the flag
    was final would put a warning in the corpus that the report never showed.
    """
    signals = build_signals(settings)
    recs = rank_each_position(settings, players, week, log=log, signals=signals,
                              **({"analyst_fetcher": analyst_fetcher}
                                 if analyst_fetcher is not None else {}))
    flex, note = rank_flex_pool(settings, players, week, signals=signals)
    ws = WeekScores(recs=recs, flex=flex, flex_note=note)
    flag_starter_boundaries(settings, recs, build_lineup(
        scored(recs), flex_pool=scored_flex(ws), flex_note=note, slots=slots))
    for rec in recs.values():
        log_deferred(settings, rec, command="report")
    return ws


def scored(recs: dict[str, Recommendation]) -> dict[str, list[PlayerScore]]:
    """Drop unscored players; keep best->worst order for slot filling."""
    return {pos: [s for s in rec.scores if s.final is not None] for pos, rec in recs.items()}


def scored_flex(ws: WeekScores) -> Optional[list[PlayerScore]]:
    """The pooled flex ranking as a scored list, or None when unavailable."""
    if ws.flex is None:
        return None
    return [s for s in ws.flex.scores if s.final is not None]


def lineup_from(ws: WeekScores) -> Lineup:
    """The suggested lineup for an already-scored week."""
    return build_lineup(scored(ws.recs), flex_pool=scored_flex(ws),
                        flex_note=ws.flex_note)


def _slot_sort_key(s: PlayerScore) -> tuple:
    """Total order over slot candidates, best first — stable across processes.

    Ties on ``final`` are common (per-position normalization puts every
    position's leader at 100), so the fallbacks matter: position order first,
    then name and key to make it a strict total order even for identical scores.

    Deliberately does *not* consult the ECR raw value. Ranks here are ranks
    *within a position*, so "RB1" and "WR1" are both 1.0 — comparing them would
    look principled while being meaningless.
    """
    return (-(s.final if s.final is not None else -1.0),
            _FLEX_ORDER.get(s.player.position, len(FLEX_POSITIONS)),
            s.player.name,
            s.player.key)


def _best_for_slot(slot: str, by_pos: dict[str, list[PlayerScore]],
                   used: set[str]) -> Optional[PlayerScore]:
    positions = SLOT_POSITIONS.get(slot, (slot,))
    candidates = []
    for pos in positions:
        for s in by_pos.get(pos, []):
            if s.player.key in used:
                continue
            candidates.append(s)
            break  # by_pos[pos] is sorted; first unused is best at that position
    return min(candidates, key=_slot_sort_key) if candidates else None


def _best_from_pool(pool: Sequence[PlayerScore], used: set[str]) -> Optional[PlayerScore]:
    """Best unused player in the pooled flex ranking (already sorted best->worst)."""
    for s in pool:
        if s.player.key not in used:
            return s
    return None


def flag_starter_boundaries(settings: Settings, recs: dict[str, Recommendation],
                            lineup: "Lineup") -> None:
    """Flag the pair that really straddles each position's last starting spot.

    The boundary that sets a lineup is "last man in vs first man out", and only
    the built lineup knows where that falls. ``starter_counts`` deliberately
    excludes flex slots — a flex slot has no position to count against — so a
    count puts the RB boundary at RB2-vs-RB3 while the FLEX slot is filled by
    the best remaining RB/WR/TE, which is that same RB3. The check then warned
    about a player who was in the lineup: of the four boundary warnings in the
    live Week 1 run, three named a runner-up who started, one of them the FLEX
    pick itself. That is the false alarm the weight and gap floors elsewhere
    exist to prevent, arriving by a different route.

    So the pair is resolved here instead: for each position, the lowest-ranked
    player who *starts* anywhere in the lineup (his own slot or a flex slot)
    against the highest-ranked one who does not. A position whose candidates
    all start, or none of whom do, has no boundary to flag and is skipped —
    there is no decision there to warn about.

    Called from ``score_week`` after ``build_lineup`` and before the deferred
    log write, so the row records the flag the report actually rendered.
    Renderers are untouched: this only sets ``close_call``/``notes``, exactly
    as the scoring-time check did.
    """
    starting = {pick.player.key for _, pick in lineup if pick is not None}
    for rec in recs.values():
        scored_players = [s for s in rec.scores if s.final is not None]
        if len(scored_players) < 2:
            continue
        starters = [s for s in scored_players if s.player.key in starting]
        bench = [s for s in scored_players if s.player.key not in starting]
        if not starters or not bench:
            continue
        # ``scores`` is ordered best -> worst, so these are the last man in and
        # the first man out without re-sorting.
        pair = (starters[-1], bench[0])
        flag_starter_boundary_pair(
            rec, pair[0], pair[1],
            settings.close_call_threshold,
            settings.min_disagree_weight,
            settings.close_call_raw_gaps,
            settings.presentational_gaps,
        )
        # The analyst comparison straddles the same boundary and was wrong for
        # the same reason. Recomputed rather than appended: `detect_conflicts`
        # returns the top-two conflict too, so adding to the list would double
        # it.
        if rec.analyst_ranks:
            rec.analyst_conflicts = detect_conflicts(
                rec, rec.analyst_ranks, rec.analyst_name,
                settings.analyst_min_gap, boundary_pair=pair)


def build_lineup(by_pos: dict[str, list[PlayerScore]],
                 flex_pool: Optional[Sequence[PlayerScore]] = None,
                 flex_note: Optional[str] = None,
                 slots: Optional[Sequence[str]] = None) -> Lineup:
    """Greedily fill the starting slots.

    Non-FLEX slots come from ``by_pos``, whose scores are normalized within each
    position group. FLEX comes from ``flex_pool`` when one is supplied — a single
    ranking over all flex-eligible candidates, which is the only way its scores
    are comparable across positions. Without a pool the FLEX slot falls back to
    comparing per-position scores and says so via ``Lineup.caveat``.

    ``slots`` overrides ``LINEUP_SLOTS`` for callers that know the league's real
    shape. The waiver pass is one: it reads starting slots off ESPN and Sleeper to
    decide what counts as surplus, and computing drop protection from the hardcoded
    template instead left a superflex league's second quarterback both unprotected
    *and* surplus — the two halves of one guard disagreeing about who starts.

    ``flex_pool`` fills ``FLEX`` and **only** ``FLEX``. It is FantasyPros'
    cross-position RB/WR/TE ranking, which does not rank quarterbacks, so a
    ``SUPER_FLEX`` slot falls through to ``_best_for_slot`` and compares
    per-position scores — a real limitation, and a better one than drawing a
    superflex pick from a list its best candidate isn't on.
    """
    used: set[str] = set()
    out: list[tuple[str, Optional[PlayerScore]]] = []
    pooled = bool(flex_pool)
    for slot in (slots if slots is not None else LINEUP_SLOTS):
        if slot == "FLEX" and pooled:
            pick = _best_from_pool(flex_pool, used)
        else:
            pick = _best_for_slot(slot, by_pos, used)
        if pick is not None:
            used.add(pick.player.key)
        out.append((slot, pick))

    has_flex = any(slot == "FLEX" and pick is not None for slot, pick in out)
    if pooled:
        return Lineup(slots=out, flex_basis="pooled",
                      caveat=FLEX_NOTE_POOLED if has_flex else None)
    # The explanation is what the reader needs; the reason is diagnostic detail.
    caveat = FLEX_CAVEAT_POSITIONAL + (f" ({flex_note})" if flex_note else "")
    return Lineup(slots=out, flex_basis="positional",
                  caveat=caveat if has_flex else None)


#: The analyst transport's id in a ``JournalistView``. Not a FantasyPros expert
#: id and deliberately unlike one -- these ranks come from the analyst's own
#: published lists, and a column that cannot say which source it came from is
#: how consensus gets published under a byline.
ANALYST_EXPERT_ID = "yahoo-boone"


def _analyst_journalist_view(settings: Settings, players: Sequence[Player],
                             week: int) -> Optional[JournalistView]:
    """The Preferred journalists section, served by the analyst transport.

    Per-expert FantasyPros ranks need a **paid** API key -- the free tier 403s
    on that endpoint, and the public page filters in the browser and serves the
    same consensus whatever is asked for (``experts --verify`` says exactly
    this). Boone is also the only configured journalist whose id resolves at
    all. So the section never rendered, while ``sources/analysts.py`` was
    already fetching that same analyst's real ranks for the disagreement notes.

    This reuses them rather than adding a scrape. The expert is labelled with
    its actual source so the table can never read as FantasyPros consensus --
    the same fail-closed attribution rule ``ecr._matches_filters`` and
    ``analysts._verify_sole_contributor`` hold.
    """
    if settings.analysts != "boone":
        return None
    from .sources.analysts import AnalystFetcher

    try:
        ranks = AnalystFetcher(season_year(), settings.data_dir).fetch(
            players, week, settings.scoring)
    except Exception as exc:
        import sys
        print(f"warning: analyst journalist view unavailable: {exc}", file=sys.stderr)
        return None
    if not ranks.by_position:
        return None

    expert = Expert(id=ANALYST_EXPERT_ID, name=f"{ranks.analyst} (Yahoo)")
    by_position: dict[str, list[JournalistRow]] = {}
    for p in players:
        rank = ranks.by_position.get(p.position, {}).get(p.key)
        if rank is None:
            continue          # unranked or on bye -- left out, not ranked last
        by_position.setdefault(p.position, []).append(
            JournalistRow(player=p, avg_rank=rank, ranks={expert.id: rank}))
    if not by_position:
        return None
    for rows in by_position.values():
        rows.sort(key=lambda r: r.avg_rank)
    return JournalistView(experts=[expert], by_position=by_position)


def build_journalist_view(settings: Settings, players: Sequence[Player],
                          week: int) -> Optional[JournalistView]:
    """Build the preferred-journalists view, or None when disabled/no data.

    FantasyPros first when experts are configured, then the analyst transport --
    which is what actually renders today, since the per-expert FantasyPros
    endpoint is a paid product. The paid path stays first so a key, once
    present, wins without a config change.
    """
    experts = parse_experts(settings.preferred_experts)
    view = None
    if experts:
        fetcher = JournalistFetcher(experts, api_key=settings.fantasypros_api_key,
                                    scoring=settings.scoring)
        try:
            view = fetcher.build_view(players, week)
        except Exception as exc:  # a broken journalist feed must never sink a run
            import sys
            print(f"warning: preferred-journalists view unavailable: {exc}",
                  file=sys.stderr)
    if view is None:
        view = _analyst_journalist_view(settings, players, week)
    return view


def build_digest(settings: Settings, players: Sequence[Player], week: int,
                 label: str = "", log: bool = False) -> str:
    """Assemble the full whole-roster markdown digest (one scoring pass)."""
    ws = score_week(settings, players, week, log=log)
    return render_digest(week, settings.scoring, ws.recs,
                         banner=preseason_banner(settings),
                         journalists=build_journalist_view(settings, players, week),
                         label=label,
                         lineup=lineup_from(ws))


def _digest_body(recs: dict[str, Recommendation],
                 banner: Optional[str] = None,
                 journalists: Optional[JournalistView] = None,
                 lineup: Optional[Lineup] = None) -> list[str]:
    """The lineup + per-position + journalists markdown, without the H1 heading.

    Shared by the single-league digest and the multi-league digest so both stay
    in lockstep.
    """
    if lineup is None:
        lineup = build_lineup(scored(recs))
    unscored = lineup_unscored_keys(recs)
    lines: list[str] = []
    if banner:
        lines += [f"> {banner}", ""]
    lines += [
        "## Suggested lineup",
        "",
        "| Slot | Player | Team | Score |",
        "|---|---|---|---|",
    ]
    any_unscored = False
    for slot, pick in lineup:
        if pick is None:
            lines.append(f"| {slot} | _(no option)_ | | |")
        elif pick.player.key in unscored:
            any_unscored = True
            lines.append(f"| {md_cell(slot)} | {md_cell(pick.player.name)} "
                         f"| {md_cell(pick.player.team or 'BYE')} | — |")
        else:
            lines.append(f"| {md_cell(slot)} | {md_cell(pick.player.name)} "
                         f"| {md_cell(pick.player.team or 'BYE')} "
                         f"| {pick.final:.1f} |")
    if any_unscored:
        lines += ["", f"_{LINEUP_UNSCORED_NOTE}_"]

    if getattr(lineup, "caveat", None):
        lines += ["", f"> ⚠️ {lineup.caveat}"]

    lines.append("")
    lines.append("## Rankings by position")
    for pos in POSITION_ORDER:
        rec = recs.get(pos)
        if rec is None or not rec.scores:
            continue
        lines.append("")
        lines.append(render_markdown(rec, title=pos))

    if journalists is not None:
        lines.append("")
        lines.append(render_journalists_markdown(journalists))
    return lines


def render_digest(week: int, scoring: str, recs: dict[str, Recommendation],
                  banner: Optional[str] = None,
                  journalists: Optional[JournalistView] = None,
                  label: str = "",
                  lineup: Optional[Lineup] = None) -> str:
    """Render precomputed per-position recs as the markdown digest.

    Split out from ``build_digest`` so callers that already have ``recs`` (e.g.
    the ``publish`` command) can render without triggering another scoring pass.
    ``banner`` (e.g. the preseason sample-data warning) renders as a blockquote
    under the title. ``label`` (a league name) is appended to the heading when set.
    """
    label_suffix = f" · {label}" if label else ""
    lines: list[str] = [
        f"# 🏈 Week {week} start/sit — {scoring.upper()}{label_suffix}",
        f"_Generated {date.today().isoformat()}._",
        "",
    ]
    lines += [single_status(week, label, recs).markdown(), ""]
    lines += _digest_body(recs, banner=banner, journalists=journalists, lineup=lineup)
    return "\n".join(lines)


def render_multi_digest(week: int, bundles: Sequence[LeagueBundle]) -> str:
    """Render several leagues into one digest, a section per league under one H1."""
    lines: list[str] = [
        f"# 🏈 Week {week} start/sit",
        f"_Generated {date.today().isoformat()} · {len(bundles)} league(s)._",
        "",
    ]
    from .data_status import bundle_status
    status = bundle_status(bundles)
    if status:
        lines += [status.markdown(), ""]
    for b in bundles:
        lines += [f"## {md_cell(b.label)} — {b.scoring.upper()}", ""]
        lines += _digest_body(b.recs, banner=b.banner, journalists=b.journalists,
                              lineup=b.lineup)
        lines.append("")
    return "\n".join(lines)


def render_journalists_markdown(view: JournalistView) -> str:
    """Render the Preferred journalists section as GFM tables.

    One table per position: each journalist's own weekly rank plus their
    average, best average first. Display-only — no blend scores here.
    """
    names = ", ".join(e.name for e in view.experts)
    lines = [
        "## Preferred journalists",
        f"_Average weekly rank across: {names}. Side-by-side view only — "
        "not part of the blended score._",
    ]
    for pos in POSITION_ORDER:
        rows = view.by_position.get(pos)
        if not rows:
            continue
        header = ["#", "Player", "Team", "Avg rank"] + [md_cell(e.name)
                                                        for e in view.experts]
        lines += ["", f"### {pos}", "",
                  "| " + " | ".join(header) + " |",
                  "|" + "---|" * len(header)]
        for i, row in enumerate(rows, start=1):
            cells = [str(i), md_cell(row.player.name),
                     md_cell(row.player.team or "BYE"), f"{row.avg_rank:.1f}"]
            for e in view.experts:
                rank = row.ranks.get(e.id)
                cells.append("—" if rank is None else f"{rank:.0f}")
            lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)
