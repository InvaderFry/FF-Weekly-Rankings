"""The RosterProvider seam.

Decouples the CLI from any single platform, mirroring how ``sources/base.py``
decouples the engine from data sources. Every provider returns canonical
``Player`` objects (``models.py``) with a unique key, so downstream matching
(``data/matching.py``) and the Vegas team assignment work regardless of where the
roster came from.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Player


class RosterError(RuntimeError):
    """Raised when a roster cannot be resolved (bad config, missing team, etc.)."""


class RosterProvider(ABC):
    #: Stable identifier ("espn", "sleeper", "manual"); used in cache filenames.
    name: str = "roster"

    @abstractmethod
    def get_roster_players(self) -> list[Player]:
        """Return the canonical roster for this provider's configured league/file."""

    def cache_tag(self) -> str:
        """Short token distinguishing this provider's cache from others.

        Defaults to the provider name; ESPN/Sleeper override to include the league
        id so different leagues don't share a cache file.
        """
        return self.name
