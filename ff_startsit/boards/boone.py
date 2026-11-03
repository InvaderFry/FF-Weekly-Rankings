"""Justin Boone's weekly ranks (Yahoo) as a board.

Wraps the existing transport in ``sources/analysts.py`` rather than re-scraping:
that module already verifies Boone is the *sole* contributor to every list it
reads and fails closed otherwise, so this only re-shapes its result.
"""

from __future__ import annotations

from typing import Optional, Sequence

from ..models import Player
from ..sources.analysts import AnalystRanks
from .base import ExpertSource
from .models import ALL_POSITIONS, BoardEntry, ExpertBoard

SOURCE_ID = "boone_weekly"


def board_from_analyst_ranks(ranks: AnalystRanks) -> ExpertBoard:
    """Re-shape an ``AnalystRanks`` into a board. Positions with no list keep
    their own reason in ``unavailable``; a league-wide refusal (standard scoring
    has no Boone list) is filed under ``ALL_POSITIONS``."""
    entries = {key: BoardEntry(pos_rank=float(rank))
               for by_key in ranks.by_position.values()
               for key, rank in by_key.items()}
    unavailable = {(ALL_POSITIONS if pos == "league" else pos): reason
                   for pos, reason in ranks.unavailable.items()}
    return ExpertBoard(source_id=SOURCE_ID, short="Boone",
                       label=f"{ranks.analyst} (Yahoo)", entries=entries,
                       unavailable=unavailable)


class BooneWeeklySource(ExpertSource):
    id = SOURCE_ID
    short = "Boone"
    label = "Justin Boone (Yahoo)"
    kind = "weekly"

    def __init__(self, fetcher):
        self.fetcher = fetcher

    def fetch(self, players: Sequence[Player], week: int, scoring: str) -> ExpertBoard:
        try:
            ranks = self.fetcher.fetch(players, week, scoring)
        except Exception as exc:  # an annotation never sinks the run beside it
            return ExpertBoard(source_id=self.id, short=self.short, label=self.label,
                               unavailable={ALL_POSITIONS: f"fetch failed: {exc}"})
        return board_from_analyst_ranks(ranks)


def boone_board_or_none(ranks: Optional[AnalystRanks]) -> Optional[ExpertBoard]:
    """A board only when Boone actually ranked someone in this run."""
    if ranks is None:
        return None
    board = board_from_analyst_ranks(ranks)
    return board if board.entries else None
