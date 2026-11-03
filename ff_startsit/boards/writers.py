"""The writers' Tuesday articles as boards: Boone's and Eisenberg's waiver
columns, and Dave Richard's trade chart (which doubles as his rest-of-season
rankings).

Each source finds this week's article through ``ArticleFetcher`` (week in the
link, byline + week + kind verified in the article itself), parses it once per
week into ``Listed`` rows, and then matches those rows against whichever
league's players it is handed. The parse is league-agnostic and memoized; only
the (pure) matching runs per league, the same split ``waivers/columns.py`` uses.

The page shapes were verified against live articles on 2026-11-02 and are
pinned by trimmed fixtures in ``tests/fixtures/boards/``. Every parser raises
``ValueError`` on a page it doesn't recognise, which the fetcher turns into a
"layout not recognised" reason rather than a guess.
"""

from __future__ import annotations

import html as htmllib
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Sequence

from ..data.matching import ExternalRow, match_rows
from ..data.teams import normalize_team
from ..models import Player
from .articles import ArticleFetcher
from .base import ExpertSource
from .models import ALL_POSITIONS, BoardEntry, ExpertBoard

OFFENSE = ("QB", "RB", "WR", "TE")


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


@dataclass
class Listed:
    """One player as an article lists him, before matching to a roster."""
    name: str
    position: str
    team: Optional[str] = None
    pos_rank: Optional[int] = None
    values: dict[str, float] = field(default_factory=dict)
    bid: Optional[str] = None
    tier: Optional[str] = None


# --- Jamey Eisenberg (CBS) -------------------------------------------------------

_FAAB_RE = re.compile(r"((?:at least )?\d+(?:\s*-\s*\d+)?) percent of (?:your )?remaining FAAB", re.I)


def faab_short(phrase: str) -> str:
    """'at least 10' -> '10%+ FAAB'; '5-10' -> '5–10% FAAB'; '1' -> '1% FAAB'."""
    phrase = phrase.strip().lower()
    if phrase.startswith("at least "):
        return f"{phrase[9:].strip()}%+ FAAB"
    span = re.sub(r"\s*-\s*", "–", phrase)   # outside the f-string: 3.10 rejects \ in one
    return f"{span}% FAAB"


def parse_eisenberg(body: str) -> list[Listed]:
    """His "Add in this order" list per position: player cards in priority
    order, each ending with a FAAB recommendation.

    Positions come from each card's own label; a non-offensive label (Travis
    Hunter is listed as CB among the receivers) takes the section's majority.
    """
    end = body.find("</article>")
    end = end if end >= 0 else len(body)
    starts = [m.start() for m in re.finditer(r"Add in this order", body)]
    if not starts:
        raise ValueError("no 'Add in this order' lists")
    listed: list[Listed] = []
    for a, b in zip(starts, starts[1:] + [end]):
        seg = body[a:b]
        cuts = [m.start() for m in re.finditer(r'<div class="PlayerObjectV4">', seg)]
        cards = []
        for c, d in zip(cuts, cuts[1:] + [len(seg)]):
            card = seg[c:d]
            name = re.search(r'PlayerObjectV4-playerName">(.*?)</(?:a|div)>', card, re.S)
            pos = re.search(r'PlayerObjectV4-playerPosition">\s*([A-Z/]+)', card)
            team = re.search(r'href="/nfl/teams/([A-Z]{2,3})/', card)
            faab = _FAAB_RE.search(_text(card))
            if not name:
                continue
            cards.append((_text(name.group(1)), pos.group(1) if pos else "",
                          normalize_team(team.group(1)) if team else None,
                          faab_short(faab.group(1)) if faab else None))
        majority = Counter(p for _, p, _, _ in cards if p in OFFENSE).most_common(1)
        section_pos = majority[0][0] if majority else None
        for rank, (name, pos, team, bid) in enumerate(cards, start=1):
            pos = pos if pos in OFFENSE else section_pos
            if pos:
                listed.append(Listed(name, pos, team, pos_rank=rank, bid=bid))
    if not listed:
        raise ValueError("priority lists hold no player cards")
    return listed


# --- Justin Boone (Yahoo) --------------------------------------------------------

_SECTION_POS = {"quarterbacks": "QB", "running backs": "RB",
                "wide receivers": "WR", "tight ends": "TE"}
_BOONE_TOKENS = re.compile(
    r'<h2 class="heading"[^>]*>(?P<h2>.*?)</h2>'
    r'|<h3 class="heading"[^>]*>(?P<h3>.*?)</h3>'
    r'|<p><strong>(?P<label>.*?)</strong>(?P<after>.*?)</p>'
    r'|<li><div><p>(?P<li>.*?)</p></div></li>'
    r'|<table class="content-table">(?P<table>.*?)</table>', re.S)
_IF_AVAILABLE = re.compile(r"is (\d+)% rostered but would be the No\. (\d+) (QB|RB|WR|TE) add", re.I)


def tier_short(label: str) -> str:
    """Boone's list labels, shortened for a table cell: 'Dart-throw flex
    options' -> 'dart throw', 'Injured-reserve stashes' -> 'IR stash'."""
    t = label.strip().lower()
    for old, new in (("injured-reserve", "IR"), ("bye-week", "bye"), ("dart-throw flex", "dart throw"),
                     ("dart-throw streaming", "dart throw"), ("stashes to roster", "stash"),
                     ("stashes", "stash"), ("streaming options", "streamer"), ("options", "")):
        t = t.replace(old, new)
    return re.sub(r"\s+", " ", t).strip()


def parse_boone_waivers(body: str) -> list[Listed]:
    """Top-10 overall, then per position: featured players (with FAB bid and
    rostered %), the tiered lists, and "would be the No. K add, if available"."""
    top10: dict[str, int] = {}
    listed: list[Listed] = []
    section: Optional[str] = None      # a position, or "top10", or None (stop)
    tier: Optional[str] = None
    rank = 0
    last_featured: list[Listed] = []
    for m in _BOONE_TOKENS.finditer(body):
        if m.group("h2") is not None:
            section = _SECTION_POS.get(_text(m.group("h2")).lower())
            tier, rank, last_featured = None, 0, []
        elif m.group("h3") is not None:
            text = _text(m.group("h3"))
            if text.lower().startswith("top-10 overall"):
                section = "top10"
            elif section in OFFENSE:
                names, _, team = text.rpartition(",")
                if not names:
                    continue
                injured = "(IR)" in team
                team = normalize_team(team.replace("(IR)", "").strip())
                last_featured = []
                for name in names.split("/"):
                    rank += 1
                    item = Listed(name.strip(), section, team, pos_rank=rank,
                                  tier="featured, IR" if injured else "featured")
                    listed.append(item)
                    last_featured.append(item)
            elif section == "top10":
                section = None
        elif m.group("table") is not None and last_featured:
            cells = [_text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", m.group("table"), re.S)]
            head = [c.lower() for c in cells[:len(cells) // 2]]
            row = cells[len(cells) // 2:]
            if "fab" in head and len(row) == len(head):
                bid = row[head.index("fab")]
                # One heading, two players ("Antonio Williams/Treylon Burks"):
                # the table gives one bid each, "$9/$4", in heading order.
                bids = [b.strip() for b in bid.split("/")]
                if len(bids) != len(last_featured):
                    bids = [bid] * len(last_featured)
                for item, each in zip(last_featured, bids):
                    item.bid = each
            last_featured = []
        elif m.group("label") is not None and section in OFFENSE:
            label, after = _text(m.group("label")), _text(m.group("after"))
            hit = _IF_AVAILABLE.search(after)
            if hit:
                listed.append(Listed(label, hit.group(3).upper(), pos_rank=int(hit.group(2)),
                                     tier=f"No. {hit.group(2)} if available ({hit.group(1)}% rostered)"))
            elif not after:
                tier = tier_short(label)
            last_featured = []
        elif m.group("li") is not None:
            text = _text(m.group("li"))
            if section == "top10":
                parts = [p.strip() for p in text.split(",")]
                if len(parts) >= 2:
                    top10.setdefault(parts[0], len(top10) + 1)
            elif section in OFFENSE and tier:
                name = re.split(r"\s+(?:vs\.|at)\s+|\s+\(", text, maxsplit=1)[0].strip()
                if name:
                    rank += 1
                    listed.append(Listed(name, section, pos_rank=rank, tier=tier))
    if not listed:
        raise ValueError("no position sections with players")
    for item in listed:
        if item.name in top10:
            item.tier = f"top-10 #{top10[item.name]}"
    return listed


# --- Dave Richard (CBS) ----------------------------------------------------------

_CHART_POS = {"quarterback": "QB", "running back": "RB", "wide receiver": "WR", "tight end": "TE"}
#: League scoring -> the chart column for RB/WR/TE.
SCORING_COLUMN = {"std": "non", "half": "0.5", "ppr": "ppr"}


def parse_richard(body: str) -> list[Listed]:
    """His four "<Position> trade values" tables: Player, tm, then a value per
    format (QB: 1QB-4 / 1QB-6 / 2QB; others: non / 0.5 / PPR). "--" = no value."""
    listed: list[Listed] = []
    for heading, table in re.findall(r"<h2>\s*([^<]*?trade values)\s*</h2>\s*<table.*?>(.*?)</table>",
                                     body, re.S | re.I):
        pos = _CHART_POS.get(heading.lower().replace("trade values", "").strip())
        if pos is None:
            continue
        rows = re.findall(r"<tr>(.*?)</tr>", table, re.S)
        if not rows:
            continue
        head = [_text(c).lower() for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", rows[0], re.S)]
        if len(head) < 3 or head[0] != "player" or head[1] not in ("tm", "team"):
            raise ValueError(f"{pos} table header not recognised: {head}")
        for row in rows[1:]:
            cells = [_text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            if len(cells) != len(head):
                continue
            values = {}
            for col, cell in zip(head[2:], cells[2:]):
                try:
                    values[col] = float(cell)
                except ValueError:
                    pass            # "--": no value in that format
            listed.append(Listed(cells[0], pos, normalize_team(cells[1]), values=values))
    if not listed:
        raise ValueError("no trade-value tables")
    return listed


def chart_column(position: str, scoring: str, superflex: bool = False) -> str:
    if position == "QB":
        return "2qb" if superflex else "1qb-4"
    return SCORING_COLUMN.get(scoring, "ppr")


def rank_by_value(listed: Sequence[Listed], column: str) -> dict[int, int]:
    """Competition rank (1, 2, 2, 4) within the list by ``column``, highest first."""
    valued = sorted((item.values[column], i) for i, item in enumerate(listed) if column in item.values)
    valued.reverse()
    ranks: dict[int, int] = {}
    for place, (value, i) in enumerate(valued, start=1):
        ranks[i] = place if place == 1 or value != valued[place - 2][0] else ranks[valued[place - 2][1]]
    return ranks


# --- the sources -----------------------------------------------------------------

class ArticleBoardSource(ExpertSource):
    """One writer's weekly article, parsed once per week, matched per league."""
    author = ""
    index_url = ""
    base_url = ""
    article_kind = ""
    link_must: tuple = ()
    headline_must: tuple = ()
    never: tuple = ()

    def __init__(self, fetcher: Optional[ArticleFetcher] = None, season: Optional[int] = None):
        self.fetcher = fetcher or ArticleFetcher()
        self.season = season
        self._weeks: dict[int, tuple] = {}

    def parse(self, body: str) -> list[Listed]:
        raise NotImplementedError

    def _accepts(self, body: str) -> bool:
        try:
            return bool(self.parse(body))
        except ValueError:
            return False

    def article(self, week: int) -> tuple[list[Listed], Optional[str], Optional[str]]:
        """(listed players, url, None) or ([], None, reason). Memoized per week."""
        if week not in self._weeks:
            from ..season import season_year
            season = self.season or season_year()
            url, body, reason = self.fetcher.find_article(
                self.index_url, self.base_url, self.author, season, week, self.article_kind,
                self.link_must, self.headline_must, self.never, accept=self._accepts)
            self._weeks[week] = (self.parse(body), url, None) if body else ([], None, reason)
        return self._weeks[week]

    def entry(self, item: Listed, position_list: Sequence[Listed], index: int,
              scoring: str, superflex: bool) -> Optional[BoardEntry]:
        return BoardEntry(pos_rank=item.pos_rank, bid=item.bid, tier=item.tier)

    def fetch(self, players: Sequence[Player], week: int, scoring: str,
              superflex: bool = False) -> ExpertBoard:
        board = ExpertBoard(self.id, self.short, self.label, kind=self.kind,
                            fetched_at=datetime.now(timezone.utc))
        listed, url, reason = self.article(week)
        if reason:
            board.unavailable[ALL_POSITIONS] = reason
            return board
        board.url = url
        by_pos: dict[str, list[Listed]] = {}
        for item in listed:
            by_pos.setdefault(item.position, []).append(item)
        rows = []
        for pos, items in by_pos.items():
            for i, item in enumerate(items):
                entry = self.entry(item, items, i, scoring, superflex)
                if entry is not None:
                    rows.append(ExternalRow(item.name, item.team, pos, value=0.0, extra={"entry": entry}))
        for key, ext in match_rows(players, rows).matched.items():
            board.entries[key] = ext.extra["entry"]
        return board


class EisenbergWaivers(ArticleBoardSource):
    id, short, kind = "eisenberg_waivers", "Eisenberg", "waiver"
    label = "Jamey Eisenberg's waiver column (CBS)"
    author = "Jamey Eisenberg"
    index_url = "https://www.cbssports.com/writers/jamey-eisenberg/"
    base_url = "https://www.cbssports.com"
    article_kind = "waiver column"
    link_must = ("waiver-wire",)
    headline_must = ("waiver wire",)

    def parse(self, body):
        return parse_eisenberg(body)


class BooneWaivers(ArticleBoardSource):
    id, short, kind = "boone_waivers", "Boone", "waiver"
    label = "Justin Boone's waiver column (Yahoo)"
    author = "Justin Boone"
    index_url = "https://sports.yahoo.com/author/justin-boone/"
    base_url = "https://sports.yahoo.com"
    article_kind = "waiver column"
    link_must = ("waiver-wire",)
    headline_must = ("waiver wire",)
    never = ("college",)        # he also writes a college waiver column, titled by week

    def parse(self, body):
        return parse_boone_waivers(body)


class RichardChart(ArticleBoardSource):
    id, short, kind = "richard_chart", "Richard", "ros"
    label = "Dave Richard's trade chart / rest-of-season values (CBS)"
    author = "Dave Richard"
    index_url = "https://www.cbssports.com/writers/dave-richard/"
    base_url = "https://www.cbssports.com"
    article_kind = "trade chart"
    link_must = ("trade-chart",)
    headline_must = ("trade chart",)

    def parse(self, body):
        return parse_richard(body)

    def entry(self, item, position_list, index, scoring, superflex):
        column = chart_column(item.position, scoring, superflex)
        if column not in item.values:
            return None             # no value in this league's format
        ranks = rank_by_value(position_list, column)
        return BoardEntry(pos_rank=ranks.get(index), value=item.values[column])


def writer_sources(fetcher: Optional[ArticleFetcher] = None) -> list[ArticleBoardSource]:
    """The Tuesday sources, in column order. Share one fetcher across leagues."""
    fetcher = fetcher or ArticleFetcher()
    return [BooneWaivers(fetcher), EisenbergWaivers(fetcher), RichardChart(fetcher)]
