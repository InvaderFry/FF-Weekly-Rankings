"""`SleeperProvider` — the wiring around Sleeper's pure parsers.

Every other Sleeper test exercises a pure function (`build_players`,
`build_free_agents`, `build_league_teams`, `parse_league_rules`); the provider
that calls them was never instantiated. What lives only here is the request
budget and the degradation, and ESPN has had the equivalent test
(`test_league_payload_is_fetched_once_for_roster_teams_and_rules`) all along.

The budget is the point. `_resolve` costs three round trips (season, user, league)
and a waiver pass asks the provider for teams, rules *and* free agents — so
without the memo one pass pays for all of it three times, per league, on a
schedule. That is the same class of quiet, recurring cost as the odds-cache
duplication, and equally invisible in the output.
"""

import json
from pathlib import Path

import pytest
import requests

from ff_startsit.roster.base import RosterError
from ff_startsit.roster.sleeper import SleeperClient, SleeperError, SleeperProvider
from ff_startsit.waivers.models import ACQ_FAAB, ACQ_UNKNOWN

FIXTURES = Path(__file__).parent / "fixtures"
META = json.loads((FIXTURES / "sleeper_pool_players.json").read_text())
ROSTERS = json.loads((FIXTURES / "sleeper_league_rosters.json").read_text())
LEAGUE = json.loads((FIXTURES / "sleeper_league.json").read_text())

USERS = [{"user_id": "u1", "display_name": "me", "metadata": {"team_name": "My Squad"}},
         {"user_id": "u2", "display_name": "rival"}]
TRENDING = [{"player_id": "101", "count": 812}]


class _FakeClient(SleeperClient):
    """A client whose only network edge — ``_get`` — is a routing table that
    records every path, so a repeated call is visible."""

    def __init__(self, data_dir, leagues=None, fail=()):
        super().__init__(data_dir=data_dir)
        self.paths: list[str] = []
        self.fail = set(fail)
        self.leagues = [{"league_id": "999"}] if leagues is None else leagues

    def _get(self, path):
        self.paths.append(path)
        for prefix in self.fail:
            if path.startswith(prefix):
                raise SleeperError(f"boom: {path}")
        if path == "/state/nfl":
            return {"season": "2025", "season_type": "regular", "week": 5}
        if path == "/user/me":
            return {"user_id": "u1"}
        if path.startswith("/user/u1/leagues"):
            return self.leagues
        if path == "/league/999/rosters":
            return ROSTERS
        if path == "/league/999/users":
            return USERS
        if path == "/league/999":
            return LEAGUE
        if path == "/players/nfl":
            return META
        if path.startswith("/players/nfl/trending/add"):
            return TRENDING
        raise AssertionError(f"unexpected request: {path}")

    def count(self, path) -> int:
        """Exact matches only — ``/players/nfl`` is a different endpoint from
        ``/players/nfl/trending/add``, and a prefix match conflates them."""
        return self.paths.count(path)

    def count_prefix(self, prefix) -> int:
        return sum(1 for p in self.paths if p.startswith(prefix))


def _provider(tmp_path, client=None, league_id="999", **kw):
    client = client or _FakeClient(tmp_path, **kw)
    return SleeperProvider(username="me", league_id=league_id,
                           data_dir=tmp_path, client=client)


# --- the request budget ---------------------------------------------------
def test_one_waiver_pass_resolves_the_league_once(tmp_path):
    """`_resolve` is three round trips. A waiver pass asks for all three views,
    so an unmemoized resolve triples every one of them, per league, per run."""
    provider = _provider(tmp_path)
    provider.get_league_teams()
    provider.get_league_rules()
    provider.get_free_agents(week=5)

    client = provider.client
    assert client.count("/state/nfl") == 1
    assert client.count("/user/me") == 1
    assert client.count_prefix("/user/u1/leagues") == 1


def test_the_rosters_blob_is_fetched_once_for_teams_and_free_agents(tmp_path):
    """Sleeper has no free-agent endpoint — the pool is derived by subtracting
    the same roster blob `get_league_teams` already read."""
    provider = _provider(tmp_path)
    provider.get_league_teams()
    provider.get_free_agents(week=5)

    assert provider.client.count("/league/999/rosters") == 1


def test_the_player_universe_is_fetched_once_across_the_pass(tmp_path):
    """The metadata blob is ~5 MB behind a 24 h disk cache; the provider must go
    through it rather than around it."""
    provider = _provider(tmp_path)
    provider.get_league_teams()
    provider.get_free_agents(week=5)
    provider.get_roster_players()

    assert provider.client.count("/players/nfl") == 1


# --- the views themselves -------------------------------------------------
def test_the_provider_delivers_the_league_view_end_to_end(tmp_path):
    provider = _provider(tmp_path)

    teams = provider.get_league_teams()
    assert len(teams) == 2
    mine = [t for t in teams if t.is_mine]
    assert [t.name for t in mine] == ["My Squad"]      # metadata team_name wins
    assert mine[0].faab_spent == 30.0

    rules = provider.get_league_rules()
    assert rules.acquisition_type == ACQ_FAAB and rules.faab_budget == 100.0

    pool = provider.get_free_agents(week=5)
    keys = {pp.player.key for pp in pool}
    assert "100" not in keys                          # rostered
    assert {"101", "102"} <= keys
    by_key = {pp.player.key: pp for pp in pool}
    assert by_key["101"].trending_adds == 812         # the trending join lands


def test_the_roster_comes_back_through_the_provider(tmp_path):
    players = _provider(tmp_path).get_roster_players()
    assert {p.name for p in players} == {"Patrick Runner", "Some Kicker"}


def test_an_explicit_league_id_is_trusted_even_when_the_listing_omits_it(tmp_path):
    """A league the listing doesn't return (an old season, a partial response) is
    still the league the user named — guessing a different one silently reports
    on somebody else's team."""
    client = _FakeClient(tmp_path, leagues=[{"league_id": "111"}])
    provider = _provider(tmp_path, client=client, league_id="999")
    assert provider.get_league_rules().acquisition_type == ACQ_FAAB
    assert client.count("/league/999") == 1


def test_a_co_owner_is_recognized_as_the_owner(tmp_path):
    """`_owns` checks `co_owners` as well as `owner_id`. Missed, a co-managed
    team reads as somebody else's and the run has no roster at all."""
    rosters = [{"roster_id": 1, "owner_id": "someone-else", "co_owners": ["u1"],
                "players": ["100", "200"], "settings": {}}]

    class _CoOwned(_FakeClient):
        def _get(self, path):
            if path == "/league/999/rosters":
                self.paths.append(path)
                return rosters
            return super()._get(path)

    provider = _provider(tmp_path, client=_CoOwned(tmp_path))
    assert {p.name for p in provider.get_roster_players()} == {"Patrick Runner",
                                                               "Some Kicker"}
    assert [t.is_mine for t in provider.get_league_teams()] == [True]


def test_a_username_with_no_roster_in_the_league_is_an_error_not_an_empty_team(tmp_path):
    """An empty roster would read as "you have nobody" and produce a confident,
    empty report; the raised error stops the run instead."""
    rosters = [{"roster_id": 2, "owner_id": "u2", "players": ["KC"], "settings": {}}]

    class _NotMine(_FakeClient):
        def _get(self, path):
            if path == "/league/999/rosters":
                self.paths.append(path)
                return rosters
            return super()._get(path)

    with pytest.raises(SleeperError):
        _provider(tmp_path, client=_NotMine(tmp_path)).get_roster_players()


def test_a_missing_username_is_refused_at_construction(tmp_path):
    with pytest.raises(RosterError):
        SleeperProvider(username="", league_id="999", data_dir=tmp_path)


def test_the_cache_tag_separates_two_teams_in_one_league(tmp_path):
    assert _provider(tmp_path).cache_tag() == "sleeper_999_me"


# --- degradation ----------------------------------------------------------
def test_an_unreachable_league_costs_the_teams_and_nothing_else(tmp_path, capsys):
    """The waiver pass warns and continues on a missing league view — trades go,
    the rest of the report stands."""
    provider = _provider(tmp_path, fail=("/league/999/users",))
    assert provider.get_league_teams() == []
    assert "warning:" in capsys.readouterr().err


def test_unreadable_settings_fall_back_to_default_rules(tmp_path, capsys):
    """Not knowing the waiver type must not raise: the bid section degrades to
    priority-style advice rather than taking the run down."""
    provider = _provider(tmp_path, fail=("/league/999",))
    rules = provider.get_league_rules()
    assert rules.acquisition_type == ACQ_UNKNOWN
    assert rules.faab_budget is None
    assert "warning:" in capsys.readouterr().err


def test_an_unreachable_pool_is_empty_rather_than_fatal(tmp_path, capsys):
    provider = _provider(tmp_path, fail=("/league/999/rosters",))
    assert provider.get_free_agents(week=5) == []
    assert "warning:" in capsys.readouterr().err


def test_a_network_error_degrades_the_same_way_a_sleeper_error_does(tmp_path, capsys):
    """Both arms of the `except` are load-bearing: requests raises its own
    exceptions, and only `SleeperError` being caught would let a flaky
    connection escape as a traceback."""
    class _Flaky(_FakeClient):
        def _get(self, path):
            self.paths.append(path)
            raise requests.ConnectionError("no route to host")

    provider = _provider(tmp_path, client=_Flaky(tmp_path))
    assert provider.get_league_teams() == []
    assert provider.get_free_agents(week=5) == []
    assert provider.get_league_rules().acquisition_type == ACQ_UNKNOWN
    assert "warning:" in capsys.readouterr().err


def test_a_failed_trending_lookup_costs_colour_not_the_pool(tmp_path):
    """Trending adds are decoration. Failing the pool for them would trade the
    whole waiver section for a nice-to-have number."""
    class _NoTrending(_FakeClient):
        def _get(self, path):
            if path.startswith("/players/nfl/trending"):
                self.paths.append(path)
                raise requests.Timeout("slow")
            return super()._get(path)

    pool = _provider(tmp_path, client=_NoTrending(tmp_path)).get_free_agents(week=5)
    assert {pp.player.key for pp in pool}
    assert all(pp.trending_adds is None for pp in pool)
