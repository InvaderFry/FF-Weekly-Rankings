"""Whether the writers you trust back an add — a veto, never a vote.

The boards elsewhere are display only. This module is the one place they reach
the add list, and only to *remove* rows: nothing here adds a player, reorders
the table, changes a bid or a drop pairing, or touches a blend weight, and
nothing reaches ``results_log.jsonl``. A recommendation the experts don't back
is held back and listed with its reason, so the veto is visible rather than a
row that silently went missing.

Three rules, the first two chosen in the Week 9 review:

* **Skill players** must be on a writer's radar. A running back or receiver
  must be on a trade chart (Richard's or Boone's) *or* named in Boone's or
  Eisenberg's waiver column; a quarterback or tight end must be on a trade
  chart. QB and TE are stricter because a league starts one of each: a QB or
  TE no chart values is a one-week streamer, and the roster-aware cap
  (``score.must_swap_in_position``) already governs those. "Malik Willis, drop
  Jadarian Price" was a QB on no chart.
* **Trade values decide a skill-player swap** the charts disagree with. Every
  trade chart read at both players' positions votes for whichever it values
  higher (one that lists only one of the two prefers him; one that lists
  neither abstains), and the swap is held back when more charts prefer the
  player you'd drop. Being *on* a chart is a low bar — "Greg Dulcich, drop
  Jadarian Price" cleared it at TE27 of Boone's 30 while both charts valued
  Price several times higher. Trade charts are the one cross-position scale
  this tool reads that was built for exactly this comparison.
* **Kickers and defenses** are judged pairwise against the player they'd
  replace, on this week's CBS consensus and Boone's weekly list. The swap is
  held back when more of the experts who ranked either player prefer the one
  you have; a tie goes to the blend.

Both **fall back to the blend** when there is nothing to consult — no chart
read at that position, no column read, no weekly K/DEF list — and say so in the
add's reasons. An expert who wasn't read is not an expert who disagreed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from ..boards.models import ALL_POSITIONS, ExpertBoard
from ..models import PlayerScore
from .models import STREAM_POSITIONS, DropCandidate

#: Trade-chart sources (Tuesday's Richard chart, Wednesday's Boone charts).
CHART_IDS = frozenset({"richard_chart", "boone_trade_values"})
#: Waiver-column sources.
COLUMN_IDS = frozenset({"boone_waivers", "eisenberg_waivers"})
#: Positions that need a trade chart, not merely a column mention.
CHART_ONLY = frozenset({"QB", "TE"})


def _read_at(board: ExpertBoard, position: str) -> bool:
    """Whether ``board`` was read at ``position`` at all."""
    return ALL_POSITIONS not in board.unavailable and position not in board.unavailable


def _names(boards: Sequence[ExpertBoard]) -> str:
    names = [b.short for b in boards]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " or " + names[-1]


@dataclass
class Endorsements:
    """The writers' readings this league's add list is checked against."""

    #: Writer boards (charts and columns), keyed by source id.
    boards: list[ExpertBoard] = field(default_factory=list)
    #: This week's K/DEF rankings: CBS consensus and Boone's weekly lists.
    weekly: list[ExpertBoard] = field(default_factory=list)
    #: Free agents quoted from a waiver column (``columns.ColumnFetcher``, the
    #: path used when the board is off). A quote is a column naming him.
    mentioned: frozenset = frozenset()

    @property
    def charts(self) -> list[ExpertBoard]:
        """One board per chart source, merged across its reads.

        Richard's chart arrives both as a Tuesday column (read over the
        free-agent pool) and as a value source (read over every rostered player
        too). Same article, different players: keeping only the first read lost
        every rostered player's value, which the trade-value rule needs for the
        drop side of the comparison. A position counts as read if any read of
        the chart covered it.
        """
        grouped: dict[str, list[ExpertBoard]] = {}
        for b in self.boards:
            if b.source_id in CHART_IDS:
                grouped.setdefault(b.source_id, []).append(b)
        merged = []
        for reads in grouped.values():
            if len(reads) == 1:
                merged.append(reads[0])
                continue
            first = reads[0]
            entries: dict = {}
            for b in reads:
                for key, entry in b.entries.items():
                    entries.setdefault(key, entry)
            unavailable = {pos: why for pos, why in first.unavailable.items()
                           if all(pos in b.unavailable for b in reads)}
            merged.append(ExpertBoard(first.source_id, first.short, first.label,
                                      kind=first.kind, entries=entries,
                                      scoring_label=first.scoring_label, url=first.url,
                                      published=first.published,
                                      fetched_at=first.fetched_at,
                                      unavailable=unavailable))
        return merged

    @property
    def columns(self) -> list[ExpertBoard]:
        return [b for b in self.boards if b.source_id in COLUMN_IDS]

    # --- skill players ---------------------------------------------------
    def skill_veto(self, add: PlayerScore) -> Optional[str]:
        """Why a skill-player add is held back, or ``None`` to let it stand."""
        pos = (add.player.position or "").upper()
        if pos in STREAM_POSITIONS:
            return None
        key = add.player.key
        charts = [b for b in self.charts if _read_at(b, pos)]
        if any(key in b.entries for b in charts):
            return None
        if pos in CHART_ONLY:
            if not charts:
                return None                  # no chart read here: the blend's call
            return f"not on {_names(charts)}'s trade chart"
        columns = [b for b in self.columns if ALL_POSITIONS not in b.unavailable]
        if any(key in b.entries for b in columns) or key in self.mentioned:
            return None
        if not charts and not columns and not self.mentioned:
            return None
        read = _names(charts + columns) or "the waiver columns"
        return f"not on a trade chart or in a waiver column ({read} read)"

    def value_readings(self, add: PlayerScore, drop: PlayerScore
                       ) -> tuple[list[str], list[str]]:
        """(charts valuing the add higher, charts valuing the drop higher).

        Only a chart read at *both* positions votes: "off chart" means something
        only where the chart was read. A chart listing just one of the two
        prefers him — the list stops above the other. Equal values abstain.
        """
        pos_a = (add.player.position or "").upper()
        pos_d = (drop.player.position or "").upper()
        prefer_add: list[str] = []
        prefer_drop: list[str] = []
        for chart in self.charts:
            if not (_read_at(chart, pos_a) and _read_at(chart, pos_d)):
                continue
            ea = chart.entries.get(add.player.key)
            ed = chart.entries.get(drop.player.key)
            va = ea.value if ea is not None else None
            vd = ed.value if ed is not None else None
            if va is None and vd is None:
                continue
            if vd is None or (va is not None and va > vd):
                prefer_add.append(chart.short)
            elif va is None or vd > va:
                prefer_drop.append(chart.short)
        return prefer_add, prefer_drop

    def value_veto(self, add: PlayerScore, drop: DropCandidate) -> Optional[str]:
        """Why a skill-player swap is held back on trade value, or ``None``."""
        positions = {(add.player.position or "").upper(),
                     (drop.score.player.position or "").upper()}
        if positions & STREAM_POSITIONS or drop.on_reserve or drop.bye_stream:
            return None
        prefer_add, prefer_drop = self.value_readings(add, drop.score)
        if len(prefer_drop) > len(prefer_add):
            name = drop.score.player.name
            if len(prefer_drop) == 1:
                return f"{prefer_drop[0]}'s trade chart values {name} higher rest of season"
            who = " and ".join(f"{w}'s" for w in prefer_drop)
            return f"{who} trade charts value {name} higher rest of season"
        return None

    def skill_unread(self, add: PlayerScore) -> bool:
        """True when no writer could be consulted for this add's position."""
        pos = (add.player.position or "").upper()
        if pos in STREAM_POSITIONS:
            return False
        if any(_read_at(b, pos) for b in self.charts):
            return False
        if pos in CHART_ONLY:
            return True
        return not (self.mentioned
                    or any(ALL_POSITIONS not in b.unavailable for b in self.columns))

    # --- kickers and defenses -------------------------------------------
    def stream_readings(self, add: PlayerScore, drop: PlayerScore
                        ) -> tuple[list[str], list[str]]:
        """(experts preferring the add, experts preferring the drop).

        An expert who ranks only one of the two prefers that one — his list
        stops above the other. One who ranks neither abstains.
        """
        pos = (add.player.position or "").upper()
        prefer_add: list[str] = []
        prefer_drop: list[str] = []
        for board in self.weekly:
            if not _read_at(board, pos):
                continue
            ra = board.entries.get(add.player.key)
            rd = board.entries.get(drop.player.key)
            ra = ra.pos_rank if ra is not None else None
            rd = rd.pos_rank if rd is not None else None
            if ra is None and rd is None:
                continue
            if rd is None or (ra is not None and ra < rd):
                prefer_add.append(board.short)
            else:
                prefer_drop.append(board.short)
        return prefer_add, prefer_drop

    def stream_veto(self, add: PlayerScore, drop: DropCandidate) -> Optional[str]:
        if (add.player.position or "").upper() not in STREAM_POSITIONS:
            return None
        if drop.bye_stream:
            return None          # he can't score this week; no ranking argues otherwise
        prefer_add, prefer_drop = self.stream_readings(add, drop.score)
        if len(prefer_drop) > len(prefer_add):
            who = " and ".join(prefer_drop)
            return f"{who} rank{'s' if len(prefer_drop) == 1 else ''} {drop.score.player.name} higher this week"
        return None

    def stream_unread(self, add: PlayerScore) -> bool:
        pos = (add.player.position or "").upper()
        return pos in STREAM_POSITIONS and not any(
            _read_at(b, pos) and b.entries for b in self.weekly)

    # --- the hook pick_adds calls ----------------------------------------
    def veto(self, add: PlayerScore, drop: DropCandidate) -> Optional[str]:
        return (self.skill_veto(add) or self.value_veto(add, drop)
                or self.stream_veto(add, drop))

    def fallback_note(self, add: PlayerScore) -> Optional[str]:
        """The reason line for an add no expert could weigh in on."""
        if self.stream_unread(add):
            return "no CBS or Boone ranking at his position this week, so this is the blend's call"
        if self.skill_unread(add) and (self.charts or self.columns or self.mentioned):
            return "no trade chart or waiver column read at his position, so this is the blend's call"
        return None
