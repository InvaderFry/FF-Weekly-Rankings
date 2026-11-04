"""The Wednesday values sheet: your roster on Richard's and Boone's charts.

Both writers publish a trade-value chart (Richard's on Tuesday, which he says
doubles as rest-of-season rankings; Boone's on Wednesday), and in a redraft
league a trade value *is* a rest-of-season value. This puts them side by side
for every skill player you roster, next to FantasyPros' rest-of-season rank,
and judges each trade idea the waiver pass found **separately per chart**.

Two rules shape it:

* **The charts are on different scales** (Richard has Gibbs at 56, Boone at
  104), so values are never added across charts or averaged. A trade verdict is
  one number per chart ("Richard +6, Boone −3"), and the disagreement flag
  compares each chart's *rank* by value, which is scale-free.
* **Display only.** Like every board, nothing here changes a pick, an add, a
  drop, a trade idea or the blend, and nothing reaches ``results_log.jsonl``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from ..boards.models import ALL_POSITIONS, BoardEntry, ExpertBoard
from ..models import Player
from .board import fp_ros_board

SKILL = ("QB", "RB", "WR", "TE")
#: Chart ranks this far apart (at a position) before "the charts disagree".
#: Scaled up for deeper ranks, where a few spots mean less: the flag needs at
#: least this many spots *and* 35% of the better rank.
DISAGREE_SPOTS = 6
DISAGREE_SHARE = 0.35


@dataclass
class ValueRow:
    player: Player
    entries: dict[str, BoardEntry] = field(default_factory=dict)
    #: Set when the two charts rank him far apart, naming which is higher.
    split: Optional[str] = None
    #: Whether both charts ranked him, so "agree" is a claim rather than a blank.
    compared: bool = False

    @property
    def charts_cell(self) -> str:
        return self.split or ("agree" if self.compared else "—")


@dataclass
class TradeVerdict:
    """One trade idea, judged on each chart: what you get minus what you send.

    ``None`` for a chart that values someone in the deal at nothing it
    publishes (off the chart, or no value in this format): a partial sum would
    read as a real verdict and isn't one.
    """
    idea: object
    net: dict[str, Optional[float]] = field(default_factory=dict)

    @property
    def split(self) -> bool:
        """The charts disagree on whether the deal helps you."""
        signs = {v > 0 for v in self.net.values() if v is not None and v != 0}
        return len(signs) > 1


@dataclass
class ValueSheet:
    label: str
    scoring: str
    week: int
    boards: list[ExpertBoard] = field(default_factory=list)
    rows: list[ValueRow] = field(default_factory=list)
    verdicts: list[TradeVerdict] = field(default_factory=list)
    #: Carried from the waiver pass: a preseason refusal, or what it couldn't see.
    banner: Optional[str] = None
    caveat: Optional[str] = None
    #: The waiver bundle behind this sheet, for Data status and the trades.
    bundle: object = None
    #: Whether the waiver pass searched for trades at all (vs found none).
    trades_considered: bool = False


def chart_split(a: Optional[BoardEntry], b: Optional[BoardEntry],
                a_name: str, b_name: str, position: str) -> Optional[str]:
    """"Boone much higher (RB12 vs RB24)" when two charts' ranks are far apart."""
    if a is None or b is None or a.pos_rank is None or b.pos_rank is None:
        return None
    gap = abs(a.pos_rank - b.pos_rank)
    better = min(a.pos_rank, b.pos_rank)
    if gap < max(DISAGREE_SPOTS, DISAGREE_SHARE * better):
        return None
    hi, lo = (a_name, b_name) if a.pos_rank < b.pos_rank else (b_name, a_name)
    hi_rank, lo_rank = sorted((a.pos_rank, b.pos_rank))
    return f"{hi} much higher ({position}{hi_rank:.0f} vs {position}{lo_rank:.0f})"


def _net(idea, board: ExpertBoard) -> Optional[float]:
    total = 0.0
    for sign, side in ((1, idea.you_get), (-1, idea.you_send)):
        for score in side:
            entry = board.entries.get(score.player.key)
            if entry is None or entry.value is None:
                return None
            total += sign * entry.value
    return total


def build_sheet(bundle, my_players: Sequence[Player], sources: Sequence, week: int,
                superflex: bool = False, boards: Optional[Sequence[ExpertBoard]] = None
                ) -> ValueSheet:
    """The values sheet for one league, from its already-built waiver bundle.

    ``sources`` are chart sources (Richard, Boone). They are handed your
    skill players plus everyone in the bundle's trade ideas, so a verdict can
    price both sides; nothing else is fetched.
    """
    sheet = ValueSheet(label=bundle.label, scoring=bundle.scoring, week=week,
                       banner=bundle.banner, caveat=bundle.caveat, bundle=bundle,
                       trades_considered=bundle.trades_considered)
    if bundle.banner and not bundle.recs:
        return sheet                     # the preseason refusal: nothing was scored
    mine = [p for p in my_players if p.position in SKILL]
    in_trades = [s.player for idea in bundle.trades for s in (*idea.you_send, *idea.you_get)]
    everyone = list({p.key: p for p in [*mine, *in_trades]}.values())
    # ``boards`` are charts the caller already fetched over a superset of these
    # players (the waiver pass reads them before picking adds): no second fetch.
    boards = list(boards) if boards is not None else []
    for source in (sources if not boards else ()):
        try:
            boards.append(source.fetch(everyone, week, bundle.scoring, superflex=superflex))
        except Exception as exc:     # a source never raises for I/O; belt and braces
            boards.append(ExpertBoard(source.id, source.short, source.label,
                                      unavailable={ALL_POSITIONS: f"{source.label}: {exc}"}))
    season_ranks = {s.player.key: s.season_rank
                    for rec in (bundle.recs or {}).values() for s in rec.scores
                    if s.season_rank is not None}
    boards.append(fp_ros_board(season_ranks))
    sheet.boards = boards

    # Only charts that were actually read: one not posted yet must not price a
    # trade as "off chart" or make every row read "agree".
    charts = [b for b in boards
              if b.source_id != "fantasypros_ros" and ALL_POSITIONS not in b.unavailable]
    for player in mine:
        row = ValueRow(player, {b.source_id: b.entries[player.key]
                                for b in boards if player.key in b.entries})
        if len(charts) >= 2:
            a, b = charts[0], charts[1]
            ea, eb = a.entries.get(player.key), b.entries.get(player.key)
            row.compared = bool(ea and eb and ea.pos_rank is not None and eb.pos_rank is not None)
            row.split = chart_split(ea, eb, a.short, b.short, player.position)
        sheet.rows.append(row)

    def order(row: ValueRow):
        ranks = [row.entries[b.source_id].pos_rank for b in charts
                 if b.source_id in row.entries and row.entries[b.source_id].pos_rank is not None]
        return (SKILL.index(row.player.position), min(ranks) if ranks else 999, row.player.name)
    sheet.rows.sort(key=order)
    sheet.verdicts = [TradeVerdict(idea, {b.short: _net(idea, b) for b in charts})
                      for idea in bundle.trades]
    return sheet


def superflex_for(rules) -> bool:
    """Whether a league's charts should be read off their 2QB columns."""
    return (bool(rules.flex_slots.get("SUPER_FLEX"))
            or (rules.roster_slots or {}).get("QB", 1) >= 2)


def chart_boards(sheet) -> list[ExpertBoard]:
    """The writers' own charts on a sheet (Richard, Boone), read or not.

    FantasyPros ROS rides on every sheet too, but the waiver page already prints
    it as "ROS rank" beside each drop, so callers adding chart columns there skip it.
    """
    from .board import FP_ROS_ID
    return [b for b in (getattr(sheet, "boards", None) or []) if b.source_id != FP_ROS_ID]


def chart_cell(board: ExpertBoard, player: Player) -> str:
    """One chart's reading of one player for a table cell.

    Distinguishes the three blanks a reader would otherwise have to guess
    between: the chart isn't out yet (Boone posts Wednesday, and the Tuesday page
    is built before then), the chart doesn't cover this format, and he simply
    isn't on a chart that was read — which is itself a reading, since both
    charts stop well above waiver-wire depth.
    """
    from ..boards.articles import not_posted
    from ..output.board import waiver_cell
    entry = board.entries.get(player.key)
    if entry is not None:
        return waiver_cell(entry, player.position)
    reason = board.unavailable.get(ALL_POSITIONS) or board.unavailable.get(player.position)
    if reason:
        return "not posted yet" if not_posted(reason) else "n/a"
    return "off chart"


def sheet_cell(board: ExpertBoard, row: "ValueRow") -> str:
    """A values-table cell: ``chart_cell`` for a writer's chart, so "off chart"
    and "not posted yet" read differently from each other; FP ROS's overall rank
    as the board renders it everywhere else."""
    from ..output.board import waiver_cell
    from .board import FP_ROS_ID, cell_position
    if board.source_id == FP_ROS_ID:
        return waiver_cell(row.entries.get(board.source_id), cell_position(board, row.player))
    return chart_cell(board, row.player)


def sheets_read(sheets) -> list[str]:
    """Chart sources and positions read in any league (for the retry marker)."""
    from .board import read_sources
    seen: set[str] = set()
    for sheet in sheets:
        # Per position: Boone's charts post one at a time, so "read Boone" is
        # the same claim before and after the TE chart lands.
        seen.update(read_sources(sheet.boards, positions=SKILL))
    return sorted(seen)
