"""The ESPN league view: all teams, the free-agent pool, and acquisition rules.

The invariant that matters most here is the **key space**: a free agent and a
rostered player must key identically (``espn-{id}``), because the whole waiver
report rests on scoring them together in one candidate set.
"""

import json
from pathlib import Path

from ff_startsit.roster.espn import (ESPNProvider, free_agent_filter,
                                     parse_free_agents, parse_league_rules,
                                     parse_league_teams, parse_roster)
from ff_startsit.waivers.models import ACQ_FAAB, ACQ_PRIORITY, ACQ_UNKNOWN

FIXTURES = Path(__file__).parent / "fixtures"
LEAGUE = json.loads((FIXTURES / "espn_league.json").read_text())
FREE_AGENTS = json.loads((FIXTURES / "espn_free_agents.json").read_text())
PRIORITY = json.loads((FIXTURES / "espn_settings_priority.json").read_text())
MY_SWID = "{AAAA1111-BBBB-2222-CCCC-3333DDDD4444}"


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, *payloads):
        self._payloads = list(payloads)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        payload = self._payloads.pop(0) if len(self._payloads) > 1 else self._payloads[0]
        return _FakeResp(payload)


def _provider(*payloads):
    return ESPNProvider(league_id="123456", season="2025", swid=MY_SWID,
                        espn_s2="cookie", session=_FakeSession(*payloads))


# --- free agents ----------------------------------------------------------
def test_free_agents_key_the_same_way_rostered_players_do():
    """The join key is the whole point: a free agent scored in a different key
    space than your roster could never be compared to it."""
    pool = parse_free_agents(FREE_AGENTS)
    keys = {pp.player.key for pp in pool}
    assert "espn-4426502" in keys
    roster_keys = {p.key for p in parse_roster(LEAGUE, swid=MY_SWID)}
    assert all(k.startswith("espn-") for k in keys | roster_keys)


def test_free_agent_parse_drops_unknown_positions_and_duplicates():
    pool = parse_free_agents(FREE_AGENTS)
    keys = [pp.player.key for pp in pool]
    assert keys.count("espn-4426502") == 1      # the duplicate entry collapses
    assert "espn-555555" not in keys            # IDP has no position code
    assert "espn--16001" in keys                # a D/ST is a real candidate


def test_missing_ownership_is_none_not_zero():
    """0% owned is a claim about the player; absent data is not. A 0 would drag
    the bid heuristic's demand term down as if nobody wanted him."""
    by_key = {pp.player.key: pp for pp in parse_free_agents(FREE_AGENTS)}
    assert by_key["espn-4426502"].percent_owned == 41.7
    assert by_key["espn--16001"].percent_owned is None


def test_injury_status_rides_along_for_the_stash_section():
    by_key = {pp.player.key: pp for pp in parse_free_agents(FREE_AGENTS)}
    assert by_key["espn-4361050"].injury_status == "OUT"


def test_free_agent_request_sends_a_parseable_fantasy_filter():
    """ESPN ignores the filter unless it arrives as a JSON *string* header; a
    dict would be stringified into something it silently discards, and the call
    would quietly return the whole player universe."""
    provider = _provider(FREE_AGENTS)
    provider.get_free_agents(9, limit=25)
    _, kwargs = provider.session.calls[0]
    body = json.loads(kwargs["headers"]["x-fantasy-filter"])
    assert body["players"]["limit"] == 25
    assert body["players"]["filterStatus"]["value"] == ["FREEAGENT", "WAIVERS"]
    assert body["players"]["filterRanksForScoringPeriodIds"]["value"] == [9]
    assert kwargs["params"]["view"] == "kona_player_info"
    assert kwargs["params"]["scoringPeriodId"] == 9


def test_free_agent_filter_sorts_by_ownership():
    """Without the sort, `limit` slices an arbitrary few hundred players out of
    the league's whole database rather than the ones anyone would claim."""
    body = free_agent_filter(3, 150)
    assert body["players"]["sortPercOwned"] == {"sortAsc": False, "sortPriority": 1}


def test_free_agent_fetch_failure_warns_and_returns_empty(capsys):
    class _Boom:
        def get(self, *a, **k):
            raise __import__("requests").RequestException("network down")

    provider = ESPNProvider(league_id="1", season="2025", swid=MY_SWID,
                            espn_s2="c", session=_Boom())
    assert provider.get_free_agents(9) == []
    assert "free-agent list unavailable" in capsys.readouterr().err


# --- league teams ---------------------------------------------------------
def test_league_teams_returns_every_team_with_mine_flagged():
    """The trade half exists because this data was already on the wire and the
    old parse threw it away."""
    teams = parse_league_teams(LEAGUE, swid=MY_SWID)
    assert len(teams) == 2
    mine = [t for t in teams if t.is_mine]
    assert len(mine) == 1 and mine[0].name == "My Team"
    rival = [t for t in teams if not t.is_mine][0]
    assert {p.name for p in rival.players} == {"Josh Allen", "Jonathan Taylor"}


def test_league_teams_carries_faab_spent_and_priority():
    by_name = {t.name: t for t in parse_league_teams(LEAGUE, swid=MY_SWID)}
    assert by_name["My Team"].faab_spent == 35.0
    assert by_name["My Team"].waiver_priority == 7


def test_unidentifiable_team_still_returns_the_league():
    """parse_roster raises here — it has nothing to return. parse_league_teams
    does not: the rest of the report is still worth building."""
    teams = parse_league_teams(LEAGUE, swid="{NOT-MINE}")
    assert len(teams) == 2
    assert not any(t.is_mine for t in teams)


def test_parse_roster_is_unchanged_by_the_refactor():
    """Regression guard: parse_roster was split into parse_team_players +
    _select_team so the league view could reuse it."""
    players = parse_roster(LEAGUE, swid=MY_SWID)
    assert len(players) == 4
    assert {p.name for p in players} == {
        "Patrick Mahomes", "Bijan Robinson", "Justin Jefferson", "Chiefs D/ST"}


# --- rules ----------------------------------------------------------------
def test_faab_league_is_detected_with_its_budget():
    rules = parse_league_rules(LEAGUE)
    assert rules.acquisition_type == ACQ_FAAB
    assert rules.faab_budget == 100.0
    assert rules.roster_slots == {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "DEF": 1, "K": 1}
    assert rules.flex_slots == {"FLEX": 1}      # slot 23, kept out of roster_slots


def test_espn_records_its_superflex_slot_without_inventing_a_position():
    """Slot 7 is ESPN's "OP". Dropped on the floor, the second quarterback it
    starts read as bench depth to every guard that decides drops."""
    payload = {"settings": {"rosterSettings": {"lineupSlotCounts": {
        "0": 1, "2": 2, "4": 2, "6": 1, "16": 1, "17": 1,
        "7": 1, "23": 2, "20": 6,
    }}}}
    rules = parse_league_rules(payload)
    assert rules.flex_slots == {"SUPER_FLEX": 1, "FLEX": 2}
    assert rules.roster_slots["QB"] == 1
    assert "SUPER_FLEX" not in rules.roster_slots and "FLEX" not in rules.roster_slots


def test_priority_league_is_detected_without_a_budget():
    rules = parse_league_rules(PRIORITY)
    assert rules.acquisition_type == ACQ_PRIORITY
    assert rules.faab_budget is None


def test_missing_settings_stay_unknown_rather_than_guessing():
    """A dollar bid printed for a rolling-priority league is advice for somebody
    else's league — worse than no advice."""
    assert parse_league_rules({}).acquisition_type == ACQ_UNKNOWN


def test_budgetless_faab_downgrades_to_priority():
    """"FAAB with no budget" is a contradiction we cannot render a bid for."""
    payload = {"settings": {"acquisitionSettings": {"isUsingAcquisitionBudget": True}}}
    assert parse_league_rules(payload).acquisition_type == ACQ_PRIORITY


def test_league_payload_is_fetched_once_for_roster_teams_and_rules():
    """A waiver run asks for all three; three requests would triple the cost of
    a pass that ESPN already answers in one."""
    provider = _provider(LEAGUE)
    provider.get_roster_players()
    provider.get_league_teams()
    provider.get_league_rules()
    assert len(provider.session.calls) == 1
    assert dict(provider.session.calls[0][1]["params"]).get("view") == "mSettings"
