"""Shared helpers for drawing expert boards as table columns.

One definition of "which board columns does this section show" and "what goes
in a cell", called by every renderer — the terminal table, the Markdown digest
and the HTML dashboard. Each renderer used to grow its own copy of rules like
this, and fixes then reached three of four outputs; keeping the rule here is
what stops that.
"""

from __future__ import annotations

from typing import Optional

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


#: The columns Discord carries, in order. Discord is read on a phone, so it
#: gets the three consensus-level views only; Pages and the digest carry every
#: board, Eisenberg's and Richard's own columns included.
DISCORD_BOARD_IDS = ("fantasypros_weekly", "boone_weekly", "cbs_consensus")


def close_call_comparison(rec: Recommendation,
                          board_ids=DISCORD_BOARD_IDS) -> Optional[str]:
    """One line putting the top two side by side in each source's ranks, e.g.
    "Gibbs vs Robinson — FP 1–2 · Boone 2–1 · CBS 1–3".

    Only for the top two scored players, the pair the close-call flag is about.
    A source that ranked neither is left out; None when no source ranked either.
    """
    top = [s for s in rec.scores if s.final is not None][:2]
    if len(top) < 2:
        return None
    a, b = top
    parts = []
    by_id = {board.source_id: board for board in rec.expert_boards}
    for board_id in board_ids:
        board = by_id.get(board_id)
        if board is None:
            continue
        ra, rb = board_cell(board, a.player.key), board_cell(board, b.player.key)
        if ra == BLANK and rb == BLANK:
            continue
        parts.append(f"{board.header} {ra}–{rb}")
    if not parts:
        return None
    return f"{a.player.name} vs {b.player.name} — " + " · ".join(parts)
