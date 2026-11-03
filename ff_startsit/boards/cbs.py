"""CBS staff consensus, plus Eisenberg's and Richard's own weekly ranks.

Source: ``cbssports.com/fantasy/football/rankings/<scoring>/<POS>/weekly/``,
server-rendered HTML. Each row carries ``RK`` (the analysts' average, sorted --
verified against every fully ranked row on 2026-11-02), the player, his
matchup, and one column per analyst. The header **names** each analyst, in
column order, which is what makes the per-analyst ranks attributable.

This is *not* the trap ``CLAUDE.md`` documents. The per-analyst slug pages
(``.../RB/jamey-eisenberg/``) serve byte-identical consensus whatever the slug,
so they fail open. This page is one document whose columns say whose they are,
so the parser can fail closed:

* the header must name analysts, and every row must carry exactly that many
  analyst cells, or the page is refused;
* the title must name the position asked for, and "PPR" when PPR was asked for;
* the matchups on the page must be the requested week's, checked against the
  schedule, because the page itself never states which week it is.

CBS publishes PPR and standard only. A half-PPR league gets the PPR list,
labelled "(PPR)" on every column, never passed off as half-PPR.
"""

from __future__ import annotations

import html as htmllib
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

import requests

from ..cache import atomic_write_text, read_json_or_none
from ..data.matching import ExternalRow, is_defense, match_rows
from ..data.teams import normalize_team
from ..models import Player
from ..net import HEADERS
from .models import ALL_POSITIONS, BoardEntry, ExpertBoard

URL = "https://www.cbssports.com/fantasy/football/rankings/{scoring}/{pos}/weekly/"
#: League scoring -> CBS path segment. CBS has no half-PPR list (its half-ppr
#: URL serves a different, analyst-less table), so half reads the PPR one.
SCORING_PATH = {"ppr": "ppr", "half": "ppr", "std": "standard"}
POSITIONS = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "K": "K", "DEF": "DST", "DST": "DST"}
#: The page updates several times a day; an hour keeps one run's leagues on one read.
TTL = 3600
#: Share of checkable rows whose matchup must match the requested week.
WEEK_AGREEMENT = 0.9

CONSENSUS_ID = "cbs_consensus"
#: Analysts shown as their own columns: board id, column header, name in the page header.
ANALYST_COLUMNS = (("cbs_eisenberg", "Eisenberg", "Jamey Eisenberg"),
                   ("cbs_richard", "Richard", "Dave Richard"))


class CbsParseError(ValueError):
    """The page could not be attributed or read; never returns partial guesses."""


@dataclass
class CbsRow:
    name: str
    team: Optional[str]
    opponent: Optional[str]
    rank: int
    analysts: dict[str, Optional[int]] = field(default_factory=dict)


@dataclass
class CbsPage:
    position: str
    analysts: list[str]
    rows: list[CbsRow]


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


#: Whatever follows the name inside the player cell; the name ends at the first.
_AFTER_NAME = re.compile(r'FantasyRankingsTable-(?:teamPosition|icons|matchup)"')


def _player_name(cell: str) -> str:
    start = cell.find('FantasyRankingsTable-playerName">')
    if start < 0:
        raise CbsParseError("row has no player name")
    rest = cell[start + len('FantasyRankingsTable-playerName">'):]
    end = _AFTER_NAME.search(rest)
    inner = rest[:end.start()] if end else rest
    # CBS renders both "Jahmyr " and "J. " and hides one by CSS; keep the full name.
    inner = re.sub(r'<span class="FantasyRankingsTable-firstInitial">.*?</span>', "", inner, flags=re.S)
    inner = re.sub(r"<span[^>]*$", "", inner)   # the opening tag of what follows
    name = _text(inner)
    if not name:
        raise CbsParseError("row has an empty player name")
    return name


def _team(cell: str) -> Optional[str]:
    m = re.search(r'FantasyRankingsTable-teamPosition">([^<]+)<', cell)
    if m:
        return normalize_team(m.group(1).strip())
    m = re.search(r"/logos/[^/]+/([A-Z]{2,3})\.png", cell)   # defenses: team logo
    return normalize_team(m.group(1)) if m else None


def _opponent(cell: str) -> Optional[str]:
    m = re.search(r'FantasyRankingsTable-opponent[^"]*">\s*(?:vs|@)\s*([A-Z]{2,3})\s*<', cell)
    return normalize_team(m.group(1)) if m else None


def parse_page(body: str, position: str, scoring_path: str) -> CbsPage:
    """Parse one CBS weekly ranking page, refusing anything it can't attribute."""
    pos_path = POSITIONS[position]
    title = _text((re.search(r"<title>(.*?)</title>", body, re.S) or [None, ""])[1])
    if f" {pos_path} Rankings" not in title:
        raise CbsParseError(f"page title does not name {pos_path} rankings ({title!r})")
    if scoring_path == "ppr" and "PPR" not in title:
        raise CbsParseError(f"page title does not say PPR ({title!r})")
    analysts = [_text(n) for n in re.findall(r'FantasyRankingsTable-expertName">(.*?)<', body, re.S)]
    if not analysts:
        raise CbsParseError("page names no analysts, so its columns can't be attributed")
    rows: list[CbsRow] = []
    for tr in re.findall(r'<tr class="[^"]*FantasyRankingsTable-row[^"]*">(.*?)</tr>', body, re.S):
        if "FantasyRankingsTable-td--rank" not in tr:
            continue
        rank = _text(re.search(r'td--rank">(.*?)</td>', tr, re.S).group(1))
        cells = [_text(c) for c in re.findall(r'td--expert">(.*?)</td>', tr, re.S)]
        if len(cells) != len(analysts):
            raise CbsParseError(f"row has {len(cells)} analyst cells for {len(analysts)} named analysts")
        if not rank.isdigit():
            raise CbsParseError(f"rank {rank!r} is not a number")
        player = re.search(r'td--player">(.*?)</td>', tr, re.S).group(1)
        rows.append(CbsRow(
            name=_player_name(player), team=_team(player), opponent=_opponent(player),
            rank=int(rank),
            analysts={a: (int(c) if c.isdigit() else None) for a, c in zip(analysts, cells)}))
    if not rows:
        raise CbsParseError("page has no ranked rows")
    return CbsPage(position=position, analysts=analysts, rows=rows)


def week_mismatch(page: CbsPage, games: dict, week: int) -> Optional[str]:
    """Why this page isn't ``week``'s, or None when its matchups agree.

    ``games`` is ``ScheduleProvider.for_week(week)``: ``{team: GameContext}``.
    A team missing from it is on bye that week, so a row giving it an opponent
    is a disagreement too.
    """
    checked = agree = 0
    for row in page.rows:
        if not row.team or not row.opponent:
            continue
        checked += 1
        game = games.get(row.team)
        if game is not None and game.opponent(row.team) == row.opponent:
            agree += 1
    if checked and agree < WEEK_AGREEMENT * checked:
        return (f"CBS {POSITIONS[page.position]} page shows another week's matchups "
                f"({checked - agree} of {checked} disagree with the Week {week} schedule)")
    return None


def _warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


class CbsFetcher:
    """Builds the CBS boards for a run. Shared across leagues; never raises."""

    def __init__(self, data_dir: Optional[Path] = None, schedule=None,
                 session: Optional[requests.Session] = None, timeout: int = 20):
        self.data_dir = Path(data_dir) if data_dir else None
        self.session = session or requests.Session()
        self.timeout = timeout
        self._schedule = schedule
        self._pages: dict[tuple[str, str], object] = {}
        #: Data status lines, one per CBS scoring path read this run.
        self.status: dict[str, str] = {}

    # -- transport ------------------------------------------------------
    def _get(self, scoring_path: str, pos_path: str) -> str:
        url = URL.format(scoring=scoring_path, pos=pos_path)
        path = (self.data_dir / f"cbs-rankings-{scoring_path}-{pos_path}.json"
                if self.data_dir else None)
        if path:
            stored = read_json_or_none(path)
            if (isinstance(stored, dict) and isinstance(stored.get("body"), str)
                    and isinstance(stored.get("fetched"), (int, float))
                    and 0 <= time.time() - stored["fetched"] < TTL):
                return stored["body"]
        resp = self.session.get(url, headers=HEADERS, timeout=self.timeout)
        resp.raise_for_status()
        if path:
            atomic_write_text(path, json.dumps({"fetched": time.time(), "body": resp.text}))
        return resp.text

    def _schedule_for(self, week: int) -> dict:
        if self._schedule is None:
            from ..sources.schedule import ScheduleProvider
            self._schedule = ScheduleProvider(cache_dir=self.data_dir)
        try:
            return self._schedule.for_week(week) or {}
        except Exception:  # the schedule provider never raises, but be sure
            return {}

    def page(self, position: str, week: int, scoring: str):
        """A verified ``CbsPage``, or the reason there isn't one (a str)."""
        scoring_path = SCORING_PATH.get(scoring)
        if scoring_path is None:
            return f"CBS publishes no {scoring} rankings"
        if position not in POSITIONS:
            return f"CBS has no {position} list"
        key = (scoring_path, POSITIONS[position], week)
        if key in self._pages:
            return self._pages[key]
        try:
            page = parse_page(self._get(scoring_path, POSITIONS[position]), position, scoring_path)
            games = self._schedule_for(week)
            reason = week_mismatch(page, games, week) if games else None
            result = reason or page
            if not games:
                self.status[scoring_path] = (f"CBS {scoring_path.upper()} rankings: matchups not "
                                             f"checked against Week {week} (schedule unavailable)")
        except (requests.RequestException, CbsParseError, AttributeError) as exc:
            result = f"CBS {POSITIONS[position]} rankings unavailable ({exc})"
            _warn(result)
        self._pages[key] = result
        return result

    # -- boards ---------------------------------------------------------
    def fetch(self, players: Sequence[Player], week: int, scoring: str) -> list[ExpertBoard]:
        """Three boards: CBS average, Eisenberg, Richard. Empty ones are kept,
        carrying their reasons; ``section_boards`` hides a column that reads
        nobody, so a missing list never prints a column of dashes."""
        label_scoring = "PPR" if scoring == "half" else None
        consensus = ExpertBoard(CONSENSUS_ID, "CBS", "CBS staff consensus (average of CBS analysts)",
                                scoring_label=label_scoring,
                                fetched_at=datetime.now(timezone.utc))
        named = {page_name: ExpertBoard(board_id, short, f"{page_name} (CBS)",
                                        scoring_label=label_scoring)
                 for board_id, short, page_name in ANALYST_COLUMNS}
        by_pos: dict[str, list[Player]] = {}
        for p in players:
            by_pos.setdefault("DEF" if is_defense(p.position) else p.position, []).append(p)
        for pos, group in sorted(by_pos.items()):
            result = self.page(pos, week, scoring)
            if isinstance(result, str):
                for board in (consensus, *named.values()):
                    board.unavailable[pos] = result
                continue
            rows = [ExternalRow(name=r.name, team=r.team,
                                position="DEF" if pos == "DEF" else pos,
                                value=float(r.rank), extra={"row": r})
                    for r in result.rows]
            for key, ext in match_rows(group, rows).matched.items():
                row: CbsRow = ext.extra["row"]
                consensus.entries[key] = BoardEntry(pos_rank=float(row.rank))
                for page_name, board in named.items():
                    rank = row.analysts.get(page_name)
                    if rank is not None:
                        board.entries[key] = BoardEntry(pos_rank=float(rank))
            for page_name, board in named.items():
                if page_name not in result.analysts:
                    board.unavailable[pos] = f"{page_name} is not among CBS's {pos} analysts this week"
        if not by_pos:
            consensus.unavailable[ALL_POSITIONS] = "no players to rank"
        return [consensus, *named.values()]
