from pathlib import Path

import pytest

from ff_startsit.roster.base import RosterError
from ff_startsit.roster.manual import parse_manual_csv

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_manual_csv_normalizes_and_skips_bad_rows():
    warnings = []
    text = (FIXTURES / "manual_roster.csv").read_text()
    players = parse_manual_csv(text, warn=warnings.append)

    by_name = {p.name: p for p in players}
    # Full team name and lowercase abbrev both normalize.
    assert by_name["Patrick Mahomes"].team == "KC"
    assert by_name["CeeDee Lamb"].team == "DAL"
    # DST is accepted and stored as DEF.
    assert by_name["San Francisco"].position == "DEF"
    # The broken row (no position) and the duplicate Mahomes are skipped + reported.
    assert len(players) == 4
    assert any("no name" in w or "invalid position" in w for w in warnings)
    assert any("duplicate" in w for w in warnings)


def test_missing_required_column_raises():
    with pytest.raises(RosterError):
        parse_manual_csv("name,team\nFoo,KC\n")


def test_no_valid_players_raises():
    with pytest.raises(RosterError):
        parse_manual_csv("name,team,position\n,,\n")
