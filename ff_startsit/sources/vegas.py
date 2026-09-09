"""Vegas implied team totals — signal #5, the scoring-environment nudge.

Pulls NFL spreads + totals from The Odds API, averages across books for a
consensus line, derives each team's implied total, and assigns it to players by
team. Players on a bye, or whose game has no posted line, are marked unavailable
so the blender falls back to ECR alone for them.

The endpoint takes no week parameter — it returns every upcoming game — so once
next week's lines are posted a team appears twice and the wrong week's number can
win. Games are therefore filtered against the schedule layer before the implied
totals are flattened. Without a schedule it falls back to first-occurrence-wins,
which given the API's kickoff ordering still means the sooner game.

Parsing is separated from HTTP so it can be tested against a saved API fixture.

The response is **disk-cached**, unlike most per-instance memoization in this
package, because the payload is the one thing here that does not vary by league:
``cli._league_bundles`` builds a fresh signal set per league, and each workflow
run scores every league twice (the pass itself, plus the sibling page rebuild
that keeps a Pages deploy from dropping the other page). That is six identical
fetches of one league-independent document per run — and The Odds API bills two
credits a call, one per market, against a 500/month free tier. The cache makes a
whole run cost one call.
"""

from __future__ import annotations

import time
from pathlib import Path
from statistics import mean
from typing import Iterable, Mapping, Optional

import requests

from .. import cache
from ..data.teams import normalize_team
from ..models import Game, GameContext, Player, SignalValue
from .base import Signal
from .schedule import ScheduleProvider, parse_kickoff

ODDS_URL = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"
#: Seconds a cached odds payload stays usable. Deliberately short: lines really
#: do move, and the only duplication this needs to absorb is within a single
#: workflow run, whose fetches land minutes apart.
ODDS_CACHE_TTL = 30 * 60


def parse_odds_response(events: list[dict]) -> list[Game]:
    """Convert The Odds API event list into consensus ``Game`` lines.

    For each event we average the totals and the home-team spread across all
    books that posted them, yielding a single consensus line per game.
    """
    games: list[Game] = []
    for ev in events:
        home = normalize_team(ev.get("home_team"))
        away = normalize_team(ev.get("away_team"))
        if not home or not away:
            continue

        totals: list[float] = []
        home_spreads: list[float] = []
        for book in ev.get("bookmakers", []):
            for market in book.get("markets", []):
                key = market.get("key")
                outcomes = market.get("outcomes", [])
                if key == "totals":
                    for o in outcomes:
                        if o.get("point") is not None:
                            totals.append(float(o["point"]))
                            break  # over/under share the same total
                elif key == "spreads":
                    for o in outcomes:
                        if normalize_team(o.get("name")) == home and o.get("point") is not None:
                            home_spreads.append(float(o["point"]))

        if not totals or not home_spreads:
            continue
        games.append(Game(home_team=home, away_team=away,
                          total=mean(totals), home_spread=mean(home_spreads),
                          kickoff=parse_kickoff(ev.get("commence_time"))))
    return games


def games_for_week(games: Iterable[Game],
                   week_games: Mapping[str, GameContext]) -> list[Game]:
    """Keep only the games belonging to the requested week (pure).

    The odds endpoint returns every upcoming game with no week parameter, so
    once next week's lines are posted a team can appear twice. Matching the
    (home, away) pair against the week's schedule is exact and doesn't depend on
    the book having supplied a ``commence_time``.

    With no schedule the games are returned unfiltered, and
    ``implied_totals_by_team``'s first-occurrence-wins is what keeps the sooner
    game. There is deliberately no kickoff-window fallback in between: the only
    source of a window is the same schedule lookup, so it is empty in exactly
    the case it would be needed.
    """
    if not week_games:
        return list(games)
    return [g for g in games
            if (ctx := week_games.get(g.home_team)) is not None
            and ctx.home_team == g.home_team and ctx.away_team == g.away_team]


def implied_totals_by_team(games: Iterable[Game]) -> dict[str, float]:
    """Flatten games into ``{team: implied_total}``.

    First occurrence wins. The odds endpoint returns every upcoming game, so a
    team can appear twice once next week's line is posted alongside this week's;
    since the API orders events by kickoff, keeping the first means keeping the
    sooner game rather than silently overwriting it with a later one.
    """
    out: dict[str, float] = {}
    for g in games:
        for team in (g.home_team, g.away_team):
            it = g.implied_total(team)
            if it is not None:
                out.setdefault(team, it)
    return out


class VegasSignal(Signal):
    name = "vegas"
    higher_is_better = True  # a higher implied total is a better scoring spot

    def __init__(self, api_key: str = "", session: Optional[requests.Session] = None,
                 timeout: int = 20, schedule: Optional[ScheduleProvider] = None,
                 cache_dir: Optional[Path] = None):
        self.api_key = api_key
        self.session = session or requests.Session()
        self.timeout = timeout
        self.schedule = schedule
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._games: Optional[list[Game]] = None  # per-instance cache

    def is_available(self) -> bool:
        return bool(self.api_key)

    def fetch(self, week: int, players: Iterable[Player]) -> dict[str, SignalValue]:
        players = list(players)
        if not self.api_key:
            return {p.key: SignalValue(raw=None, available=False, note="no ODDS_API_KEY")
                    for p in players}

        # The raw fetch isn't week-parameterized, so it's cached once and the
        # week filter is applied per call.
        games = games_for_week(
            self._fetch_games(),
            self.schedule.for_week(week) if self.schedule else {},
        )
        totals = implied_totals_by_team(games)
        return self.assign(players, totals)

    @staticmethod
    def assign(players: Iterable[Player], totals: dict[str, float]) -> dict[str, SignalValue]:
        """Map per-team implied totals onto players (pure; used by tests)."""
        out: dict[str, SignalValue] = {}
        for p in players:
            if not p.team:
                out[p.key] = SignalValue(raw=None, available=False, note="bye / no team")
            elif p.team in totals:
                out[p.key] = SignalValue(raw=totals[p.team], available=True)
            else:
                out[p.key] = SignalValue(raw=None, available=False, note="no line for team")
        return out

    def _fetch_games(self) -> list[Game]:
        # The odds endpoint returns every upcoming game regardless of position,
        # so one fetch serves a whole-roster pass; cache it on the instance.
        if self._games is not None:
            return self._games
        self._games = parse_odds_response(self._load())
        return self._games

    # --- fetching -------------------------------------------------------
    def _cache_path(self) -> Optional[Path]:
        # No week and no league in the name because the payload has neither: the
        # endpoint takes no week parameter, and odds are the same document for
        # every league. Freshness is the TTL's job, not the filename's.
        if self.cache_dir is None:
            return None
        return self.cache_dir / "odds_nfl.json"

    def _load(self) -> list[dict]:
        """The cached raw event list if fresh, else fetch and cache it.

        The *raw* payload is cached rather than the parsed games, so a parser
        change doesn't require busting the cache — same contract as
        ``ScheduleProvider._load``.
        """
        path = self._cache_path()
        if path is not None and path.exists():
            if (time.time() - path.stat().st_mtime) < ODDS_CACHE_TTL:
                blob = cache.read_json_or_none(path)
                if isinstance(blob, list):
                    return blob      # else: unreadable cache -> refetch
        blob = self._fetch()
        if path is not None:
            try:
                cache.write_json(path, blob)
            except OSError:
                pass  # caching is an optimization, never a hard requirement
        return blob

    def _fetch(self) -> list[dict]:
        resp = self.session.get(
            ODDS_URL,
            params={
                "apiKey": self.api_key,
                "regions": "us",
                "markets": "spreads,totals",
                "oddsFormat": "american",
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()
