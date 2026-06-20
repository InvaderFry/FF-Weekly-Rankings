import json
from pathlib import Path

from ff_startsit.roster.sleeper import build_players

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
