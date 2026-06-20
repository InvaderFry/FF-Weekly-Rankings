"""The Signal abstraction — the single most important seam for #7.

A Signal scores players for a given week. v1 ships ECR and Vegas; adding a
usage-based model (or any future input) later is just another subclass with the
same contract. The blender consumes whatever signals are available and weights
them, so new signals require no engine changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

from ..models import Player, SignalValue


class Signal(ABC):
    #: Stable identifier used as the key in blend weights and the results log.
    name: str = "signal"

    #: True when higher ``raw`` values mean a better player (Vegas implied
    #: totals); False when lower is better (ECR rank). Drives normalization.
    higher_is_better: bool = True

    @abstractmethod
    def is_available(self) -> bool:
        """Whether this signal can produce data at all (e.g. has a key)."""

    @abstractmethod
    def fetch(self, week: int, players: Iterable[Player]) -> dict[str, SignalValue]:
        """Return ``{player.key: SignalValue}`` for the given players and week.

        Implementations should return a value for every player passed in,
        marking ones they cannot cover as ``SignalValue(raw=None,
        available=False, note=...)`` rather than omitting them.
        """


def unavailable_for(players: Iterable[Player], note: str) -> dict[str, SignalValue]:
    """Helper: mark every player unavailable (used when a signal is disabled)."""
    return {p.key: SignalValue(raw=None, available=False, note=note) for p in players}
