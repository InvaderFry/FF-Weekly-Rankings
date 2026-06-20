from pathlib import Path

import pytest

from ff_startsit import cli
from ff_startsit.config import Settings
from ff_startsit.roster.espn import ESPNProvider
from ff_startsit.roster.manual import ManualProvider
from ff_startsit.roster.sleeper import SleeperProvider


@pytest.fixture(autouse=True)
def _no_network_season(monkeypatch):
    # Avoid the league-agnostic season lookup hitting the network in tests.
    monkeypatch.setattr(cli, "_current_season", lambda settings: "2025")


def _settings(**kw):
    base = dict(
        roster_source="espn",
        espn_league_id="111",
        sleeper_username="me",
        manual_roster_file=Path("manual_roster.csv"),
        data_dir=Path(".cache"),
    )
    base.update(kw)
    return Settings(**base)


def test_factory_defaults_to_espn():
    provider = cli.build_roster_provider(_settings())
    assert isinstance(provider, ESPNProvider)
    assert provider.cache_tag() == "espn_111"


def test_flag_source_overrides_env():
    provider = cli.build_roster_provider(_settings(roster_source="espn"), source="manual")
    assert isinstance(provider, ManualProvider)


def test_sleeper_source_and_league_override():
    provider = cli.build_roster_provider(
        _settings(roster_source="sleeper", sleeper_league_id="999"),
        league="555",
    )
    assert isinstance(provider, SleeperProvider)
    assert provider.league_id == "555"          # --league wins over env
    assert provider.cache_tag() == "sleeper_555"


def test_espn_team_override():
    provider = cli.build_roster_provider(_settings(), team="7")
    assert provider.team_id == "7"
