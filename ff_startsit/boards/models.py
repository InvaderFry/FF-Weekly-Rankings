"""What an expert board holds: one source's reading of each player, side by side.

A board is **display data**, never a blend input. It carries no weight, never
reaches ``Recommendation.notes`` or ``results_log.jsonl``, and nothing in
``engine/`` reads it. That is the line between this package and
``sources/base.py:Signal``: a signal changes the pick, a board only sits next to
it so the owner can see where the people he trusts disagree.

Attribution is the one thing a board must get right. Every source that builds
one fails closed — a byline, week or column header it cannot verify produces an
``unavailable`` reason, never somebody else's numbers under this board's label.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

#: Key in ``ExpertBoard.unavailable`` for a reason that covers every position.
ALL_POSITIONS = "*"

#: Column-header tag per board kind. One writer can fill three columns — Boone's
#: weekly ranks, his waiver column and his trade chart — and a bare "Boone" over
#: each read as the same thing (the waivers page's "Boone" was his waiver
#: column, so his trade chart, which decided an add, was nowhere on the page).
KIND_TAGS = {"weekly": "Wk", "waiver": "WW", "ros": "ROS"}


@dataclass(frozen=True)
class BoardEntry:
    """One source's reading of one player.

    Every field is optional because sources differ in what they publish: a
    weekly ranking has only ``pos_rank``, a trade chart has a ``value`` (and a
    rank derived from it), and a waiver column has a ``bid`` in the writer's own
    wording plus a ``tier``.
    """

    pos_rank: Optional[float] = None
    value: Optional[float] = None
    bid: Optional[str] = None
    tier: Optional[str] = None
    note: Optional[str] = None


@dataclass
class ExpertBoard:
    """One source's readings for a run, keyed by ``Player.key``.

    ``short`` is the column header ("FP", "Boone"); ``label`` is the full
    attribution ("FantasyPros consensus", "Justin Boone (Yahoo)"), which is what
    a reader is told the numbers came from. ``scoring_label`` is set when the
    list's scoring differs from the league's — e.g. CBS publishes no half-PPR
    list, so a half-PPR league shows its PPR list headed "CBS (PPR)" rather than
    passing it off as half-PPR.
    """

    source_id: str
    short: str
    label: str
    kind: str = "weekly"            # "weekly" | "ros" | "waiver"
    entries: dict[str, BoardEntry] = field(default_factory=dict)
    scoring_label: Optional[str] = None
    url: Optional[str] = None
    published: Optional[datetime] = None
    fetched_at: Optional[datetime] = None
    #: position (or ``ALL_POSITIONS``) -> why this board has nothing there.
    unavailable: dict[str, str] = field(default_factory=dict)

    @property
    def header(self) -> str:
        """The column header: ``short`` plus what kind of reading it is ("Boone
        ROS"), and a scoring mismatch when there is one. Sentences use ``short``
        ("Richard's trade chart values ..."), which already says which kind."""
        tag = KIND_TAGS.get(self.kind)
        base = self.short if not tag or self.short.endswith(tag) else f"{self.short} {tag}"
        return f"{base} ({self.scoring_label})" if self.scoring_label else base

    def entry(self, key: str) -> Optional[BoardEntry]:
        return self.entries.get(key)

    def covers(self, keys) -> bool:
        """Whether this board has a reading for at least one of ``keys``."""
        return any(k in self.entries for k in keys)
