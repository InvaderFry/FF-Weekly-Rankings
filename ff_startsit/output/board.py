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
        # Every source here is a weekly list, so the "Wk" tag adds nothing to
        # a one-line Discord comparison; the scoring mismatch still does.
        name = f"{board.short} ({board.scoring_label})" if board.scoring_label else board.short
        parts.append(f"{name} {ra}–{rb}")
    if not parts:
        return None
    return f"{a.player.name} vs {b.player.name} — " + " · ".join(parts)


def waiver_cell(entry: Optional[BoardEntry], position: Optional[str] = None) -> str:
    """One writer's reading in the Tuesday board, in his own terms:
    Boone "RB1 · $29 · top-10 #1", Eisenberg "RB2 · 10%+ FAAB",
    Richard "12 · RB31" (value, then rank by value), FP ROS "#88".

    ``position`` prefixes a positional rank, since the board mixes positions
    and "#1" would not say #1 *of what*. Leave it out for an overall rank."""
    if entry is None:
        return BLANK
    parts = []
    if entry.value is not None:
        parts.append(f"{entry.value:g}")
    if entry.pos_rank is not None:
        parts.append(f"{position or '#'}{format_rank(entry.pos_rank)}")
    if entry.bid:
        parts.append(entry.bid)
    if entry.tier and entry.tier != "featured":
        parts.append(entry.tier)
    return " · ".join(parts) or BLANK


def waiver_legend(boards) -> str:
    """Where each Tuesday column came from."""
    return "Columns — " + "; ".join(f"{b.header}: {b.label}" for b in boards) + "."


def chart_net(value: Optional[float]) -> str:
    """A trade's net on one chart: "+6", "−3", "even", or "off chart"."""
    if value is None:
        return "off chart"
    if value == 0:
        return "even"
    return f"{value:+g}".replace("-", "−")


def trade_line(idea) -> str:
    send = ", ".join(s.player.name for s in idea.you_send)
    get = ", ".join(s.player.name for s in idea.you_get)
    return f"Send {send} to {idea.partner} for {get}"


def verdict_text(verdict) -> str:
    """ "Richard +6 · Boone −3 (charts split)" """
    if not verdict.net:
        return "no chart read yet"
    text = " · ".join(f"{name} {chart_net(v)}" for name, v in verdict.net.items())
    return text + (" (charts split)" if verdict.split else "")
