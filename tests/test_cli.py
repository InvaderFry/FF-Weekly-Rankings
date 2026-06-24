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


def test_publish_does_one_scoring_pass(tmp_path, monkeypatch):
    import argparse

    from ff_startsit import report
    from ff_startsit.models import Player, PlayerScore, Recommendation
    from ff_startsit.output import discord as discord_mod

    players = [Player("1", "Alpha", "KC", "RB")]
    monkeypatch.setattr(cli, "_get_roster", lambda args, settings: players)
    monkeypatch.setattr(cli, "_resolve_week", lambda args, settings: 7)

    calls = {"n": 0}

    def fake_rank(settings, plyrs, week, log=False):
        calls["n"] += 1
        ps = PlayerScore(player=players[0])
        ps.final = 90.0
        ps.normalized = {"ecr": 90.0}
        return {"RB": Recommendation(week=week, scoring="ppr",
                                     weights={"ecr": 1.0}, scores=[ps])}

    monkeypatch.setattr(report, "rank_each_position", fake_rank)

    sent = {"n": 0}
    monkeypatch.setattr(discord_mod, "send_discord",
                        lambda url, payload, **kw: sent.__setitem__("n", sent["n"] + 1))

    report_path = tmp_path / "r.md"
    dash_path = tmp_path / "site" / "index.html"
    args = argparse.Namespace(report=report_path, dashboard=dash_path,
                              discord=True, url="https://example.test/site/")
    settings = _settings(discord_webhook_url="https://discord.test/webhook")

    rc = cli.cmd_publish(args, settings)

    assert rc == 0
    assert calls["n"] == 1                       # one pass feeds all three outputs
    assert sent["n"] == 1                        # Discord sent exactly once
    assert report_path.exists() and dash_path.exists()
    assert "Alpha" in report_path.read_text()
    assert "<!doctype html>" in dash_path.read_text()


def test_publish_survives_discord_failure(tmp_path, monkeypatch):
    import argparse

    from ff_startsit import report
    from ff_startsit.models import Player, PlayerScore, Recommendation
    from ff_startsit.output import discord as discord_mod

    players = [Player("1", "Alpha", "KC", "RB")]
    monkeypatch.setattr(cli, "_get_roster", lambda args, settings: players)
    monkeypatch.setattr(cli, "_resolve_week", lambda args, settings: 7)

    def fake_rank(settings, plyrs, week, log=False):
        ps = PlayerScore(player=players[0])
        ps.final = 90.0
        ps.normalized = {"ecr": 90.0}
        return {"RB": Recommendation(week=week, scoring="ppr",
                                     weights={"ecr": 1.0}, scores=[ps])}

    monkeypatch.setattr(report, "rank_each_position", fake_rank)

    def boom(url, payload, **kw):
        raise RuntimeError("webhook 404")

    monkeypatch.setattr(discord_mod, "send_discord", boom)

    dash_path = tmp_path / "index.html"
    args = argparse.Namespace(report=None, dashboard=dash_path, discord=True, url=None)
    settings = _settings(discord_webhook_url="https://discord.test/webhook")

    # A Discord failure is swallowed: the command still succeeds and the
    # dashboard the rest of the workflow depends on is still written.
    rc = cli.cmd_publish(args, settings)
    assert rc == 0
    assert dash_path.exists()
