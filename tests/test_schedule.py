import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

from ff_startsit.models import GameContext
from ff_startsit.sources.schedule import (
    ScheduleProvider,
    by_team,
    parse_scoreboard,
    venue_for,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _blob():
    return json.loads((FIXTURES / "espn_scoreboard.json").read_text())


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload=None, exc=None):
        self._payload = payload
        self._exc = exc
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        if self._exc:
            raise self._exc
        return _FakeResp(self._payload)


def test_parse_scoreboard_reads_teams_venue_and_kickoff():
    games = parse_scoreboard(_blob())
    # The malformed event (no competitors) is skipped, not fatal.
    assert len(games) == 4

    buf = next(g for g in games if g.home_team == "BUF")
    assert buf.away_team == "NE"
    assert buf.venue_name == "Highmark Stadium"
    assert buf.indoor is False and buf.neutral_site is False
    assert buf.kickoff == datetime(2025, 10, 5, 17, 0, tzinfo=timezone.utc)


def test_parse_scoreboard_normalizes_espn_abbreviations():
    """ESPN says WSH and JAC; the rest of the app says WAS and JAX."""
    games = parse_scoreboard(_blob())
    assert any(g.home_team == "WAS" for g in games)
    assert any(g.home_team == "JAX" for g in games)


def test_parse_scoreboard_handles_empty_and_junk():
    assert parse_scoreboard({}) == []
    assert parse_scoreboard({"events": []}) == []
    assert parse_scoreboard({"events": [{"competitions": []}]}) == []


def test_game_context_opponent_and_home():
    g = GameContext(home_team="BUF", away_team="NE")
    assert g.opponent("NE") == "BUF" and g.opponent("BUF") == "NE"
    assert g.opponent("KC") is None
    assert g.is_home("BUF") and not g.is_home("NE")


def test_by_team_covers_both_sides_and_omits_byes():
    index = by_team(parse_scoreboard(_blob()))
    assert index["NE"].home_team == "BUF"      # away team finds its game
    assert index["BUF"].away_team == "NE"
    assert "KC" not in index                    # on a bye this week


def test_venue_for_ordinary_home_game():
    from ff_startsit.data.stadiums import STADIUMS

    index = by_team(parse_scoreboard(_blob()))
    # A road player's game resolves to the HOME team's stadium, which is the
    # whole point -- previously each player was forecast at their own venue.
    assert venue_for(index["NE"]) == STADIUMS["BUF"]


def test_venue_for_trusts_the_feeds_indoor_flag():
    index = by_team(parse_scoreboard(_blob()))
    assert venue_for(index["CHI"]).dome is True    # at Ford Field


def test_venue_for_resolves_a_known_neutral_site():
    """A London game must not be forecast in Jacksonville."""
    from ff_startsit.data.stadiums import STADIUMS

    index = by_team(parse_scoreboard(_blob()))
    london = venue_for(index["JAX"])
    assert london is not None
    assert london != STADIUMS["JAX"]
    assert 51.0 < london.lat < 52.0               # actually in London


def test_venue_for_unknown_neutral_site_is_none():
    """Unknown venue -> no forecast, rather than the wrong city's forecast."""
    g = GameContext(home_team="JAX", away_team="MIN",
                    venue_name="Some New Stadium", neutral_site=True)
    assert venue_for(g) is None


def test_provider_caches_in_memory():
    session = _FakeSession(_blob())
    provider = ScheduleProvider(season=2025, session=session, cache_dir=None)
    provider.for_week(5)
    provider.for_week(5)
    assert len(session.calls) == 1
    assert session.calls[0] == {"week": 5, "seasontype": 2, "dates": 2025}


def test_provider_returns_empty_index_on_failure(capsys):
    """A dead endpoint must not raise -- callers degrade, they don't crash."""
    session = _FakeSession(exc=requests.RequestException("boom"))
    provider = ScheduleProvider(season=2025, session=session, cache_dir=None)
    assert provider.for_week(5) == {}
    assert "schedule unavailable" in capsys.readouterr().err


def test_provider_warns_when_the_shape_changes(capsys):
    """Reaching the endpoint and parsing nothing is its own failure mode."""
    session = _FakeSession({"events": []})
    provider = ScheduleProvider(season=2025, session=session, cache_dir=None)
    assert provider.for_week(5) == {}
    assert "returned no games" in capsys.readouterr().err


def test_provider_disk_cache_avoids_a_second_fetch(tmp_path):
    session = _FakeSession(_blob())
    ScheduleProvider(season=2025, session=session, cache_dir=tmp_path).for_week(5)
    assert (tmp_path / "schedule_2025_w5.json").exists()

    # A fresh provider (empty memory cache) reads the file instead of refetching.
    ScheduleProvider(season=2025, session=session, cache_dir=tmp_path).for_week(5)
    assert len(session.calls) == 1


def test_kickoff_window_spans_the_week():
    session = _FakeSession(_blob())
    provider = ScheduleProvider(season=2025, session=session, cache_dir=None)
    start, end = provider.kickoff_window(5)
    assert start == datetime(2025, 10, 3, 0, 15, tzinfo=timezone.utc)   # TNF
    assert end > datetime(2025, 10, 5, 17, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("raw,expected", [
    ("2025-10-05T17:00Z", datetime(2025, 10, 5, 17, 0, tzinfo=timezone.utc)),
    ("2025-10-05T17:00:00Z", datetime(2025, 10, 5, 17, 0, tzinfo=timezone.utc)),
    ("not a date", None),
    (None, None),
])
def test_kickoff_parsing_is_lenient(raw, expected):
    from ff_startsit.sources.schedule import _parse_kickoff
    assert _parse_kickoff(raw) == expected
