import json
from pathlib import Path

import pytest

from ff_startsit.roster.base import RosterError
from ff_startsit.roster.espn import parse_roster

FIXTURES = Path(__file__).parent / "fixtures"
PAYLOAD = json.loads((FIXTURES / "espn_league.json").read_text())
MY_SWID = "{AAAA1111-BBBB-2222-CCCC-3333DDDD4444}"


def test_parse_roster_by_swid_maps_fields():
    players = parse_roster(PAYLOAD, swid=MY_SWID)
    by_key = {p.key: p for p in players}

    # IDP (defaultPositionId 9) is dropped; the other four are kept.
    assert len(players) == 4
    mahomes = by_key["espn-3139477"]
    assert mahomes.name == "Patrick Mahomes"
    assert mahomes.position == "QB"
    assert mahomes.team == "KC"
    assert by_key["espn-4047365"].team == "MIN"   # proTeamId 16 -> MIN
    assert by_key["espn--16012"].position == "DEF"  # D/ST handling


def test_swid_match_is_brace_and_case_insensitive():
    players = parse_roster(PAYLOAD, swid="aaaa1111-bbbb-2222-cccc-3333dddd4444")
    assert any(p.name == "Patrick Mahomes" for p in players)


def test_parse_roster_by_team_id():
    players = parse_roster(PAYLOAD, team_id="2")
    names = {p.name for p in players}
    assert names == {"Josh Allen", "Jonathan Taylor"}


def test_unknown_team_id_raises():
    with pytest.raises(RosterError):
        parse_roster(PAYLOAD, team_id="99")


def test_public_league_without_team_or_swid_raises():
    with pytest.raises(RosterError):
        parse_roster(PAYLOAD)


def test_swid_with_no_match_raises():
    with pytest.raises(RosterError):
        parse_roster(PAYLOAD, swid="{not-an-owner}")


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload, status=200):
        self._payload, self._status = payload, status
        self.last_kwargs = None

    def get(self, url, **kwargs):
        self.last_kwargs = kwargs
        return _FakeResp(self._payload, self._status)


def test_provider_fetches_and_sends_cookies():
    from ff_startsit.roster.espn import ESPNProvider

    session = _FakeSession(PAYLOAD)
    provider = ESPNProvider(league_id="111", season="2025", espn_s2="s2val",
                            swid=MY_SWID, session=session)
    players = provider.get_roster_players()
    assert any(p.name == "Patrick Mahomes" for p in players)        # auto-detected my team
    assert session.last_kwargs["cookies"] == {"espn_s2": "s2val", "SWID": MY_SWID}
    assert provider.cache_tag() == "espn_111"


def test_provider_raises_on_auth_denied():
    from ff_startsit.roster.espn import ESPNProvider

    provider = ESPNProvider(league_id="111", season="2025",
                            session=_FakeSession({}, status=401))
    with pytest.raises(RosterError):
        provider.get_roster_players()
