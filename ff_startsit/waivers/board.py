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

#: Boards whose listing *is* a pickup recommendation. The trade charts value
#: players but don't recommend adds, so they fill a column without counting.
RECOMMENDING = ("boone_waivers", "eisenberg_waivers")
#: Trade charts (rest-of-season values). A free agent on one gets a row even
#: when no waiver column named him.
CHARTS = ("richard_chart", "boone_trade_values")
FP_ROS_ID = "fantasypros_ros"


@dataclass
class BoardRow:
    player: Player
    entries: dict[str, BoardEntry] = field(default_factory=dict)
    #: How many writers recommend him (Boone, Eisenberg): the sort's first key.
    listed_by: int = 0
    #: What this tool's own pass made of him: "add (drop X)", "also consider".
    tool: Optional[str] = None
    #: The writers' own words about him (``ColumnMention``), one per writer.
    quotes: list = field(default_factory=list)


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
                adds=(), alternates=(), mentions=None) -> list[BoardRow]:
    """Rows for every free agent a writer named or a trade chart values,
    best-supported first.

    Sorted by how many writers recommend him, then the best (lowest) rank
    either gave him, then how many trade charts list him, then Richard's value,
    then Boone's, then name, so the top of the table is where the people you
    trust agree. Each chart's value is only ever compared with that chart's
    own: the two are on different scales.
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
        seen_authors: set[str] = set()
        for quote in (mentions or {}).get(player.key, ()):
            if quote.author not in seen_authors and quote.snippet:
                seen_authors.add(quote.author)
                row.quotes.append(quote)
        for board in boards:
            entry = board.entries.get(player.key)
            if entry is not None:
                row.entries[board.source_id] = entry
        row.listed_by = sum(1 for sid in RECOMMENDING if sid in row.entries)
        rows.append(row)

    def key(row: BoardRow):
        ranks = [row.entries[s].pos_rank for s in RECOMMENDING
                 if s in row.entries and row.entries[s].pos_rank is not None]
        top10 = [int(m[1]) for e in row.entries.values()
                 if e.tier and (m := re.match(r"top-10 #(\d+)", e.tier))]
        charts = [row.entries.get(s) for s in CHARTS]
        return (-row.listed_by, min(ranks) if ranks else 999, min(top10) if top10 else 99,
                -sum(1 for c in charts if c is not None),
                *[-(c.value or 0) if c is not None else 0 for c in charts],
                row.player.name)
    rows.sort(key=key)
    return rows


#: The writers' board shows at least this many rows before "Show all".
BOARD_MIN_ROWS = 10


def board_visible_keys(rows) -> set:
    """Which writers'-board rows the page shows before "Show all".

    The board lists every free agent any writer named (~70 a league on a
    Tuesday), most of them on one writer's list and untouched by this tool.
    Visible by default: the top of the list (``build_board`` sorts by how many
    writers recommend a player, so this is where they agree), at least
    ``BOARD_MIN_ROWS`` deep and deep enough to hold everyone two writers named,
    plus every player this tool acted on wherever he sits. Display only: the
    folded rows are one click away on the page (and in a folded section of the
    issue), and no add or drop reads this. One rule for the page, the issue and
    Discord, so the three agree on which rows lead.
    """
    agree = sum(1 for r in rows if r.listed_by >= 2)
    depth = max(agree, BOARD_MIN_ROWS)
    return {r.player.key for i, r in enumerate(rows) if i < depth or r.tool}


def board_reasons(boards: Sequence[ExpertBoard]) -> list[str]:
    """One line per writer whose article couldn't be read, in its own words
    ("…not posted yet" stays distinct from "…could not be verified")."""
    return [b.unavailable[ALL_POSITIONS] for b in boards if ALL_POSITIONS in b.unavailable]


def read_sources(boards: Sequence[ExpertBoard],
                 positions: Optional[Sequence[str]] = None) -> list[str]:
    """Writer boards read this run, as retry-marker ids.

    Without ``positions`` a board is one id, read or not: right for the
    Tuesday articles, which arrive whole. With ``positions`` (a source that
    serves one chart per position, like Boone's values) the id is per position
    read, ``source:POS`` — otherwise a run missing TE and the retry that found
    it record the same id, and the gate suppresses the complete report.
    """
    ids: list[str] = []
    for b in boards:
        if b.source_id == FP_ROS_ID or ALL_POSITIONS in b.unavailable:
            continue
        if positions is None:
            ids.append(b.source_id)
        else:
            ids += [f"{b.source_id}:{pos}" for pos in positions if pos not in b.unavailable]
    return ids


# --- the retry gate ------------------------------------------------------------
#
# Tuesday runs twice: a first pass, and a later retry for the article that was
# not up yet. The retry must post only when it read something the earlier post
# didn't, or every Tuesday would land two identical messages. What was read is
# recorded in the digest itself, as an HTML comment the issue keeps, so the
# retry can compare against exactly what was published.

_MARKER = re.compile(r"<!-- ff-board season=(\d+) week=(\d+)(?: mode=(\w+))? read=([\w,:]*) -->")

#: Run kinds, weakest first. A marker written before modes existed reads as live.
MODES = ("preseason", "rehearsal", "live")


def board_marker(season: int, week: int, read: Iterable[str], mode: str = "live") -> str:
    return (f"<!-- ff-board season={season} week={week} mode={mode} "
            f"read={','.join(sorted(set(read)))} -->")


def run_mode(items) -> str:
    """The strongest mode among a run's bundles (or sheets carrying one).

    One live league makes the report live: that is the one carrying real
    advice. Anything without a ``mode`` counts as live.
    """
    modes = [getattr(i, "mode", None) or getattr(getattr(i, "bundle", None), "mode", None)
             or "live" for i in items]
    return max(modes, key=MODES.index) if modes else "live"


def bundles_read(bundles) -> list[str]:
    """Writer boards read in any league this run, plus this week's K/DEF lists.

    The K/DEF lists (CBS consensus, Boone's weekly) can veto a streaming swap,
    and both tend to post Tuesday evening — after the first pass and before the
    retry. Leaving them out of the marker made that retry read the lists and
    then decide there was nothing new, so the published picks never saw them.
    They are recorded per position (``cbs_consensus:K``), and only where the
    list actually ranked one of the players in the table: a list that was
    fetched but ranked nobody we asked about gave the report nothing.
    """
    seen: set[str] = set()
    for bundle in bundles:
        seen.update(read_sources(getattr(bundle, "expert_boards", [])))
        seen.update(weekly_read(bundle))
        # FantasyPros fills in through the week; a retry that finds its page on
        # the right week, or its consensus past FF_ECR_MIN_EXPERTS, is news.
        if getattr(bundle, "ecr_week_ok", None):
            seen.add("fantasypros_week")
        if getattr(bundle, "ecr_full", False):
            seen.add("fantasypros_full")
    return sorted(seen)


# --- the Wednesday "adds changed" gate -----------------------------------------
#
# Wednesday's values run rebuilds waivers.html with a fuller FantasyPros
# consensus and Boone's charts. It posts to Discord only when the claims
# themselves changed — which add, for which drop, in which league — so a
# Wednesday that agrees with Tuesday stays quiet. The signature rides in every
# digest as a second invisible marker, beside the retry marker above.

_ADDS_MARKER = re.compile(r"<!-- ff-adds season=(\d+) week=(\d+) sig=(\w+) -->")


def adds_signature(bundles) -> str:
    """A short hash of every league's claims: (league, add, drop)."""
    import hashlib
    lines = sorted(
        f"{b.label}|{t.score.player.key}|{t.drop.player.key if t.drop else ''}"
        for b in bundles for t in getattr(b, "adds", ()) or ())
    return hashlib.sha1("\n".join(lines).encode()).hexdigest()[:12]


def adds_marker(season: int, week: int, bundles) -> str:
    return f"<!-- ff-adds season={season} week={week} sig={adds_signature(bundles)} -->"


def adds_changed(current_text: str, previous_text: Optional[str]) -> bool:
    """Whether this digest's claims differ from the last ones published for its week.

    Compares against the *latest* marker for the same season and week, so a
    Wednesday update that was already posted (and commented on the issue) is
    what a later retry compares with. Nothing published for this week yet, or a
    current digest without a marker, counts as changed.
    """
    match = _ADDS_MARKER.search(current_text or "")
    if match is None:
        return True
    season, week, sig = match[1], match[2], match[3]
    published = [s for s_, w_, s in _ADDS_MARKER.findall(previous_text or "")
                 if s_ == season and w_ == week]
    return not published or published[-1] != sig


def weekly_read(bundle) -> list[str]:
    """``source:POS`` for each weekly K/DEF list that ranked someone in the table."""
    ids: list[str] = []
    rows = getattr(bundle, "stream_rows", None) or {}
    for board in getattr(bundle, "weekly_boards", None) or []:
        if ALL_POSITIONS in board.unavailable:
            continue
        for pos, pos_rows in rows.items():
            if pos in board.unavailable:
                continue
            if any(r.player.key in board.entries for r in pos_rows):
                ids.append(f"{board.source_id}:{pos}")
    return ids


def has_news(read: Iterable[str], previous_text: Optional[str], season: int, week: int,
             mode: str = "live") -> bool:
    """Whether this run is worth publishing over what was already published.

    True when nothing was published for this season, week and mode yet, or
    when this run read a writer the published report didn't. Mode matters
    because a preseason refusal, a dress rehearsal and the live report can
    all be "Week 1" with nothing read: the live one is never a repeat of
    either. Equal or fewer is not news:
    a retry that found the same articles would only post a duplicate, and one
    that found fewer (a page went down) would replace a better report.
    """
    published: Optional[set[str]] = None
    for s_, w_, m_, ids in _MARKER.findall(previous_text or ""):
        if int(s_) == season and int(w_) == week and (m_ or "live") == mode:
            got = {i for i in ids.split(",") if i}
            published = got if published is None else published | got
    if published is None:
        return True
    return bool(set(read) - published)


def published_before(current_text: str, previous_text: Optional[str]) -> bool:
    """Whether a report for this digest's season, week and mode is already out.

    ``report_has_news`` answers "publish at all?"; this answers "is this the
    first post or a later one?". A later Tuesday run that read something new
    still updates the issue and the page, but Discord already has the board and
    the claims, so ``cmd_waivers`` sends only a summary, and only when the
    claims changed (``adds_changed``).
    """
    match = _MARKER.search(current_text or "")
    if match is None:
        return False
    season, week, mode = match[1], match[2], match[3] or "live"
    return any(s_ == season and w_ == week and (m_ or "live") == mode
               for s_, w_, m_, _ in _MARKER.findall(previous_text or ""))


def report_has_news(current_text: str, previous_text: Optional[str]) -> bool:
    """``has_news`` read straight off two rendered digests.

    Every digest carries a marker, boards on or off: with them off it reads
    ``read=`` (nothing), so a retry is not news but next week's report is. A
    digest with no marker at all (rendered before the marker was universal) is
    news only when nothing has been published yet.
    """
    match = _MARKER.search(current_text or "")
    if match is None:
        return not (previous_text or "").strip()
    season, week, mode = int(match[1]), int(match[2]), match[3] or "live"
    ids = [i for i in match[4].split(",") if i]
    return has_news(ids, previous_text, season, week, mode)
