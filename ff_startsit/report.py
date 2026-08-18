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
from .models import Player, PlayerScore, Recommendation
from .output.render import md_cell, render_markdown
from .pipeline import build_signals, recommend
from .season import preseason_banner
from .sources.journalists import JournalistFetcher, JournalistView, parse_experts

# A common 1QB/PPR-ish starting set used for the suggested lineup.
LINEUP_SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"]
# A tuple, not a set: this order is the FLEX tie-break, and set iteration order
# for strings varies per process under hash randomization — which made the FLEX
# pick differ between runs on identical data.
FLEX_POSITIONS: tuple[str, ...] = ("RB", "WR", "TE")
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


def rank_each_position(settings: Settings, players: Sequence[Player], week: int,
                       log: bool = False,
                       signals: Optional[Sequence] = None) -> dict[str, Recommendation]:
    """Rank each position group once. One scoring pass = one set of API calls.

    The signal instances are built once and reused across positions so each
    signal can memoize its fetch — Vegas pulls every game regardless of
    position, and ECR caches per position — keeping a whole-roster pass cheap on
    network calls and API quota. ``signals`` lets a caller share one set across
    this pass and the pooled FLEX pass, so the two together still cost a single
    Odds API credit.
    """
    signals = list(signals) if signals is not None else build_signals(settings)
    recs: dict[str, Recommendation] = {}
    for pos in {p.position for p in players}:
        cands = [p for p in players if p.position == pos]
        recs[pos] = recommend(settings, cands, week, signals=signals,
                              command="report", log=log)
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
                    command=command, log=False)

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
               log: bool = False) -> WeekScores:
    """One signal set, one per-position pass, one pooled FLEX pass.

    ``log`` appends the per-position decisions to the results log, so the
    whole-roster commands can feed the #7 calibrator the same way ``rank`` and
    ``compare`` do. It stays opt-in and defaults off: a scheduled run that
    scores every position every week would otherwise dominate the corpus with
    rows nobody acted on. The pooled FLEX pass is never logged either way —
    see ``rank_pooled``.
    """
    signals = build_signals(settings)
    recs = rank_each_position(settings, players, week, log=log, signals=signals)
    flex, note = rank_flex_pool(settings, players, week, signals=signals)
    return WeekScores(recs=recs, flex=flex, flex_note=note)


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
    positions = FLEX_POSITIONS if slot == "FLEX" else (slot,)
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


def build_lineup(by_pos: dict[str, list[PlayerScore]],
                 flex_pool: Optional[Sequence[PlayerScore]] = None,
                 flex_note: Optional[str] = None) -> Lineup:
    """Greedily fill the standard slots.

    Non-FLEX slots come from ``by_pos``, whose scores are normalized within each
    position group. FLEX comes from ``flex_pool`` when one is supplied — a single
    ranking over all flex-eligible candidates, which is the only way its scores
    are comparable across positions. Without a pool the FLEX slot falls back to
    comparing per-position scores and says so via ``Lineup.caveat``.
    """
    used: set[str] = set()
    out: list[tuple[str, Optional[PlayerScore]]] = []
    pooled = bool(flex_pool)
    for slot in LINEUP_SLOTS:
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


def build_journalist_view(settings: Settings, players: Sequence[Player],
                          week: int) -> Optional[JournalistView]:
    """Build the preferred-journalists view, or None when disabled/no data."""
    experts = parse_experts(settings.preferred_experts)
    if not experts:
        return None
    fetcher = JournalistFetcher(experts, api_key=settings.fantasypros_api_key,
                                scoring=settings.scoring)
    try:
        return fetcher.build_view(players, week)
    except Exception as exc:  # a broken journalist feed must never sink a run
        import sys
        print(f"warning: preferred-journalists view unavailable: {exc}",
              file=sys.stderr)
        return None


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
    lines: list[str] = []
    if banner:
        lines += [f"> {banner}", ""]
    lines += [
        "## Suggested lineup",
        "",
        "| Slot | Player | Team | Score |",
        "|---|---|---|---|",
    ]
    for slot, pick in lineup:
        if pick is None:
            lines.append(f"| {slot} | _(no option)_ | | |")
        else:
            lines.append(f"| {md_cell(slot)} | {md_cell(pick.player.name)} "
                         f"| {md_cell(pick.player.team or 'BYE')} "
                         f"| {pick.final:.1f} |")

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
    lines += _digest_body(recs, banner=banner, journalists=journalists, lineup=lineup)
    return "\n".join(lines)


def render_multi_digest(week: int, bundles: Sequence[LeagueBundle]) -> str:
    """Render several leagues into one digest, a section per league under one H1."""
    lines: list[str] = [
        f"# 🏈 Week {week} start/sit",
        f"_Generated {date.today().isoformat()} · {len(bundles)} league(s)._",
        "",
    ]
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
