"""Whole-roster markdown digest + the shared lineup builder.

Both the `lineup` and `report` CLI commands (and the weekly GitHub Action) use
``build_lineup`` so there's one definition of "best starter per slot". ``build_digest``
assembles the full phone-friendly report posted as a GitHub Issue.
"""

from __future__ import annotations

from datetime import date
from typing import Optional, Sequence

from .config import Settings
from .models import Player, PlayerScore, Recommendation
from .output.render import render_markdown
from .pipeline import build_signals, recommend
from .season import preseason_banner
from .sources.journalists import JournalistFetcher, JournalistView, parse_experts

# A common 1QB/PPR-ish starting set used for the suggested lineup.
LINEUP_SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"]
FLEX_POSITIONS = {"RB", "WR", "TE"}
# Order positions appear in the digest.
POSITION_ORDER = ["QB", "RB", "WR", "TE", "K", "DEF"]


def rank_each_position(settings: Settings, players: Sequence[Player], week: int,
                       log: bool = False) -> dict[str, Recommendation]:
    """Rank each position group once. One scoring pass = one set of API calls.

    The signal instances are built once and reused across positions so each
    signal can memoize its fetch — Vegas pulls every game regardless of
    position, and ECR caches per position — keeping a whole-roster pass cheap on
    network calls and API quota.
    """
    signals = build_signals(settings)
    recs: dict[str, Recommendation] = {}
    for pos in {p.position for p in players}:
        cands = [p for p in players if p.position == pos]
        recs[pos] = recommend(settings, cands, week, signals=signals,
                              command="report", log=log)
    return recs


def scored(recs: dict[str, Recommendation]) -> dict[str, list[PlayerScore]]:
    """Drop unscored players; keep best->worst order for slot filling."""
    return {pos: [s for s in rec.scores if s.final is not None] for pos, rec in recs.items()}


def _best_for_slot(slot: str, by_pos: dict[str, list[PlayerScore]],
                   used: set[str]) -> Optional[PlayerScore]:
    positions = FLEX_POSITIONS if slot == "FLEX" else {slot}
    best = None
    for pos in positions:
        for s in by_pos.get(pos, []):
            if s.player.key in used:
                continue
            if best is None or (s.final or 0) > (best.final or 0):
                best = s
            break  # by_pos[pos] is sorted; first unused is best at that position
    return best


def build_lineup(by_pos: dict[str, list[PlayerScore]]) -> list[tuple[str, Optional[PlayerScore]]]:
    """Greedily fill the standard slots; returns [(slot, pick_or_None), ...]."""
    used: set[str] = set()
    out: list[tuple[str, Optional[PlayerScore]]] = []
    for slot in LINEUP_SLOTS:
        pick = _best_for_slot(slot, by_pos, used)
        if pick is not None:
            used.add(pick.player.key)
        out.append((slot, pick))
    return out


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


def build_digest(settings: Settings, players: Sequence[Player], week: int) -> str:
    """Assemble the full whole-roster markdown digest (one scoring pass)."""
    recs = rank_each_position(settings, players, week)
    return render_digest(week, settings.scoring, recs,
                         banner=preseason_banner(settings),
                         journalists=build_journalist_view(settings, players, week))


def render_digest(week: int, scoring: str, recs: dict[str, Recommendation],
                  banner: Optional[str] = None,
                  journalists: Optional[JournalistView] = None) -> str:
    """Render precomputed per-position recs as the markdown digest.

    Split out from ``build_digest`` so callers that already have ``recs`` (e.g.
    the ``publish`` command) can render without triggering another scoring pass.
    ``banner`` (e.g. the preseason sample-data warning) renders as a blockquote
    under the title.
    """
    by_pos = scored(recs)

    lines: list[str] = [
        f"# 🏈 Week {week} start/sit — {scoring.upper()}",
        f"_Generated {date.today().isoformat()}._",
        "",
    ]
    if banner:
        lines += [f"> {banner}", ""]
    lines += [
        "## Suggested lineup",
        "",
        "| Slot | Player | Team | Score |",
        "|---|---|---|---|",
    ]
    for slot, pick in build_lineup(by_pos):
        if pick is None:
            lines.append(f"| {slot} | _(no option)_ | | |")
        else:
            lines.append(f"| {slot} | {pick.player.name} | {pick.player.team or 'BYE'} "
                         f"| {pick.final:.1f} |")

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
        header = ["#", "Player", "Team", "Avg rank"] + [e.name for e in view.experts]
        lines += ["", f"### {pos}", "",
                  "| " + " | ".join(header) + " |",
                  "|" + "---|" * len(header)]
        for i, row in enumerate(rows, start=1):
            cells = [str(i), row.player.name, row.player.team or "BYE",
                     f"{row.avg_rank:.1f}"]
            for e in view.experts:
                rank = row.ranks.get(e.id)
                cells.append("—" if rank is None else f"{rank:.0f}")
            lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)
