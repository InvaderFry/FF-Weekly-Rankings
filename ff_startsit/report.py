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
from .pipeline import recommend

# A common 1QB/PPR-ish starting set used for the suggested lineup.
LINEUP_SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"]
FLEX_POSITIONS = {"RB", "WR", "TE"}
# Order positions appear in the digest.
POSITION_ORDER = ["QB", "RB", "WR", "TE", "K", "DEF"]


def rank_each_position(settings: Settings, players: Sequence[Player], week: int,
                       log: bool = False) -> dict[str, Recommendation]:
    """Rank each position group once. One scoring pass = one set of API calls."""
    recs: dict[str, Recommendation] = {}
    for pos in {p.position for p in players}:
        cands = [p for p in players if p.position == pos]
        recs[pos] = recommend(settings, cands, week, command="report", log=log)
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


def build_digest(settings: Settings, players: Sequence[Player], week: int) -> str:
    """Assemble the full whole-roster markdown digest (one scoring pass)."""
    recs = rank_each_position(settings, players, week)
    by_pos = scored(recs)

    lines: list[str] = [
        f"# 🏈 Week {week} start/sit — {settings.scoring.upper()}",
        f"_Generated {date.today().isoformat()}._",
        "",
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

    return "\n".join(lines)
