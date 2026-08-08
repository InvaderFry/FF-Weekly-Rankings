"""NFL schedule — the shared game context weather and Vegas both need.

Not a ``Signal``: it produces no per-player value and carries no blend weight.
It answers a prior question — *what is this player's game?* — that two signals
were previously each guessing at, wrongly:

* weather forecast every player at their own team's home stadium, so a road
  player got the wrong city and a dome team playing outdoors got a free pass;
* Vegas asked for "upcoming games" and filtered nothing, so once next week's
  lines were posted a team could carry the wrong week's number.

Source is ESPN's public scoreboard endpoint, which needs no key (matching
Open-Meteo and Sleeper elsewhere in the app), reflects in-season schedule moves,
and reports a per-game ``indoor`` flag — better information than a static
per-team roof table, since it is right about neutral-site and international games.

Parsing is pure and separated from HTTP so it tests offline against a fixture,
and every lookup fails soft: an unreachable or unparseable schedule yields an
empty index rather than an exception, and callers degrade from there.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import requests

from ..data.stadiums import STADIUMS, Stadium, neutral_venue
from ..data.teams import normalize_team
from ..models import GameContext

SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"

#: Regular season. ESPN uses 1=pre, 2=regular, 3=post.
SEASON_TYPE_REGULAR = 2

#: Schedules are near-static, but kickoffs move (flex scheduling), so this is
#: short enough to notice a change and long enough to stay off the wire.
SCHEDULE_CACHE_TTL = 6 * 3600


def parse_kickoff(raw: Optional[str]) -> Optional[datetime]:
    """ESPN's Zulu timestamp -> aware UTC datetime, or None if unusable.

    ``fromisoformat`` only learned to accept a trailing "Z" in 3.11 and this
    package supports 3.10, so the suffix is rewritten explicitly.
    """
    if not raw:
        return None
    text = str(raw).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_scoreboard(blob: dict) -> list[GameContext]:
    """ESPN scoreboard JSON -> ``GameContext`` list (pure).

    Every field is optional on the way in: an event missing competitors, teams
    or a venue is skipped rather than raising, because a single odd entry must
    not cost the whole week's schedule.
    """
    out: list[GameContext] = []
    for event in (blob or {}).get("events") or []:
        comps = event.get("competitions") or []
        if not comps:
            continue
        comp = comps[0]

        home = away = None
        for c in comp.get("competitors") or []:
            abbrev = normalize_team(((c.get("team") or {}).get("abbreviation")))
            if not abbrev:
                continue
            if c.get("homeAway") == "home":
                home = abbrev
            elif c.get("homeAway") == "away":
                away = abbrev
        if not home or not away:
            continue

        venue = comp.get("venue") or {}
        address = venue.get("address") or {}
        # A game outside the US is neutral-site even when ESPN doesn't say so.
        neutral = bool(comp.get("neutralSite")) or bool(address.get("country"))

        out.append(GameContext(
            home_team=home,
            away_team=away,
            kickoff=parse_kickoff(comp.get("date") or event.get("date")),
            venue_id=str(venue.get("id") or ""),
            venue_name=str(venue.get("fullName") or ""),
            indoor=bool(venue.get("indoor")),
            neutral_site=neutral,
        ))
    return out


def by_team(games: Iterable[GameContext]) -> dict[str, GameContext]:
    """``{team: its game}``. Teams on a bye are simply absent."""
    out: dict[str, GameContext] = {}
    for g in games:
        for team in (g.home_team, g.away_team):
            out.setdefault(team, g)
    return out


def venue_for(game: GameContext) -> Optional[Stadium]:
    """Where to forecast this game, or None when we can't say.

    Resolution order, most authoritative first:

    1. the feed says the venue is roofed — no coordinates needed at all;
    2. a known neutral-site venue (the international games), by name;
    3. an ordinary home game — the home team's stadium;
    4. otherwise None. Never the away team's stadium, and never the home team's
       stadium for a game played somewhere else: a wrong forecast presented as
       fact is worse than no forecast, which simply re-weights the other signals.
    """
    if game.indoor:
        return Stadium(0.0, 0.0, dome=True)
    if game.neutral_site:
        return neutral_venue(game.venue_name)
    return STADIUMS.get(game.home_team)


def _warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


class ScheduleProvider:
    """Resolves a week's games once, then serves them from memory.

    Injected into ``WeatherSignal`` and ``VegasSignal`` so both agree on who
    plays whom, where and when. ``for_week`` never raises — on any failure it
    warns and returns an empty index, and the signals degrade from there.
    """

    def __init__(self, season: Optional[int] = None,
                 session: Optional[requests.Session] = None, timeout: int = 20,
                 cache_dir: Optional[Path] = None):
        self.season = season
        self.session = session or requests.Session()
        self.timeout = timeout
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._weeks: dict[int, dict[str, GameContext]] = {}

    def _season(self) -> int:
        if self.season is None:
            from ..season import season_year
            self.season = season_year()
        return int(self.season)

    def for_week(self, week: int) -> dict[str, GameContext]:
        """``{team: GameContext}`` for ``week``, or ``{}`` when unavailable."""
        if week in self._weeks:
            return self._weeks[week]
        try:
            blob = self._load(week)
        except Exception as exc:  # network, JSON, anything — never fatal
            _warn(f"NFL schedule unavailable ({exc}); "
                  "signals that need it will be skipped this run.")
            games = []
        else:
            games = parse_scoreboard(blob)
            if not games:
                # Reaching the endpoint and parsing nothing is a *different*
                # failure from not reaching it, and a quieter one: it would
                # silently disable weather with nothing in the output to say so.
                _warn(f"NFL schedule for week {week} returned no games; "
                      "the endpoint may have changed shape.")
        index = by_team(games)
        self._weeks[week] = index
        return index

    def kickoff_window(self, week: int) -> Optional[tuple[datetime, datetime]]:
        """(first kickoff, last kickoff + 6h), for filtering by time."""
        kicks = [g.kickoff for g in set(self.for_week(week).values()) if g.kickoff]
        if not kicks:
            return None
        return min(kicks), max(kicks) + timedelta(hours=6)

    # --- fetching -------------------------------------------------------
    def _cache_path(self, week: int) -> Optional[Path]:
        if self.cache_dir is None:
            return None
        return self.cache_dir / f"schedule_{self._season()}_w{week}.json"

    def _load(self, week: int) -> dict:
        """Cached raw blob if fresh, else fetch and cache it.

        The *raw* payload is cached, not the parsed games, so a parser change
        doesn't require busting the cache.
        """
        path = self._cache_path(week)
        if path is not None and path.exists():
            if (time.time() - path.stat().st_mtime) < SCHEDULE_CACHE_TTL:
                try:
                    return json.loads(path.read_text())
                except ValueError:
                    pass  # unreadable cache -> refetch
        blob = self._fetch(week)
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_name(path.name + ".tmp")
                tmp.write_text(json.dumps(blob))
                tmp.replace(path)
            except OSError:
                pass  # caching is an optimization, never a hard requirement
        return blob

    def _fetch(self, week: int) -> dict:
        resp = self.session.get(
            SCOREBOARD_URL,
            params={"week": week, "seasontype": SEASON_TYPE_REGULAR,
                    "dates": self._season()},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()
