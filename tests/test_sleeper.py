import json
from pathlib import Path

from ff_startsit.roster.sleeper import SleeperClient, build_players
from ff_startsit.season import date_week

FIXTURES = Path(__file__).parent / "fixtures"


def test_build_players_filters_and_normalizes():
    meta = json.loads((FIXTURES / "sleeper_players.json").read_text())
    players = build_players(["100", "101", "102", "103", "200", "KC", "999"], meta)
    by_key = {p.key: p for p in players}

    # OL ("999") is dropped; everything else fantasy-relevant is kept.
    assert "999" not in by_key
    assert by_key["100"].name == "Patrick Runner"
    assert by_key["100"].team == "KC"
    assert by_key["103"].team is None          # no team -> bye/FA
    assert by_key["200"].position == "K"
    assert by_key["KC"].position == "DEF"
    assert by_key["KC"].team == "KC"


class _StubClient(SleeperClient):
    """A client whose only network edge is replaced by a canned /state/nfl."""

    def __init__(self, state):
        super().__init__(data_dir=Path("."))
        self._state = state

    def _get(self, path):
        assert path == "/state/nfl"
        return self._state


def test_preseason_week_is_not_reported_as_a_fantasy_week():
    """/state/nfl counts preseason games in August — week 3 there is the third
    preseason game, not Week 3 of the season."""
    assert _StubClient({"season_type": "pre", "week": 3}).current_week() == 1


def test_regular_season_week_is_taken_as_is():
    assert _StubClient({"season_type": "regular", "week": 7}).current_week() == 7


def test_a_missing_season_type_falls_back_to_the_date_never_zero():
    week = _StubClient({"week": 0}).current_week()
    assert week == date_week()
    assert week >= 1
