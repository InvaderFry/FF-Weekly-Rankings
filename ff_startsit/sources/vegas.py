"""Vegas implied team totals — signal #5, the scoring-environment nudge.

Pulls NFL spreads + totals from The Odds API, averages across books for a
consensus line, derives each team's implied total, and assigns it to players by
team. Players on a bye, or whose game has no posted line, are marked unavailable
so the blender falls back to ECR alone for them.

The endpoint takes no week parameter — it returns every upcoming game — so once
next week's lines are posted a team appears twice and the wrong week's number can
win. Games are therefore filtered against the schedule layer before the implied
totals are flattened. Without a schedule it falls back to the kickoff window, and
failing that to first-occurrence-wins, which given the API's kickoff ordering
still means the sooner game.

Parsing is separated from HTTP so it can be tested against a saved API fixture.
"""

from __future__ import annotations

from datetime import datetime
from statistics import mean
from typing import Iterable, Mapping, Optional

import requests

from ..data.teams import normalize_team
from ..models import Game, GameContext, Player, SignalValue
from .base import Signal
from .schedule import ScheduleProvider, parse_kickoff

ODDS_URL = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"


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


def games_for_week(games: Iterable[Game], week_games: Mapping[str, GameContext],
                   window: Optional[tuple[datetime, datetime]] = None) -> list[Game]:
    """Keep only the games belonging to the requested week (pure).

    The odds endpoint returns every upcoming game with no week parameter, so
    once next week's lines are posted a team can appear twice. Matching the
    (home, away) pair against the week's schedule is exact and doesn't depend on
    the book having supplied a ``commence_time``.

    ``window`` is the fallback when there is no schedule: keep games whose
    kickoff falls inside the week. Games with no kickoff at all are kept, since
    dropping them would lose data we have no evidence against.
    """
    if week_games:
        return [g for g in games
                if (ctx := week_games.get(g.home_team)) is not None
                and ctx.home_team == g.home_team and ctx.away_team == g.away_team]
    if window is not None:
        start, end = window
        return [g for g in games if g.kickoff is None or start <= g.kickoff <= end]
    return list(games)


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
                 timeout: int = 20, schedule: Optional[ScheduleProvider] = None):
        self.api_key = api_key
        self.session = session or requests.Session()
        self.timeout = timeout
        self.schedule = schedule
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
            self.schedule.kickoff_window(week) if self.schedule else None,
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
        self._games = parse_odds_response(resp.json())
        return self._games
