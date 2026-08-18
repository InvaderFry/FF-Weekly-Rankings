from ff_startsit.data.matching import (ExternalRow, match_rows, normalize_name,
                                        player_match_key)
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


def test_defense_matches_across_every_source_spelling():
    """ESPN, Sleeper, manual and FantasyPros each name a defense differently.

    ECR carries 0.60 of the blend, so a defense that fails to join is scored on
    Vegas/injury/weather alone — a silent hole in the DEF slot rather than a
    visible error.
    """
    players = [
        Player(key="espn-1", name="Chiefs D/ST", team="KC", position="DEF"),
        Player(key="SF", name="San Francisco", team="SF", position="DEF"),
    ]
    rows = [
        # FantasyPros spells the position "DST" and the name in full.
        ExternalRow(name="Kansas City Chiefs", team="KC", position="DST", value=3),
        ExternalRow(name="San Francisco 49ers", team="SF", position="DST", value=1),
    ]
    result = match_rows(players, rows)
    assert result.matched["espn-1"].value == 3
    assert result.matched["SF"].value == 1
    assert result.unmatched == []


def test_defense_key_is_the_team_regardless_of_spelling_or_position():
    keys = {
        player_match_key("Chiefs D/ST", "DEF"),
        player_match_key("Kansas City", "DEF"),
        player_match_key("Kansas City Chiefs", "DST"),
        player_match_key("KC D/ST", "DEF"),        # Sleeper's thin-metadata fallback
        player_match_key("KC", "DST"),
        player_match_key("Chiefs", "DEF"),
    }
    assert keys == {("def:KC", "DEF")}


def test_unresolvable_defense_falls_back_to_the_name():
    """A defense we can't map to a team keeps the old name-based key."""
    assert player_match_key("Mars Rovers D/ST", "DEF") == ("mars rovers d st", "DEF")


def test_non_defense_matching_is_unchanged_by_the_defense_branch():
    assert player_match_key("Patrick Mahomes II", "QB") == ("patrick mahomes", "QB")
    # A team hint must not leak into a non-defense key.
    assert player_match_key("Justin Jefferson", "WR", "MIN") == ("justin jefferson", "WR")


def test_short_defense_leftovers_do_not_match_an_arbitrary_team():
    """`normalize_team` falls back to a loose substring match, so a one-character
    leftover would otherwise resolve to whichever team it happens to appear in."""
    assert player_match_key("A D/ST", "DEF") == ("a d st", "DEF")
    assert player_match_key("D/ST", "DEF") == ("d st", "DEF")
    # ...while the shortest real identifier still resolves.
    assert player_match_key("KC D/ST", "DEF") == ("def:KC", "DEF")
