"""Pure analyst inversions at the blend's top pair and starter boundary."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import Recommendation


@dataclass(frozen=True)
class AnalystConflict:
    analyst: str
    position: str
    leader: str
    preferred: str
    leader_rank: float
    preferred_rank: float
    boundary: bool
    material: bool

    @property
    def gap(self) -> float:
        return self.leader_rank - self.preferred_rank


def detect_conflicts(rec: Recommendation, ranks: Mapping[str, float], analyst: str,
                     min_gap: float, starter_count: Optional[int] = None) -> list[AnalystConflict]:
    """Return annotations without changing close_call, notes, or any scores."""
    scored = [s for s in rec.scores if s.final is not None]
    if len(scored) < 2:
        return []
    pairs = [(scored[0], scored[1], False)]
    if starter_count and starter_count >= 2 and len(scored) > starter_count:
        pairs.append((scored[starter_count - 1], scored[starter_count], True))
    conflicts = []
    for a, b, boundary in pairs:
        ar, br = ranks.get(a.player.key), ranks.get(b.player.key)
        if ar is not None and br is not None and br < ar:
            conflicts.append(AnalystConflict(
                analyst, a.player.position, a.player.name, b.player.name,
                ar, br, boundary, ar - br >= min_gap))
    return conflicts
