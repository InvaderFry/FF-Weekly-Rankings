"""FantasyPros consensus as a board, read off the ECR signal already fetched.

No request of its own: the ``ecr`` signal's raw value *is* the FantasyPros
positional consensus rank (``rank_ecr``), so this only re-labels what the
start/sit pass already holds. Built per run from the per-position
recommendations, never from the pooled FLEX pass, whose raw value is a
cross-position FLEX rank and would read as nonsense in a positional column.
"""

from __future__ import annotations

from typing import Iterable, Optional

from ..models import Recommendation
from .models import BoardEntry, ExpertBoard

SOURCE_ID = "fantasypros_weekly"


def fantasypros_weekly(recs: Iterable[Recommendation]) -> Optional[ExpertBoard]:
    """Board of FantasyPros positional ranks for every player in ``recs``.

    Returns ``None`` for a sample (preseason fill) run — sample ranks are
    nobody's opinion — and when no player carries a usable ECR reading, so a
    run with ECR down shows no empty column (the per-player "no ECR rank" flag
    and Data status already say so).
    """
    recs = list(recs)
    if any(rec.sample_data for rec in recs):
        return None
    entries: dict[str, BoardEntry] = {}
    for rec in recs:
        for score in rec.scores:
            reading = score.raw.get("ecr")
            if reading is not None and reading.available and reading.raw is not None:
                entries[score.player.key] = BoardEntry(pos_rank=float(reading.raw))
    if not entries:
        return None
    return ExpertBoard(source_id=SOURCE_ID, short="FP",
                       label="FantasyPros consensus", entries=entries)
