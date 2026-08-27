"""Sleeper's league view: rosters, the derived free-agent pool, and rules.

Sleeper has no free-agent endpoint at all — the pool is the whole player
universe minus everyone rostered. That subtraction, and the ordering that turns
11,000 players into a claimable shortlist, is what these pin.
"""

import json
from pathlib import Path

from ff_startsit.roster.sleeper import (build_free_agents, build_league_teams,
                                        parse_league_rules)
from ff_startsit.waivers.models import ACQ_FAAB, ACQ_PRIORITY, ACQ_UNKNOWN

FIXTURES = Path(__file__).parent / "fixtures"
META = json.loads((FIXTURES / "sleeper_pool_players.json").read_text())
ROSTERS = json.loads((FIXTURES / "sleeper_league_rosters.json").read_text())
LEAGUE = json.loads((FIXTURES / "sleeper_league.json").read_text())


def test_free_agents_exclude_everyone_already_rostered():
    keys = {pp.player.key for pp in build_free_agents(ROSTERS, META)}
    assert "100" not in keys and "200" not in keys   # roster 1
    assert "KC" not in keys                          # roster 2
    assert {"101", "102", "300"} <= keys


def test_free_agents_drop_inactive_and_non_fantasy_positions():
    keys = {pp.player.key for pp in build_free_agents(ROSTERS, META)}
    assert "103" not in keys   # active: False
    assert "999" not in keys   # position OL


def test_free_agents_are_ordered_by_search_rank():
    """`limit` only produces a claimable shortlist if the ordering is the
    platform's own relevance one — otherwise it slices an arbitrary few."""
    pool = build_free_agents(ROSTERS, META, limit=2)
    assert [pp.player.key for pp in pool] == ["101", "102"]


def test_unranked_players_sort_last_not_first():
    """A missing search_rank defaulting to 0 would have put every anonymous
    third-stringer at the top of the wire."""
    pool = build_free_agents(ROSTERS, META)
    assert [pp.player.key for pp in pool][-1] == "300"


def test_trending_adds_are_attached_when_available():
    pool = build_free_agents(ROSTERS, META, trending={"101": 14231})
    by_key = {pp.player.key: pp for pp in pool}
    assert by_key["101"].trending_adds == 14231
    assert by_key["102"].trending_adds is None


def test_injury_status_rides_along():
    by_key = {pp.player.key: pp for pp in build_free_agents(ROSTERS, META)}
    assert by_key["101"].injury_status == "IR"


def test_league_teams_flag_mine_and_carry_budget():
    teams = build_league_teams(ROSTERS, {"u1": "My Squad", "u2": "Rival"}, META, "u1")
    by_name = {t.name: t for t in teams}
    assert by_name["My Squad"].is_mine and not by_name["Rival"].is_mine
    assert by_name["My Squad"].faab_spent == 30.0
    assert by_name["My Squad"].waiver_priority == 4
    assert {p.name for p in by_name["My Squad"].players} == {"Patrick Runner", "Some Kicker"}


def test_waiver_type_two_is_faab():
    rules = parse_league_rules(LEAGUE)
    assert rules.acquisition_type == ACQ_FAAB
    assert rules.faab_budget == 100.0
    assert rules.roster_slots == {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "K": 1, "DEF": 1}
    # The flex slot is recorded, and kept out of roster_slots: nobody plays "FLEX"
    # and it has no starter demand of its own to divide a rank by.
    assert rules.flex_slots == {"FLEX": 1}


def test_a_superflex_slot_is_recorded_rather_than_discarded():
    """It decides who starts, which is what drop protection turns on. Dropped, a
    superflex league's second quarterback was surplus to the waiver pass's count
    and unprotected by the lineup builder at once."""
    league = {"settings": {"waiver_type": 2, "waiver_budget": 100},
              "roster_positions": ["QB", "SUPER_FLEX", "RB", "RB", "WR", "WR",
                                   "TE", "FLEX", "FLEX", "K", "DEF", "BN"]}
    rules = parse_league_rules(league)
    assert rules.flex_slots == {"SUPER_FLEX": 1, "FLEX": 2}
    assert "SUPER_FLEX" not in rules.roster_slots
    assert rules.roster_slots["QB"] == 1     # the superflex is not a second QB slot


def test_rolling_waiver_type_is_priority():
    assert parse_league_rules(
        {"settings": {"waiver_type": 0}}).acquisition_type == ACQ_PRIORITY


def test_no_settings_stays_unknown():
    assert parse_league_rules({}).acquisition_type == ACQ_UNKNOWN
