"""Weather — a scoring-environment signal for outdoor games (no API key needed).

Fantasy passing and kicking suffer in high wind and heavy rain, so this signal
nudges players in rough forecasts down and leaves fair-weather (and roofed) games
alone. It pulls the forecast from the free, keyless Open-Meteo API and turns wind
+ precipitation into a 0-100 "conditions" score (higher = better place to score).

It is a *game*-based signal, not a team-based one: the conditions belong to the
game, so both teams in a matchup share a score and a single forecast lookup. The
venue and kickoff come from ``sources/schedule.py`` — without that context a road
player would be forecast at their own empty stadium, a dome team playing outdoors
would get a free pass, and a Thursday or Monday game would be scored against
Sunday's weather.

Everything degrades to unavailable rather than to a guess: no schedule, no
kickoff, an unknown venue, a forecast that doesn't reach the game, or a failed
fetch all mark the player unavailable so the blender re-weights the remaining
signals. A wrong forecast presented as fact is worse than no forecast. Roofed
venues score neutral without a network call. Parsing and scoring are pure and
separated from HTTP so they test offline against a fixture.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Optional

import requests

from ..data.stadiums import Stadium
from ..models import GameContext, Player, SignalValue
from .base import Signal
from .schedule import ScheduleProvider, venue_for

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Conditions scoring: start from a perfect 100 and subtract wind/precip penalties.
DOME_SCORE = 100.0          # roofed venue: no weather effect
CALM_WIND_MPH = 8.0         # wind at/below this is harmless
WIND_PENALTY_PER_MPH = 3.0  # each mph above CALM_WIND_MPH costs this much
MAX_WIND_PENALTY = 70.0     # cap so a gale still leaves a floor
PRECIP_PENALTY_AT_100 = 30.0  # penalty when precipitation probability is 100%

#: Hours of forecast to consider from kickoff onward — long enough to cover a
#: game, short enough that the rest of the day's weather doesn't leak in.
GAME_HOURS = 4


def score_conditions(wind_mph: float, precip_prob: float) -> float:
    """Map wind (mph) + precipitation probability (0-100) to a 0-100 score.

    Calm and dry is ~100; wind is the dominant factor (it hurts passing and field
    goals most), precipitation a secondary one. Monotonic: worse weather never
    scores higher. Clamped to [0, 100].
    """
    wind_penalty = max(0.0, wind_mph - CALM_WIND_MPH) * WIND_PENALTY_PER_MPH
    wind_penalty = min(wind_penalty, MAX_WIND_PENALTY)
    precip_penalty = max(0.0, min(precip_prob, 100.0)) / 100.0 * PRECIP_PENALTY_AT_100
    return round(max(0.0, min(100.0, 100.0 - wind_penalty - precip_penalty)), 2)


def parse_hourly(blob: dict) -> dict[str, tuple[float, float]]:
    """Open-Meteo hourly blob -> ``{hour_iso: (wind_mph, precip_prob)}`` (pure).

    Keys are the API's own ``"YYYY-MM-DDTHH:MM"`` strings. Requested with
    ``timezone=UTC`` so they line up directly with the schedule's UTC kickoff —
    no local-timezone or DST arithmetic anywhere in this signal. Hours missing a
    wind reading are skipped rather than crashing.
    """
    hourly = (blob or {}).get("hourly") or {}
    times = hourly.get("time") or []
    winds = hourly.get("wind_speed_10m") or []
    precips = hourly.get("precipitation_probability") or []
    out: dict[str, tuple[float, float]] = {}
    for i, hour in enumerate(times):
        wind = winds[i] if i < len(winds) else None
        precip = precips[i] if i < len(precips) else None
        if wind is None:
            continue
        try:
            out[str(hour)] = (float(wind), float(precip) if precip is not None else 0.0)
        except (TypeError, ValueError):
            continue
    return out


def _hour_key(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:00")


def select_at_kickoff(parsed: dict[str, tuple[float, float]],
                      kickoff: Optional[datetime],
                      hours: int = GAME_HOURS) -> Optional[tuple[float, float]]:
    """Worst wind and worst precipitation across the game window (pure).

    Returns ``None`` when there is no kickoff or the forecast doesn't reach it —
    the honest answer outside the horizon is "no data", not a stand-in from some
    other time. (This replaces an earlier fallback that returned the *windiest
    day in the horizon*, which manufactured risk from weather that had nothing to
    do with the game.)

    Taking the max across the window rather than the single kickoff hour keeps a
    storm arriving at half-time visible.
    """
    if not parsed or kickoff is None:
        return None
    start = kickoff.replace(minute=0, second=0, microsecond=0)
    window = [parsed[key] for key in (_hour_key(start + timedelta(hours=h))
                                      for h in range(max(1, hours)))
              if key in parsed]
    # The kickoff hour itself must be covered; a window that only clips the tail
    # end of the horizon is not a forecast of this game.
    if _hour_key(start) not in parsed or not window:
        return None
    return max(w for w, _ in window), max(p for _, p in window)


class WeatherSignal(Signal):
    name = "weather"
    higher_is_better = True  # better conditions = better place to score

    def __init__(self, enabled: bool = True, session: Optional[requests.Session] = None,
                 timeout: int = 20, schedule: Optional[ScheduleProvider] = None):
        self.enabled = enabled
        self.session = session or requests.Session()
        self.timeout = timeout
        self.schedule = schedule
        # Cache keyed by (venue, kickoff hour) rather than by team, so both
        # teams in a game share one lookup and a Thursday game is never served
        # a Sunday forecast.
        self._cache: dict[tuple, tuple[Optional[float], str]] = {}

    def is_available(self) -> bool:
        return self.enabled and self.schedule is not None

    def fetch(self, week: int, players: Iterable[Player]) -> dict[str, SignalValue]:
        players = list(players)
        if not self.enabled:
            return {p.key: SignalValue(raw=None, available=False, note="weather disabled")
                    for p in players}
        if self.schedule is None:
            # Without game context the only honest options are "no value" or a
            # guess. Guessing is what this signal used to do.
            return {p.key: SignalValue(raw=None, available=False, note="no schedule")
                    for p in players}

        games = self.schedule.for_week(week)
        scores: dict[str, Optional[float]] = {}
        notes: dict[str, str] = {}
        for team in {p.team for p in players if p.team}:
            game = games.get(team)
            if game is None:
                scores[team] = None
                notes[team] = "not scheduled"
                continue
            score, note = self._score_for_game(game)
            scores[team] = score
            if note:
                notes[team] = note
        return self.assign(players, scores, notes)

    def _score_for_game(self, game: GameContext) -> tuple[Optional[float], str]:
        key = (game.venue_id or game.venue_name or game.home_team,
               _hour_key(game.kickoff) if game.kickoff else None)
        if key in self._cache:
            return self._cache[key]
        result = self._compute_game(game)
        self._cache[key] = result
        return result

    def _compute_game(self, game: GameContext) -> tuple[Optional[float], str]:
        venue = venue_for(game)
        if venue is None:
            return None, ""          # unknown venue (e.g. a new neutral site)
        if venue.dome:
            return DOME_SCORE, ""    # roofed: no network call needed
        if game.kickoff is None:
            return None, ""          # no kickoff -> no hour to forecast
        try:
            parsed = parse_hourly(self._fetch_forecast(venue, game.kickoff))
        except Exception:
            return None, ""
        cond = select_at_kickoff(parsed, game.kickoff)
        if cond is None:
            return None, ""          # game is outside the forecast horizon
        wind, precip = cond
        return score_conditions(wind, precip), _condition_note(wind, precip)

    def _fetch_forecast(self, venue: Stadium, kickoff: datetime) -> dict:
        # Two days so a late kickoff whose window crosses midnight UTC is still
        # fully covered.
        start = kickoff.date()
        resp = self.session.get(
            FORECAST_URL,
            params={
                "latitude": venue.lat,
                "longitude": venue.lon,
                "hourly": "wind_speed_10m,precipitation_probability",
                "wind_speed_unit": "mph",
                "timezone": "UTC",
                "start_date": start.isoformat(),
                "end_date": (start + timedelta(days=1)).isoformat(),
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def assign(players: Iterable[Player], scores: dict[str, Optional[float]],
               notes: Optional[dict[str, str]] = None) -> dict[str, SignalValue]:
        """Map per-team conditions scores onto players (pure; used by tests).

        Mirrors ``VegasSignal.assign``: no team -> bye; team with a score ->
        available; missing/None score -> unavailable (not scheduled, unknown
        venue, or a forecast that couldn't be fetched or didn't reach the game).
        """
        notes = notes or {}
        out: dict[str, SignalValue] = {}
        for p in players:
            if not p.team:
                out[p.key] = SignalValue(raw=None, available=False, note="bye / no team")
                continue
            score = scores.get(p.team)
            if score is None:
                out[p.key] = SignalValue(raw=None, available=False,
                                         note=notes.get(p.team, "no forecast"))
            else:
                out[p.key] = SignalValue(raw=score, available=True, note=notes.get(p.team, ""))
        return out


def _condition_note(wind_mph: float, precip_prob: float) -> str:
    """A short flag for rough weather, or '' when conditions are unremarkable."""
    parts = []
    if wind_mph > CALM_WIND_MPH + 4:  # ~12+ mph starts mattering
        parts.append(f"wind {wind_mph:.0f}mph")
    if precip_prob >= 50:
        parts.append(f"{precip_prob:.0f}% precip")
    return ", ".join(parts)
