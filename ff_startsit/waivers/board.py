"""The Tuesday expert board: what each writer says about this league's wire.

One row per free agent that a writer named, with each writer's reading side by
side (Boone's rank, tier and FAB bid; Eisenberg's priority and FAAB %; Richard's
rest-of-season value) next to FantasyPros' rest-of-season rank and what this
tool's own add/drop pass decided about him.

Restricted to *this league's* free agents on purpose: a writer's column covers
every league, and a player already rostered here is not a pickup. The board is
display data, like every board (see ``ff_startsit/boards``): it changes no add,
drop, bid or blend, and never reaches ``results_log.jsonl``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from ..boards.models import ALL_POSITIONS, BoardEntry, ExpertBoard
from ..models import Player

#: Boards whose listing *is* a pickup recommendation. Richard's chart values
#: players but doesn't recommend adds, so it fills a column without counting.
RECOMMENDING = ("boone_waivers", "eisenberg_waivers")
FP_ROS_ID = "fantasypros_ros"


@dataclass
class BoardRow:
    player: Player
    entries: dict[str, BoardEntry] = field(default_factory=dict)
    #: How many writers recommend him (Boone, Eisenberg): the sort's first key.
    listed_by: int = 0
    #: What this tool's own pass made of him: "add (drop X)", "also consider".
    tool: Optional[str] = None


def cell_position(board: ExpertBoard, player: Player) -> Optional[str]:
    """The prefix for a board's rank: the player's position, except for FP ROS,
    whose rank is overall."""
    return None if board.source_id == FP_ROS_ID else player.position


def fp_ros_board(season_ranks: dict[str, float]) -> ExpertBoard:
    """FantasyPros' overall rest-of-season rank, already fetched by the pass."""
    return ExpertBoard(FP_ROS_ID, "FP ROS", "FantasyPros rest-of-season (overall rank)",
                       kind="ros",
                       entries={k: BoardEntry(pos_rank=float(r)) for k, r in season_ranks.items()})


def build_board(free_agents: Sequence[Player], boards: Sequence[ExpertBoard],
                adds=(), alternates=()) -> list[BoardRow]:
    """Rows for every free agent a writer named, best-supported first.

    Sorted by how many writers recommend him, then the best (lowest) rank
    either gave him, then Richard's value, then name, so the top of the table
    is where the people you trust agree.
    """
    tool: dict[str, str] = {}
    for target in adds:
        drop = target.drop.player.name if getattr(target, "drop", None) else None
        tool[target.score.player.key] = f"add (drop {drop})" if drop else "add"
    for target in alternates:
        tool.setdefault(target.score.player.key, "also consider")
    selecting = [b for b in boards if b.source_id != FP_ROS_ID]
    rows: list[BoardRow] = []
    for player in free_agents:
        if not any(player.key in b.entries for b in selecting):
            continue
        row = BoardRow(player=player, tool=tool.get(player.key))
        for board in boards:
            entry = board.entries.get(player.key)
            if entry is not None:
                row.entries[board.source_id] = entry
        row.listed_by = sum(1 for sid in RECOMMENDING if sid in row.entries)
        rows.append(row)

    def key(row: BoardRow):
        ranks = [row.entries[s].pos_rank for s in RECOMMENDING
                 if s in row.entries and row.entries[s].pos_rank is not None]
        richard = row.entries.get("richard_chart")
        top10 = [int(m[1]) for e in row.entries.values()
                 if e.tier and (m := re.match(r"top-10 #(\d+)", e.tier))]
        return (-row.listed_by, min(ranks) if ranks else 999, min(top10) if top10 else 99,
                -(richard.value or 0) if richard else 0, row.player.name)
    rows.sort(key=key)
    return rows


def board_reasons(boards: Sequence[ExpertBoard]) -> list[str]:
    """One line per writer whose article couldn't be read, in its own words
    ("…not posted yet" stays distinct from "…could not be verified")."""
    return [b.unavailable[ALL_POSITIONS] for b in boards if ALL_POSITIONS in b.unavailable]


def read_sources(boards: Sequence[ExpertBoard]) -> list[str]:
    """Writer boards whose article was found and read this run."""
    return [b.source_id for b in boards
            if b.source_id != FP_ROS_ID and ALL_POSITIONS not in b.unavailable]


# --- the retry gate ------------------------------------------------------------
#
# Tuesday runs twice: a first pass, and a later retry for the article that was
# not up yet. The retry must post only when it read something the earlier post
# didn't, or every Tuesday would land two identical messages. What was read is
# recorded in the digest itself, as an HTML comment the issue keeps, so the
# retry can compare against exactly what was published.

_MARKER = re.compile(r"<!-- ff-board season=(\d+) week=(\d+) read=([\w,]*) -->")


def board_marker(season: int, week: int, read: Iterable[str]) -> str:
    return f"<!-- ff-board season={season} week={week} read={','.join(sorted(set(read)))} -->"


def bundles_read(bundles) -> list[str]:
    """Writer boards read in any league this run."""
    seen: set[str] = set()
    for bundle in bundles:
        seen.update(read_sources(getattr(bundle, "expert_boards", [])))
    return sorted(seen)


def has_news(read: Iterable[str], previous_text: Optional[str], season: int, week: int) -> bool:
    """Whether this run is worth publishing over what was already published.

    True when nothing was published for this season and week yet, or when this
    run read a writer the published report didn't. Equal or fewer is not news:
    a retry that found the same articles would only post a duplicate, and one
    that found fewer (a page went down) would replace a better report.
    """
    published: Optional[set[str]] = None
    for s_, w_, ids in _MARKER.findall(previous_text or ""):
        if int(s_) == season and int(w_) == week:
            got = {i for i in ids.split(",") if i}
            published = got if published is None else published | got
    if published is None:
        return True
    return bool(set(read) - published)


def report_has_news(current_text: str, previous_text: Optional[str]) -> bool:
    """``has_news`` read straight off two rendered digests.

    A digest with no board marker (the board is switched off) is news only
    when nothing has been published yet, so a retry with the board off never
    reposts the same report.
    """
    match = _MARKER.search(current_text or "")
    if match is None:
        return not (previous_text or "").strip()
    season, week, ids = int(match[1]), int(match[2]), [i for i in match[3].split(",") if i]
    return has_news(ids, previous_text, season, week)
