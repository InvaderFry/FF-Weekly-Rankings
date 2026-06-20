from ff_startsit.data.matching import ExternalRow, match_rows, normalize_name
from ff_startsit.data.teams import normalize_team
from ff_startsit.models import Player


def test_normalize_name_strips_suffix_and_punct():
    assert normalize_name("Patrick Mahomes II") == "patrick mahomes"
    assert normalize_name("D.J. Moore") == "dj moore"
    assert normalize_name("A.J. Brown Jr.") == "aj brown"


def test_normalize_team_handles_aliases_and_full_names():
    assert normalize_team("JAC") == "JAX"
    assert normalize_team("Washington Commanders") == "WAS"
    assert normalize_team("kansas city chiefs") == "KC"
    assert normalize_team("KC") == "KC"
    assert normalize_team("Mars Rovers") is None


def test_match_rows_matches_and_reports_unmatched():
    players = [
        Player(key="1", name="Patrick Mahomes", team="KC", position="QB"),
        Player(key="2", name="Justin Jefferson", team="MIN", position="WR"),
    ]
    rows = [
        ExternalRow(name="Patrick Mahomes II", team="KC", position="QB", value=1),
        ExternalRow(name="Nonexistent Player", team="NE", position="WR", value=5),
    ]
    result = match_rows(players, rows)
    assert result.matched["1"].value == 1
    assert "2" not in result.matched           # no row for Jefferson
    assert len(result.unmatched) == 1
    assert result.unmatched[0].name == "Nonexistent Player"


def test_match_rows_disambiguates_by_team():
    players = [
        Player(key="a", name="Mike Williams", team="NYJ", position="WR"),
        Player(key="b", name="Mike Williams", team="LAC", position="WR"),
    ]
    rows = [ExternalRow(name="Mike Williams", team="LAC", position="WR", value=10)]
    result = match_rows(players, rows)
    assert "b" in result.matched
    assert "a" not in result.matched
