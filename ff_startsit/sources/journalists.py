"""Journalists ranking backbone — CBS (Richard/Eisenberg) + Yahoo (Boone).

An opt-in alternative to FantasyPros ECR that fills the same `ecr` signal slot, so
the blend (and Vegas layering) is unchanged — only the consensus source differs.

Each analyst contributes a per-player rank; we equal-average the available ranks
into one consensus rank. Data is sourced **scrape-then-CSV**: a best-effort scraper
runs only when a (verified) URL is configured, otherwise the analyst falls back to
its column in a hand-editable CSV. CBS is scoring-aware (PPR / Standard, averaged
for half-PPR); Boone is half-PPR.

Parsers are HTTP-free for testing, mirroring `sources/ecr.py`.
"""

from __future__ import annotations

import csv
from statistics import mean
from typing import Iterable, Optional

import requests
from bs4 import BeautifulSoup

from ..config import Settings
from ..data.matching import ExternalRow, match_rows
from ..data.teams import normalize_team
from ..models import Player, SignalValue
from .base import Signal

ANALYSTS = ("richard", "eisenberg", "boone")
_CORE_COLUMNS = {"name", "team", "position", "pos"}
_UA = {"User-Agent": "Mozilla/5.0 (ff-startsit)"}


# --------------------------------------------------------------------------
# Pure parsers (testable without network)
# --------------------------------------------------------------------------
def parse_journalists_csv(text: str) -> dict[str, list[ExternalRow]]:
    """Parse the wide CSV into {analyst_key: [ExternalRow]}.

    Header: name,team,position,<analyst1>,<analyst2>,... Each analyst column holds
    that analyst's rank for the player; blank cells are skipped.
    """
    reader = csv.DictReader(line for line in text.splitlines() if line.strip())
    if not reader.fieldnames:
        return {}
    fields = {(f or "").strip().lower(): f for f in reader.fieldnames}
    if "name" not in fields or not ({"position", "pos"} & set(fields)):
        return {}
    pos_field = fields.get("position") or fields.get("pos")
    analyst_fields = {k: v for k, v in fields.items() if k not in _CORE_COLUMNS}

    out: dict[str, list[ExternalRow]] = {a: [] for a in analyst_fields}
    for row in reader:
        name = (row.get(fields["name"]) or "").strip()
        pos = (row.get(pos_field) or "").strip().upper()
        team = normalize_team((row.get(fields.get("team", "")) or "").strip()) if "team" in fields else None
        if pos == "DST":
            pos = "DEF"
        if not name or not pos:
            continue
        for akey, col in analyst_fields.items():
            raw = (row.get(col) or "").strip()
            if not raw:
                continue
            try:
                rank = float(raw)
            except ValueError:
                continue
            out[akey].append(ExternalRow(name=name, team=team, position=pos, value=rank))
    return out


def parse_cbs_table(html: str) -> list[ExternalRow]:
    """Best-effort parse of a CBS rankings table into ExternalRows.

    Defensive: scans table rows for a rank number + a player name and a team/pos;
    returns [] if the structure isn't recognized. (Finalize against a live page.)
    """
    soup = BeautifulSoup(html, "html.parser")
    rows: list[ExternalRow] = []
    for tr in soup.select("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
        if len(cells) < 2:
            continue
        rank = _leading_int(cells[0])
        if rank is None:
            continue
        name = _player_name(tr) or cells[1]
        team = normalize_team(_find_team(cells))
        pos = _find_pos(cells)
        if not name or not pos:
            continue
        rows.append(ExternalRow(name=name, team=team, position=pos, value=float(rank)))
    return rows


def parse_boone_article(html: str) -> list[ExternalRow]:
    """Best-effort parse of a Yahoo Boone rankings article (tables, half-PPR)."""
    return parse_cbs_table(html)  # same table-scanning heuristic


# --- small parsing helpers ------------------------------------------------
def _leading_int(text: str) -> Optional[int]:
    token = text.strip().split(".")[0].split()[0] if text.strip() else ""
    return int(token) if token.isdigit() else None


def _player_name(tr) -> str:
    a = tr.find("a")
    return a.get_text(strip=True) if a else ""


def _find_team(cells: list[str]) -> Optional[str]:
    for c in cells:
        if normalize_team(c):
            return c
    return None


def _find_pos(cells: list[str]) -> Optional[str]:
    valid = {"QB", "RB", "WR", "TE", "K", "DEF", "DST"}
    for c in cells:
        token = c.strip().upper()
        if token in valid:
            return "DEF" if token == "DST" else token
        # e.g. "WR1", "RB23"
        for v in valid:
            if token.startswith(v) and token[len(v):].isdigit():
                return "DEF" if v == "DST" else v
    return None


# --------------------------------------------------------------------------
# Analyst sources (scrape-then-CSV)
# --------------------------------------------------------------------------
class AnalystSource:
    """One analyst's rankings: try the scraper (if configured), else the CSV."""

    def __init__(self, key: str, csv_rows: list[ExternalRow]):
        self.key = key
        self.csv_rows = csv_rows

    def get_rows(self, scoring: str, session: requests.Session) -> list[ExternalRow]:
        try:
            scraped = self._scrape(scoring, session)
        except requests.RequestException:
            scraped = []
        return scraped if scraped else self.csv_rows

    def _scrape(self, scoring: str, session: requests.Session) -> list[ExternalRow]:
        return []  # base: no scraper -> CSV only


class CBSAnalyst(AnalystSource):
    """Dave Richard / Jamey Eisenberg. Scrapes only when CBS_RANKINGS_URL is set."""

    # CBS scoring token per league scoring; half-PPR averages PPR + Standard.
    _SCORING_TOKENS = {"ppr": ["ppr"], "std": ["standard"], "half": ["ppr", "standard"]}

    def __init__(self, key: str, csv_rows: list[ExternalRow], url_template: str = ""):
        super().__init__(key, csv_rows)
        self.url_template = url_template

    def _scrape(self, scoring: str, session: requests.Session) -> list[ExternalRow]:
        if not self.url_template:
            return []
        tokens = self._SCORING_TOKENS.get(scoring, ["ppr"])
        per_token: list[list[ExternalRow]] = []
        for token in tokens:
            url = self.url_template.format(scoring=token, analyst=self.key)
            resp = session.get(url, headers=_UA, timeout=20)
            resp.raise_for_status()
            per_token.append(parse_cbs_table(resp.text))
        return _average_rows(per_token) if len(per_token) > 1 else (per_token[0] if per_token else [])


class YahooBooneAnalyst(AnalystSource):
    """Justin Boone (half-PPR). Scrapes only when BOONE_ARTICLE_URL is set."""

    def __init__(self, csv_rows: list[ExternalRow], article_url: str = ""):
        super().__init__("boone", csv_rows)
        self.article_url = article_url

    def _scrape(self, scoring: str, session: requests.Session) -> list[ExternalRow]:
        if not self.article_url:
            return []
        resp = session.get(self.article_url, headers=_UA, timeout=20)
        resp.raise_for_status()
        return parse_boone_article(resp.text)


def _average_rows(row_lists: list[list[ExternalRow]]) -> list[ExternalRow]:
    """Average ranks for the same (name, position) across multiple row lists."""
    from ..data.matching import player_match_key

    bucket: dict[tuple, list[ExternalRow]] = {}
    for rows in row_lists:
        for r in rows:
            bucket.setdefault(player_match_key(r.name, r.position), []).append(r)
    out: list[ExternalRow] = []
    for items in bucket.values():
        first = items[0]
        out.append(ExternalRow(name=first.name, team=first.team, position=first.position,
                               value=mean(i.value for i in items)))
    return out


# --------------------------------------------------------------------------
# The signal
# --------------------------------------------------------------------------
class JournalistsSignal(Signal):
    name = "ecr"               # fills the same consensus slot; keeps blend weights
    higher_is_better = False   # ranks: lower is better

    def __init__(self, settings: Settings, session: Optional[requests.Session] = None):
        self.settings = settings
        self.session = session or requests.Session()

    def is_available(self) -> bool:
        return True  # CSV fallback means it's always attemptable

    def _load_csv(self) -> dict[str, list[ExternalRow]]:
        path = self.settings.journalists_file
        if path and path.exists():
            return parse_journalists_csv(path.read_text())
        return {}

    def _analysts(self) -> list[AnalystSource]:
        csv_rows = self._load_csv()
        cbs_url = self.settings.cbs_rankings_url
        return [
            CBSAnalyst("richard", csv_rows.get("richard", []), cbs_url),
            CBSAnalyst("eisenberg", csv_rows.get("eisenberg", []), cbs_url),
            YahooBooneAnalyst(csv_rows.get("boone", []), self.settings.boone_article_url),
        ]

    def fetch(self, week: int, players: Iterable[Player]) -> dict[str, SignalValue]:
        players = list(players)
        scoring = self.settings.scoring

        # Per-player ranks contributed by each analyst.
        contributions: dict[str, list[float]] = {p.key: [] for p in players}
        for analyst in self._analysts():
            rows = analyst.get_rows(scoring, self.session)
            if not rows:
                continue
            matched = match_rows(players, rows).matched
            for key, row in matched.items():
                contributions[key].append(row.value)

        out: dict[str, SignalValue] = {}
        for p in players:
            ranks = contributions[p.key]
            if ranks:
                out[p.key] = SignalValue(raw=round(mean(ranks), 2), available=True)
            else:
                out[p.key] = SignalValue(raw=None, available=False, note="no journalist rank")
        return out
