"""The ``ExpertSource`` seam: where a new side-by-side column comes from.

The third extension point beside ``sources/base.py:Signal`` (changes the pick)
and ``roster/base.py:RosterProvider`` (whose players). A source turns one
expert's published list into an ``ExpertBoard`` for the players in front of it.

Contract, the same one the existing analyst transport keeps:

* **Fail closed.** If the byline, the week, the season or a column header
  can't be verified, return a board whose ``unavailable`` says why, and no
  entries. Never return plausible numbers you can't attribute.
* **Never raise for a fetch problem.** A board is an annotation; it must not
  sink the start/sit run beside it. Network and parse failures become an
  ``unavailable`` reason with its own wording, so "not posted yet" stays
  distinguishable from "broke".
* **Match on ``Player.key``** via ``data/matching`` (name + position + team),
  never on raw names.
* **Cache pages through ``cache.py``** and send ``net.HEADERS``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from ..models import Player
from .models import ExpertBoard


class ExpertSource(ABC):
    #: Stable id ("boone_weekly", "cbs_consensus", ...).
    id: str = "source"
    #: Column header.
    short: str = "?"
    #: Full attribution shown to the reader.
    label: str = "?"
    #: "weekly" | "ros" | "waiver"
    kind: str = "weekly"

    @abstractmethod
    def fetch(self, players: Sequence[Player], week: int, scoring: str) -> ExpertBoard:
        """Return this source's board for ``players``. Must not raise for I/O."""
