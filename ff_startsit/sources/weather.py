"""Weather — a scoring-environment signal for outdoor games (no API key needed).

Fantasy passing and kicking suffer in high wind and heavy rain, so this signal
nudges players in rough forecasts down and leaves fair-weather (and roofed) games
alone. It pulls each stadium's forecast from the free, keyless Open-Meteo API,
turns wind + precipitation into a 0-100 "conditions" score (higher = better place
to score points), and assigns it to players by their team's home stadium.

Like Vegas, it is a team-based signal: players on a bye (no team), at an unknown
stadium, or whose forecast can't be fetched are marked unavailable so the blender
falls back to the other signals rather than penalizing them. Dome / retractable-
roof venues (see ``data/stadiums.py``) are weather-controlled, so they score
neutral without a network call.

**v1 game-day approximation:** we don't carry an NFL schedule, so we forecast the
*upcoming Sunday* (the modal game day) for each stadium, falling back to the
windiest day in the forecast horizon if that date isn't covered. Parsing and
scoring are pure and separated from HTTP so they test offline against a fixture.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, Optional

import requests

from ..data.stadiums import STADIUMS, Stadium
from ..models import Player, SignalValue
from .base import Signal

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Conditions scoring: start from a perfect 100 and subtract wind/precip penalties.
DOME_SCORE = 100.0          # roofed venue: no weather effect
CALM_WIND_MPH = 8.0         # wind at/below this is harmless
WIND_PENALTY_PER_MPH = 3.0  # each mph above CALM_WIND_MPH costs this much
MAX_WIND_PENALTY = 70.0     # cap so a gale still leaves a floor
PRECIP_PENALTY_AT_100 = 30.0  # penalty when precipitation probability is 100%


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


def parse_forecast(blob: dict) -> dict[str, tuple[float, float]]:
    """Open-Meteo daily blob -> ``{date_iso: (wind_mph, precip_prob)}`` (pure).

    Missing fields for a day are skipped rather than crashing. Expects the daily
    block requested with ``wind_speed_10m_max`` + ``precipitation_probability_max``.
    """
    daily = (blob or {}).get("daily") or {}
    times = daily.get("time") or []
    winds = daily.get("wind_speed_10m_max") or []
    precips = daily.get("precipitation_probability_max") or []
    out: dict[str, tuple[float, float]] = {}
    for i, day in enumerate(times):
        wind = winds[i] if i < len(winds) else None
        precip = precips[i] if i < len(precips) else None
        if wind is None:
            continue
        try:
            out[str(day)] = (float(wind), float(precip) if precip is not None else 0.0)
        except (TypeError, ValueError):
            continue
    return out


def select_conditions(parsed: dict[str, tuple[float, float]],
                      gameday: str) -> Optional[tuple[float, float]]:
    """Pick the game-day forecast, else the windiest day in the horizon (pure).

    Returns ``None`` when there is no forecast at all.
    """
    if not parsed:
        return None
    if gameday in parsed:
        return parsed[gameday]
    # No exact game-day match (e.g. forecast horizon fell short) — use the worst
    # wind day as a conservative proxy so we still surface the risk.
    return max(parsed.values(), key=lambda wp: wp[0])


def upcoming_sunday(today: Optional[date] = None) -> str:
    """ISO date of the coming Sunday (today if today is Sunday). Python Mon=0..Sun=6."""
    today = today or date.today()
    return (today + timedelta(days=(6 - today.weekday()) % 7)).isoformat()


class WeatherSignal(Signal):
    name = "weather"
    higher_is_better = True  # better conditions = better place to score

    def __init__(self, enabled: bool = True, session: Optional[requests.Session] = None,
                 timeout: int = 20):
        self.enabled = enabled
        self.session = session or requests.Session()
        self.timeout = timeout
        # Per-instance cache of team -> (conditions score or None, flag note), so a
        # whole-roster pass (report/lineup reuse one instance across positions) hits
        # each stadium once and surfaces the same flag every time.
        self._cache: dict[str, tuple[Optional[float], str]] = {}

    def is_available(self) -> bool:
        return self.enabled

    def fetch(self, week: int, players: Iterable[Player]) -> dict[str, SignalValue]:
        players = list(players)
        if not self.enabled:
            return {p.key: SignalValue(raw=None, available=False, note="weather disabled")
                    for p in players}

        gameday = upcoming_sunday()
        scores: dict[str, Optional[float]] = {}
        notes: dict[str, str] = {}
        for team in {p.team for p in players if p.team}:
            score, note = self._score_for_team(team, gameday)
            scores[team] = score
            if note:
                notes[team] = note
        return self.assign(players, scores, notes)

    def _score_for_team(self, team: str, gameday: str) -> tuple[Optional[float], str]:
        """Conditions score + optional flag note for a team (uses the instance cache)."""
        if team in self._cache:
            return self._cache[team]
        result = self._compute_team(team, gameday)
        self._cache[team] = result
        return result

    def _compute_team(self, team: str, gameday: str) -> tuple[Optional[float], str]:
        stadium = STADIUMS.get(team)
        if stadium is None:
            return None, ""
        if stadium.dome:
            return DOME_SCORE, ""
        try:
            parsed = parse_forecast(self._fetch_forecast(stadium))
        except Exception:
            return None, ""
        cond = select_conditions(parsed, gameday)
        if cond is None:
            return None, ""
        wind, precip = cond
        score = score_conditions(wind, precip)
        return score, _condition_note(wind, precip)

    def _fetch_forecast(self, stadium: Stadium) -> dict:
        resp = self.session.get(
            FORECAST_URL,
            params={
                "latitude": stadium.lat,
                "longitude": stadium.lon,
                "daily": "wind_speed_10m_max,precipitation_probability_max",
                "wind_speed_unit": "mph",
                "timezone": "America/New_York",
                "forecast_days": 7,
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
        available; missing/None score -> unavailable (unknown stadium or a
        forecast that couldn't be fetched).
        """
        notes = notes or {}
        out: dict[str, SignalValue] = {}
        for p in players:
            if not p.team:
                out[p.key] = SignalValue(raw=None, available=False, note="bye / no team")
                continue
            score = scores.get(p.team)
            if score is None:
                out[p.key] = SignalValue(raw=None, available=False, note="no forecast")
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
