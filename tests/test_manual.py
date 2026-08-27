from pathlib import Path

import pytest

from ff_startsit.roster.base import RosterError
from ff_startsit.roster.manual import (ManualProvider, parse_manual_csv,
                                       write_template)

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


# --- the starter template --------------------------------------------------
def test_write_template_never_overwrites_an_existing_file(tmp_path):
    """It runs on a *failure* path, and a failure path must not destroy data.

    The repo ships a filled-in `manual_roster.csv.example`, and a user who copied
    and edited it in place has their edits in exactly this file. Regenerating over
    it discards them to re-offer a template that is strictly poorer than what was
    already there.
    """
    example = tmp_path / "roster.csv.example"
    example.write_text("name,team,position\nMine,KC,QB\n")

    assert write_template(example) is False
    assert example.read_text() == "name,team,position\nMine,KC,QB\n"


def test_write_template_does_write_when_nothing_is_there(tmp_path):
    example = tmp_path / "roster.csv.example"
    assert write_template(example) is True
    assert "name,team,position" in example.read_text()


def test_a_missing_roster_points_at_the_template_either_way(tmp_path):
    """Whether it wrote one or found one, the error names the file to copy."""
    roster = tmp_path / "roster.csv"

    with pytest.raises(RosterError, match="A template was written to"):
        ManualProvider(roster).get_roster_players()

    # Second run: the example now exists, so the wording changes and the file
    # keeps whatever the user has since put in it.
    (tmp_path / "roster.csv.example").write_text("name,team,position\nEdited,KC,QB\n")
    with pytest.raises(RosterError, match="There is already a template at"):
        ManualProvider(roster).get_roster_players()
    assert "Edited" in (tmp_path / "roster.csv.example").read_text()
