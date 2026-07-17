import json
from datetime import date
from pathlib import Path

from ff_startsit.models import Player
from ff_startsit.sources.weather import (
    DOME_SCORE,
    WeatherSignal,
    parse_forecast,
    score_conditions,
    select_conditions,
    upcoming_sunday,
)

FIXTURES = Path(__file__).parent / "fixtures"


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeSession:
    """Returns the same forecast for every call and counts the calls."""

    def __init__(self, payload):
        self._payload = payload
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return _FakeResponse(self._payload)


def test_score_conditions_clear_is_perfect_and_monotonic():
    assert score_conditions(0, 0) == 100.0
    assert score_conditions(5, 0) == 100.0          # below the calm threshold
    assert score_conditions(8, 0) == 100.0
    # 18 mph: (18-8)*3 = 30 penalty.
    assert score_conditions(18, 0) == 70.0
    # 100% precip: 30 penalty.
    assert score_conditions(0, 100) == 70.0
    # Worse weather never scores higher.
    assert score_conditions(20, 50) < score_conditions(10, 20) < score_conditions(5, 0)
    # Clamped to a floor, never negative.
    assert score_conditions(80, 100) >= 0.0


def test_parse_forecast_reads_daily_block():
    blob = json.loads((FIXTURES / "open_meteo.json").read_text())
    parsed = parse_forecast(blob)
    assert parsed["2024-10-06"] == (22.0, 80.0)
    assert parsed["2024-10-07"] == (6.0, 0.0)


def test_select_conditions_prefers_gameday_else_windiest():
    parsed = {"2024-10-06": (22.0, 80.0), "2024-10-07": (6.0, 0.0)}
    assert select_conditions(parsed, "2024-10-07") == (6.0, 0.0)
    # Game day not in the horizon -> conservative fallback to the windiest day.
    assert select_conditions(parsed, "2099-01-01") == (22.0, 80.0)
    assert select_conditions({}, "2024-10-07") is None


def test_upcoming_sunday():
    # 2024-10-02 is a Wednesday -> next Sunday is the 6th.
    assert upcoming_sunday(date(2024, 10, 2)) == "2024-10-06"
    # A Sunday returns itself.
    assert upcoming_sunday(date(2024, 10, 6)) == "2024-10-06"


def test_assign_marks_bye_dome_and_missing():
    players = [
        Player(key="1", name="A", team="BUF", position="WR"),   # outdoor, scored
        Player(key="2", name="B", team=None, position="WR"),    # bye / no team
        Player(key="3", name="C", team="SF", position="WR"),    # no score available
    ]
    scores = {"BUF": 34.0, "SF": None}
    out = WeatherSignal.assign(players, scores, notes={"BUF": "wind 22mph"})
    assert out["1"].available and out["1"].raw == 34.0 and "wind" in out["1"].note
    assert not out["2"].available and "bye" in out["2"].note.lower()
    assert not out["3"].available


def test_fetch_scores_outdoor_skips_dome_and_bye():
    blob = json.loads((FIXTURES / "open_meteo.json").read_text())
    session = _FakeSession(blob)
    sig = WeatherSignal(session=session)
    players = [
        Player(key="1", name="Outdoor", team="BUF", position="WR"),   # outdoor
        Player(key="2", name="Roofed", team="NO", position="WR"),     # dome
        Player(key="3", name="Bye", team=None, position="WR"),        # bye
    ]
    out = sig.fetch(5, players)

    # Dome team is neutral without a network call; only the outdoor stadium is fetched.
    assert session.calls == 1
    assert out["2"].available and out["2"].raw == DOME_SCORE
    # Game day won't be in the fixture horizon -> windiest day (22mph, 80%): score 34.
    assert out["1"].available and out["1"].raw == 34.0
    assert not out["3"].available


def test_disabled_signal_is_unavailable():
    sig = WeatherSignal(enabled=False)
    assert sig.is_available() is False
    players = [Player(key="1", name="A", team="BUF", position="WR")]
    out = sig.fetch(5, players)
    assert not out["1"].available and "disabled" in out["1"].note
