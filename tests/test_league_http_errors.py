"""One league's HTTP failure must cost that league, never the whole run.

``ESPNProvider._fetch`` turned connection errors into ``RosterError`` but let an
HTTP 5xx leave as ``requests.HTTPError`` (and a non-JSON body as a decode
error). The per-league loops in ``_league_bundles`` and ``_waiver_bundles``
caught only ``RosterError``/``SleeperError``, so the first league's 502 ended a
multi-league run before the second league was read.
"""
from types import SimpleNamespace

import pytest
import requests

from ff_startsit import cli, report
from ff_startsit.config import LeagueProfile, Settings
from ff_startsit.models import Player, SignalValue
from ff_startsit.roster.base import RosterError, RosterProvider
from ff_startsit.roster.espn import ESPNProvider
from ff_startsit.roster.sleeper import SleeperClient, SleeperError
from ff_startsit.sources.base import Signal
from ff_startsit.waivers.base import LeagueViewProvider


class _Resp:
    def __init__(self, status: int, body=None, text: str = ""):
        self.status_code = status
        self._body = body
        self.text = text
        self.reason = "Bad Gateway" if status == 502 else ""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Server Error", response=self)

    def json(self):
        if self._body is None:
            raise requests.exceptions.JSONDecodeError("Expecting value", self.text, 0)
        return self._body


class _Session:
    def __init__(self, resp):
        self.resp = resp
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        return self.resp


def _players():
    return [Player(str(i), f"Player {i}", "KC", "WR") for i in range(3)]


class _Ranks(Signal):
    name = "ecr"
    higher_is_better = False

    def is_available(self):
        return True

    def fetch(self, week, players):
        return {p.key: SignalValue(float(p.key) + 1) for p in players}


class _GoodProvider(RosterProvider, LeagueViewProvider):
    name = "espn"

    def cache_tag(self):
        return "good"

    def get_roster_players(self):
        return _players()

    def get_league_teams(self):
        return []

    def get_free_agents(self, week, limit=150):
        return []


def _broken_espn():
    return ESPNProvider(league_id="111", season="2026", team_id="1",
                        session=_Session(_Resp(502, text="<html>bad gateway</html>")))


def _settings(tmp_path):
    return Settings(data_dir=tmp_path, leagues=[
        LeagueProfile(name="Broken", source="espn", league_id="111", team_id="1"),
        LeagueProfile(name="Fine", source="espn", league_id="222", team_id="2")])


def _route_providers(monkeypatch):
    def build(settings, source=None, league=None, team=None, profile=None):
        return _broken_espn() if profile.name == "Broken" else _GoodProvider()
    monkeypatch.setattr(cli, "build_roster_provider", build)


# --- the providers -----------------------------------------------------------
def test_espn_http_5xx_is_a_roster_error_naming_the_status():
    with pytest.raises(RosterError, match="502"):
        _broken_espn().get_roster_players()


def test_espn_non_json_body_is_a_roster_error():
    provider = ESPNProvider(league_id="111", season="2026", team_id="1",
                            session=_Session(_Resp(200, body=None, text="<html>")))
    with pytest.raises(RosterError, match="not JSON"):
        provider.get_roster_players()


def test_sleeper_http_5xx_is_a_sleeper_error_naming_the_status(tmp_path):
    client = SleeperClient(tmp_path, session=_Session(_Resp(502)))
    with pytest.raises(SleeperError, match="502"):
        client.current_season()


def test_sleeper_non_json_body_is_a_sleeper_error(tmp_path):
    client = SleeperClient(tmp_path, session=_Session(_Resp(200, body=None)))
    with pytest.raises(SleeperError, match="not JSON"):
        client.current_season()


def test_sleeper_trending_failure_still_only_costs_the_colour(tmp_path):
    """``trending_adds`` is colour; wrapping the status error must not make it
    fatal to the free-agent pool, whose guard caught only RequestException."""
    from ff_startsit.roster.sleeper import SleeperProvider

    provider = SleeperProvider(username="u", league_id="L", data_dir=tmp_path)

    class _Client:
        def all_rosters(self, league_id):
            return []

        def load_player_metadata(self):
            return {}

        def trending_adds(self):
            raise SleeperError("Sleeper request failed: HTTP 502")

    provider.client = _Client()
    provider._resolved = ("me", "L")
    assert provider.get_free_agents(1) == []


# --- the per-league loops ----------------------------------------------------
def test_start_sit_skips_only_the_league_whose_espn_returned_502(monkeypatch, tmp_path):
    _route_providers(monkeypatch)
    monkeypatch.setattr(report, "build_signals", lambda settings: [_Ranks()])
    monkeypatch.setattr(report, "build_journalist_view", lambda *a: None)

    bundles = cli._league_bundles(SimpleNamespace(log=False, source=None,
                                                  league=None, team=None),
                                  _settings(tmp_path), 1)

    assert [b.label for b in bundles] == ["Fine"]
    status = bundles[0].data_status
    assert list(status.skipped) == ["Broken"]
    assert "502" in status.skipped["Broken"]


def test_waivers_skip_only_the_league_whose_espn_returned_502(monkeypatch, tmp_path):
    _route_providers(monkeypatch)
    import ff_startsit.waivers.build as build_mod

    def fake_build_bundle(settings, label, provider, players, week, **kwargs):
        return SimpleNamespace(label=label, recs={})
    monkeypatch.setattr(build_mod, "build_bundle", fake_build_bundle)

    args = SimpleNamespace(all_leagues=True, source=None, league=None, team=None)
    bundles = cli._waiver_bundles(args, _settings(tmp_path), 1)

    assert [b.label for b in bundles] == ["Fine"]
    status = bundles[0].data_status
    assert list(status.skipped) == ["Broken"]
    assert "502" in status.skipped["Broken"]


def test_an_unwrapped_request_exception_still_costs_one_league(monkeypatch, tmp_path):
    """A provider that forgets to wrap must still cost one league, not the run."""
    class _Leaky(_GoodProvider):
        def get_roster_players(self):
            raise requests.ConnectionError("connection reset")

    def build(settings, source=None, league=None, team=None, profile=None):
        return _Leaky() if profile.name == "Broken" else _GoodProvider()
    monkeypatch.setattr(cli, "build_roster_provider", build)
    monkeypatch.setattr(cli, "_read_roster_cache", lambda path: None)
    monkeypatch.setattr(report, "build_signals", lambda settings: [_Ranks()])
    monkeypatch.setattr(report, "build_journalist_view", lambda *a: None)

    bundles = cli._league_bundles(SimpleNamespace(log=False, source=None,
                                                  league=None, team=None),
                                  _settings(tmp_path), 1)
    assert [b.label for b in bundles] == ["Fine"]
    assert "connection reset" in bundles[0].data_status.skipped["Broken"]
