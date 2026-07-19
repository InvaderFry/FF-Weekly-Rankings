"""FantasyPros Expert Consensus Rankings (ECR) — signal #4, the backbone.

Two interchangeable paths:
  1. API key (``FANTASYPROS_API_KEY``) against the consensus-rankings endpoint.
  2. Fallback: scrape the public weekly rankings page, whose HTML embeds an
     ``ecrData`` JSON blob with the same fields.

Both paths produce ``ExternalRow``s, which are matched onto the roster. Parsing is
separated from HTTP so the parsers can be unit-tested against saved fixtures.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Iterable, Optional

import requests

from ..data.matching import ExternalRow, match_rows
from ..data.teams import normalize_team
from ..models import Player, SignalValue
from .base import Signal

API_URL = "https://api.fantasypros.com/v2/json/nfl/{season}/consensus-rankings"
SCRAPE_URL = "https://www.fantasypros.com/nfl/rankings/{slug}.php"

# Positions whose page/scoring slug ignores scoring format.
_SCORING_AGNOSTIC = {"QB", "K", "DST", "DEF"}
_ECR_DATA_RE = re.compile(r"var\s+ecrData\s*=\s*(\{.*?\})\s*;", re.DOTALL)


def _scrape_slug(position: str, scoring: str) -> str:
    pos = position.lower()
    if position.upper() in {"DST", "DEF"}:
        return "dst"
    if position.upper() in _SCORING_AGNOSTIC:
        return pos
    if scoring == "ppr":
        return f"ppr-{pos}"
    if scoring == "half":
        return f"half-point-ppr-{pos}"
    return pos  # standard


def parse_api_response(payload: dict) -> list[ExternalRow]:
    """Parse the FantasyPros consensus-rankings API JSON into rows."""
    rows: list[ExternalRow] = []
    for p in payload.get("players", []):
        rank = p.get("rank_ecr") or p.get("rank_ave")
        if rank is None:
            continue
        rows.append(
            ExternalRow(
                name=p.get("player_name", ""),
                team=normalize_team(p.get("player_team_id")),
                position=(p.get("player_position_id") or "").upper(),
                value=float(rank),
                extra={"tier": p.get("tier"), "rank_min": p.get("rank_min"),
                       "rank_max": p.get("rank_max")},
            )
        )
    return rows


def parse_scrape_html(html: str) -> list[ExternalRow]:
    """Extract the embedded ``ecrData`` JSON from a rankings page and parse it."""
    m = _ECR_DATA_RE.search(html)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    return parse_api_response(data)


def fetch_api_rows(session: requests.Session, api_key: str, season: int, scoring: str,
                   position: str, week: int, timeout: int = 20,
                   filters: Optional[str] = None) -> list[ExternalRow]:
    """Fetch rankings rows from the consensus-rankings API.

    ``filters`` is a colon-separated string of FantasyPros expert ids; when set,
    the returned consensus covers only those experts (a single id yields that
    expert's individual ranks).
    """
    params = {
        "type": "weekly",
        "scoring": _api_scoring(scoring),
        "position": _api_pos(position),
        "week": week,
    }
    if filters:
        params["filters"] = filters
    resp = session.get(
        API_URL.format(season=season),
        params=params,
        headers={"x-api-key": api_key},
        timeout=timeout,
    )
    resp.raise_for_status()
    return parse_api_response(resp.json())


def fetch_scrape_rows(session: requests.Session, scoring: str, position: str,
                      timeout: int = 20,
                      filters: Optional[str] = None) -> list[ExternalRow]:
    """Fetch rankings rows by scraping the public rankings page.

    ``filters`` narrows the page's embedded ``ecrData`` to the given
    colon-separated expert ids, same as the API path.
    """
    params = {"filters": filters} if filters else None
    resp = session.get(
        SCRAPE_URL.format(slug=_scrape_slug(position, scoring)),
        params=params,
        headers={"User-Agent": "Mozilla/5.0 (ff-startsit)"},
        timeout=timeout,
    )
    resp.raise_for_status()
    return parse_scrape_html(resp.text)


class ECRSignal(Signal):
    name = "ecr"
    higher_is_better = False  # a lower ECR rank is better

    def __init__(self, api_key: str = "", scoring: str = "ppr",
                 season: Optional[int] = None, session: Optional[requests.Session] = None,
                 timeout: int = 20):
        self.api_key = api_key
        self.scoring = scoring
        self.season = season or _current_season()
        self.session = session or requests.Session()
        self.timeout = timeout
        self.last_source: str = ""  # "api" or "scrape", for diagnostics
        self._rows_cache: dict[tuple[str, int], list[ExternalRow]] = {}

    def is_available(self) -> bool:
        return True  # scrape fallback means ECR is always attemptable

    def fetch(self, week: int, players: Iterable[Player]) -> dict[str, SignalValue]:
        players = list(players)
        positions = sorted({_canon_pos(p.position) for p in players})

        rows: list[ExternalRow] = []
        for pos in positions:
            rows.extend(self._rows_for_position(pos, week))

        result = match_rows(players, rows)
        out: dict[str, SignalValue] = {}
        for p in players:
            row = result.matched.get(p.key)
            if row is None:
                out[p.key] = SignalValue(raw=None, available=False, note="no ECR rank")
            else:
                out[p.key] = SignalValue(raw=row.value, available=True)
        return out

    def _rows_for_position(self, position: str, week: int) -> list[ExternalRow]:
        cache_key = (position, week)
        if cache_key in self._rows_cache:
            return self._rows_cache[cache_key]
        rows = self._rows_uncached(position, week)
        self._rows_cache[cache_key] = rows
        return rows

    def _rows_uncached(self, position: str, week: int) -> list[ExternalRow]:
        if self.api_key:
            try:
                rows = self._fetch_api(position, week)
                if rows:
                    self.last_source = "api"
                    return rows
            except (requests.RequestException, ValueError):
                pass  # fall through to scrape
        try:
            rows = self._fetch_scrape(position)
        except requests.RequestException:
            return []  # offline / page unreachable -> signal simply has no data
        self.last_source = "scrape"
        if not rows:
            # Reached the page but parsed nothing: the embedded ecrData blob is
            # gone or changed shape. Warn so a silently-broken scrape is visible.
            print(f"warning: FantasyPros scrape for {position} returned no "
                  "rankings — the page format may have changed.", file=sys.stderr)
        return rows

    def _fetch_api(self, position: str, week: int) -> list[ExternalRow]:
        return fetch_api_rows(self.session, self.api_key, self.season, self.scoring,
                              position, week, timeout=self.timeout)

    def _fetch_scrape(self, position: str) -> list[ExternalRow]:
        return fetch_scrape_rows(self.session, self.scoring, position,
                                 timeout=self.timeout)


def _canon_pos(position: str) -> str:
    p = (position or "").upper()
    return "DST" if p in {"DEF", "DST"} else p


def _api_pos(position: str) -> str:
    return "DST" if position in {"DEF", "DST"} else position


def _api_scoring(scoring: str) -> str:
    return {"ppr": "PPR", "half": "HALF", "std": "STD"}.get(scoring, "PPR")


def _current_season() -> int:
    from ..season import season_year
    return season_year()
