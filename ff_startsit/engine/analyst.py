"""Pure analyst inversions at the blend's top pair and starter boundary."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import PlayerScore, Recommendation


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
                     min_gap: float,
                     boundary_pair: Optional[tuple[PlayerScore, PlayerScore]] = None,
                     ) -> list[AnalystConflict]:
    """Return annotations without changing close_call, notes, or any scores.

    ``boundary_pair`` is the (starter, first-man-out) pair resolved from a built
    lineup, and it is the *only* way a boundary conflict is reported. There is
    no starter-count fallback: a positional count cannot see a flex slot, so it
    names the rank N+1 player the FLEX slot is about to start -- which is how
    "Justin Boone would flip your last starting spot" reached a live report
    about two players who were *both* in the lineup.
    ``report.flag_starter_boundaries`` supplies the pair; a caller with no
    lineup (``rank``, ``compare``) passes none and gets the top-two conflict
    only, which is the honest reading available without one.
    """
    scored = [s for s in rec.scores if s.final is not None]
    if len(scored) < 2:
        return []
    pairs = [(scored[0], scored[1], False)]
    if boundary_pair is not None:
        pairs.append((boundary_pair[0], boundary_pair[1], True))
    conflicts = []
    for a, b, boundary in pairs:
        ar, br = ranks.get(a.player.key), ranks.get(b.player.key)
        if ar is not None and br is not None and br < ar:
            conflicts.append(AnalystConflict(
                analyst, a.player.position, a.player.name, b.player.name,
                ar, br, boundary, ar - br >= min_gap))
    return conflicts
