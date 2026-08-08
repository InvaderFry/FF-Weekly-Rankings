import json
from datetime import datetime, timezone
from pathlib import Path

from ff_startsit.models import GameContext, Player
from ff_startsit.sources.schedule import ScheduleProvider
from ff_startsit.sources.weather import (
    DOME_SCORE,
    WeatherSignal,
    parse_hourly,
    score_conditions,
    select_at_kickoff,
)

FIXTURES = Path(__file__).parent / "fixtures"

KICKOFF = datetime(2025, 10, 5, 17, 0, tzinfo=timezone.utc)


def _hourly():
    return json.loads((FIXTURES / "open_meteo_hourly.json").read_text())


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeSession:
    """Returns the same forecast for every call and records the params."""

    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        return _FakeResponse(self._payload)


class _FakeSchedule:
    """Stands in for ScheduleProvider without touching the network."""

    def __init__(self, games):
        self._games = games

    def for_week(self, week):
        return self._games


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


def test_parse_hourly_reads_the_hourly_block():
    parsed = parse_hourly(_hourly())
    assert parsed["2025-10-05T17:00"] == (10.0, 20.0)
    assert parsed["2025-10-05T06:00"] == (40.0, 90.0)
    assert parse_hourly({}) == {}


def test_select_at_kickoff_uses_the_game_window_not_the_day():
    """A 6am gale must not score a 5pm kickoff.

    This is the concrete reason for going hourly: the daily *max* wind would
    have reported 40mph for a game played in 10mph conditions.
    """
    parsed = parse_hourly(_hourly())
    wind, precip = select_at_kickoff(parsed, KICKOFF)
    assert wind == 10.0 and precip == 20.0
    assert max(w for w, _ in parsed.values()) == 40.0   # the day's max, ignored


def test_select_at_kickoff_takes_the_worst_hour_in_the_window():
    """Weather arriving at half-time still counts."""
    parsed = {
        "2025-10-05T17:00": (5.0, 0.0),
        "2025-10-05T18:00": (25.0, 80.0),   # storm rolls in
        "2025-10-05T19:00": (6.0, 10.0),
        "2025-10-05T20:00": (6.0, 10.0),
    }
    assert select_at_kickoff(parsed, KICKOFF) == (25.0, 80.0)


def test_select_at_kickoff_outside_the_horizon_is_none():
    """Replaces the old windiest-day-in-the-horizon fallback.

    That fallback invented a worst-case number from an unrelated day and fed it
    into the blend; the honest answer is no data, which re-weights the rest.
    """
    parsed = parse_hourly(_hourly())
    far_future = datetime(2099, 1, 1, 13, 0, tzinfo=timezone.utc)
    assert select_at_kickoff(parsed, far_future) is None
    assert select_at_kickoff(parsed, None) is None
    assert select_at_kickoff({}, KICKOFF) is None


def test_assign_marks_bye_and_missing():
    players = [
        Player(key="1", name="A", team="BUF", position="WR"),
        Player(key="2", name="B", team=None, position="WR"),    # bye / no team
        Player(key="3", name="C", team="SF", position="WR"),    # no score available
    ]
    scores = {"BUF": 34.0, "SF": None}
    out = WeatherSignal.assign(players, scores, notes={"BUF": "wind 22mph"})
    assert out["1"].available and out["1"].raw == 34.0 and "wind" in out["1"].note
    assert not out["2"].available and "bye" in out["2"].note.lower()
    assert not out["3"].available


def test_forecasts_the_game_venue_not_the_players_own_stadium():
    """The core fix: a road player is scored where the game is played.

    NE at BUF must be forecast in Orchard Park, not Foxborough.
    """
    from ff_startsit.data.stadiums import STADIUMS

    game = GameContext(home_team="BUF", away_team="NE", kickoff=KICKOFF,
                       venue_id="3839", venue_name="Highmark Stadium")
    session = _FakeSession(_hourly())
    sig = WeatherSignal(session=session, schedule=_FakeSchedule({"BUF": game, "NE": game}))

    players = [Player(key="1", name="Home", team="BUF", position="WR"),
               Player(key="2", name="Road", team="NE", position="WR")]
    out = sig.fetch(5, players)

    # One forecast for the game, shared by both teams -- not one per team.
    assert len(session.calls) == 1
    assert session.calls[0]["latitude"] == STADIUMS["BUF"].lat
    assert out["1"].raw == out["2"].raw == score_conditions(10.0, 20.0)


def test_dome_team_playing_outdoors_gets_no_free_pass():
    """Previously DET always scored 100 on its own dome, even on the road."""
    game = GameContext(home_team="CHI", away_team="DET", kickoff=KICKOFF,
                       venue_name="Soldier Field")
    session = _FakeSession(_hourly())
    sig = WeatherSignal(session=session, schedule=_FakeSchedule({"CHI": game, "DET": game}))

    out = sig.fetch(5, [Player(key="1", name="Lion", team="DET", position="WR")])
    assert out["1"].available and out["1"].raw != DOME_SCORE


def test_indoor_flag_from_the_feed_wins():
    """An outdoor team playing in a dome is scored as roofed, with no fetch."""
    game = GameContext(home_team="DET", away_team="CHI", kickoff=KICKOFF,
                       venue_name="Ford Field", indoor=True)
    session = _FakeSession(_hourly())
    sig = WeatherSignal(session=session, schedule=_FakeSchedule({"CHI": game, "DET": game}))

    out = sig.fetch(5, [Player(key="1", name="Bear", team="CHI", position="WR")])
    assert out["1"].raw == DOME_SCORE
    assert session.calls == []


def test_thursday_kickoff_is_scored_at_its_own_hour():
    """A TNF game must not be scored against Sunday afternoon."""
    tnf = datetime(2025, 10, 5, 0, 0, tzinfo=timezone.utc)
    game = GameContext(home_team="BUF", away_team="NE", kickoff=tnf,
                       venue_name="Highmark Stadium")
    sig = WeatherSignal(session=_FakeSession(_hourly()),
                        schedule=_FakeSchedule({"BUF": game}))
    out = sig.fetch(5, [Player(key="1", name="A", team="BUF", position="WR")])
    # 00:00 reads 18mph/60%, distinct from the 17:00 window's 10mph/20%.
    assert out["1"].raw == score_conditions(18.0, 60.0)


def test_unknown_neutral_site_is_unavailable_not_the_home_city():
    game = GameContext(home_team="JAX", away_team="MIN", kickoff=KICKOFF,
                       venue_name="Brand New Stadium", neutral_site=True)
    session = _FakeSession(_hourly())
    sig = WeatherSignal(session=session, schedule=_FakeSchedule({"JAX": game}))
    out = sig.fetch(5, [Player(key="1", name="A", team="JAX", position="WR")])
    assert not out["1"].available
    assert session.calls == []          # never guessed at Jacksonville


def test_team_on_a_bye_is_unavailable():
    sig = WeatherSignal(session=_FakeSession(_hourly()), schedule=_FakeSchedule({}))
    out = sig.fetch(5, [Player(key="1", name="A", team="KC", position="WR")])
    assert not out["1"].available and "not scheduled" in out["1"].note


def test_without_a_schedule_the_signal_is_unavailable():
    """No game context means no honest forecast -- and it says so."""
    sig = WeatherSignal(session=_FakeSession(_hourly()), schedule=None)
    assert sig.is_available() is False
    out = sig.fetch(5, [Player(key="1", name="A", team="BUF", position="WR")])
    assert not out["1"].available and "no schedule" in out["1"].note


def test_failed_forecast_fetch_is_unavailable():
    import requests

    class _Boom:
        def get(self, *a, **kw):
            raise requests.RequestException("network down")

    game = GameContext(home_team="BUF", away_team="NE", kickoff=KICKOFF,
                       venue_name="Highmark Stadium")
    sig = WeatherSignal(session=_Boom(), schedule=_FakeSchedule({"BUF": game}))
    out = sig.fetch(5, [Player(key="1", name="A", team="BUF", position="WR")])
    assert not out["1"].available


def test_disabled_signal_is_unavailable():
    sig = WeatherSignal(enabled=False)
    assert sig.is_available() is False
    players = [Player(key="1", name="A", team="BUF", position="WR")]
    out = sig.fetch(5, players)
    assert not out["1"].available and "disabled" in out["1"].note


def test_real_provider_wires_through_end_to_end(tmp_path):
    """The signal and the real ScheduleProvider agree on shapes."""
    scoreboard = json.loads((FIXTURES / "espn_scoreboard.json").read_text())

    class _Sess:
        def __init__(self):
            self.hosts = []

        def get(self, url, params=None, timeout=None):
            self.hosts.append(url)
            payload = scoreboard if "espn" in url else _hourly()
            return _FakeResponse(payload)

    sess = _Sess()
    schedule = ScheduleProvider(season=2025, session=sess, cache_dir=None)
    sig = WeatherSignal(session=sess, schedule=schedule)
    out = sig.fetch(5, [Player(key="1", name="Patriot", team="NE", position="WR")])
    assert out["1"].available
    assert any("espn" in h for h in sess.hosts)
