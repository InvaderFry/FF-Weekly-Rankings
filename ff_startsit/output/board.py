"""Shared helpers for drawing expert boards as table columns.

One definition of "which board columns does this section show" and "what goes
in a cell", called by every renderer — the terminal table, the Markdown digest
and the HTML dashboard. Each renderer used to grow its own copy of rules like
this, and fixes then reached three of four outputs; keeping the rule here is
what stops that.
"""

from __future__ import annotations

from ..boards.models import BoardEntry, ExpertBoard
from ..models import Recommendation

BLANK = "—"


def section_boards(rec: Recommendation) -> list[ExpertBoard]:
    """The boards worth a column in this position section.

    A board appears only if it read at least one player shown here. A board
    with nothing for this position (Boone hasn't posted his TE list, ECR is
    down) adds no all-blank column: the reason already reaches the reader once,
    via Data status or the section's analyst note, and a column of dashes in
    every table would bury that under noise.
    """
    keys = [s.player.key for s in rec.scores]
    return [b for b in rec.expert_boards if b.covers(keys)]


def format_rank(rank: float) -> str:
    """12.0 -> "12"; an averaged 12.5 keeps its half."""
    return f"{rank:.0f}" if float(rank).is_integer() else f"{rank:.1f}"


def board_cell(board: ExpertBoard, player_key: str) -> str:
    entry: BoardEntry | None = board.entry(player_key)
    if entry is None or entry.pos_rank is None:
        return BLANK
    return format_rank(entry.pos_rank)


def boards_legend(boards: list[ExpertBoard]) -> str:
    """One line naming where each column came from, e.g.
    "Ranks by position — FP: FantasyPros consensus; Boone: Justin Boone (Yahoo)."
    """
    parts = [f"{b.header}: {b.label}" for b in boards]
    return "Ranks by position — " + "; ".join(parts) + "."
