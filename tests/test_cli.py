import os
from pathlib import Path

import pytest

from ff_startsit import cli
from ff_startsit.config import LeagueProfile, Settings
from ff_startsit.models import Player
from ff_startsit.roster.base import RosterError
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
    # Season and team are part of the cache identity, so a stale-season cache
    # and two teams in one league can no longer collide on one file.
    assert provider.cache_tag() == "espn_2025_111_auto"


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
    assert provider.cache_tag() == "sleeper_555_me"


def test_espn_team_override():
    provider = cli.build_roster_provider(_settings(), team="7")
    assert provider.team_id == "7"


# --- multi-league selection -------------------------------------------------
def _multi_settings(**kw):
    leagues = [
        LeagueProfile("work", "espn", "111", "3"),
        LeagueProfile("dynasty", "espn", "222", "7", scoring="half"),
    ]
    return _settings(leagues=leagues, default_league="work", **kw)


def test_resolve_league_by_name():
    p = cli.resolve_league(_multi_settings(), "dynasty")
    assert p.league_id == "222" and p.scoring == "half"


def test_resolve_league_defaults_to_default_league():
    assert cli.resolve_league(_multi_settings()).name == "work"


def test_resolve_league_unknown_raises():
    with pytest.raises(RosterError):
        cli.resolve_league(_multi_settings(), "nope")


def test_resolve_league_synthesizes_default_when_unconfigured():
    # Settings built directly (no leagues) still resolves to the flat-env default.
    p = cli.resolve_league(_settings(espn_league_id="111"))
    assert p.name == "default" and p.league_id == "111"


def test_build_provider_uses_selected_profile():
    p = cli.resolve_league(_multi_settings(), "dynasty")
    provider = cli.build_roster_provider(_multi_settings(), profile=p)
    assert isinstance(provider, ESPNProvider)
    assert provider.league_id == "222" and provider.team_id == "7"
    assert provider.cache_tag() == "espn_2025_222_7"


def test_explicit_flags_win_over_profile():
    p = cli.resolve_league(_multi_settings(), "dynasty")
    provider = cli.build_roster_provider(_multi_settings(), league="999", team="1", profile=p)
    assert provider.league_id == "999" and provider.team_id == "1"


def test_league_context_applies_per_league_scoring():
    import argparse
    args = argparse.Namespace(source=None, league=None, team=None, league_name="dynasty")
    lsettings, profile = cli._league_context(args, _multi_settings())
    assert profile.name == "dynasty"
    assert lsettings.scoring == "half"       # league scoring overrides global


def test_default_league_label_is_empty():
    # The synthesized single-league default renders without a label (legacy look).
    assert cli._league_label(LeagueProfile("default", "espn", "1", "1")) == ""
    assert cli._league_label(LeagueProfile("work", "espn", "1", "1")) == "work"


def test_publish_does_one_scoring_pass(tmp_path, monkeypatch):
    import argparse

    from ff_startsit import report
    from ff_startsit.models import Player, PlayerScore, Recommendation
    from ff_startsit.output import discord as discord_mod

    players = [Player("1", "Alpha", "KC", "RB")]
    monkeypatch.setattr(cli, "_get_roster", lambda args, settings, profile=None: players)
    monkeypatch.setattr(cli, "_resolve_week", lambda args, settings: 7)

    calls = {"n": 0}

    def fake_rank(settings, plyrs, week, log=False, signals=None):
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
    monkeypatch.setattr(cli, "_get_roster", lambda args, settings, profile=None: players)
    monkeypatch.setattr(cli, "_resolve_week", lambda args, settings: 7)

    def fake_rank(settings, plyrs, week, log=False, signals=None):
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


def test_journalists_disabled_exits_with_hint(capsys):
    import argparse
    args = argparse.Namespace(source=None, league=None, team=None, week=None)
    rc = cli.cmd_journalists(args, _settings())  # preferred_experts unset
    assert rc == 1
    assert "FF_PREFERRED_EXPERTS" in capsys.readouterr().err


def test_journalists_prints_section(monkeypatch, capsys):
    import argparse

    from ff_startsit import report
    from ff_startsit.models import Player
    from ff_startsit.sources.journalists import (Expert, JournalistRow,
                                                 JournalistView)

    players = [Player("1", "Alpha", "KC", "RB")]
    monkeypatch.setattr(cli, "_get_roster", lambda args, settings, profile=None: players)
    monkeypatch.setattr(cli, "_resolve_week", lambda args, settings: 7)
    view = JournalistView(
        experts=[Expert("101", "Justin Boone")],
        by_position={"RB": [JournalistRow(players[0], 2.0, {"101": 2.0})]})
    monkeypatch.setattr(report, "build_journalist_view",
                        lambda settings, plyrs, week: view)

    args = argparse.Namespace(source=None, league=None, team=None, week=None)
    rc = cli.cmd_journalists(args, _settings(preferred_experts="101:Justin Boone"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "## Preferred journalists" in out and "Justin Boone" in out


def test_journalists_no_data_exits_gracefully(monkeypatch, capsys):
    import argparse

    from ff_startsit import report
    from ff_startsit.models import Player

    monkeypatch.setattr(cli, "_get_roster",
                        lambda args, settings, profile=None: [Player("1", "Alpha", "KC", "RB")])
    monkeypatch.setattr(cli, "_resolve_week", lambda args, settings: 7)
    monkeypatch.setattr(report, "build_journalist_view",
                        lambda settings, plyrs, week: None)

    args = argparse.Namespace(source=None, league=None, team=None, week=None)
    rc = cli.cmd_journalists(args, _settings(preferred_experts="101:Justin Boone"))
    assert rc == 1
    assert "No preferred-journalist rankings" in capsys.readouterr().err


def test_publish_includes_journalists_in_both_outputs(tmp_path, monkeypatch):
    import argparse

    from ff_startsit import report
    from ff_startsit.models import Player, PlayerScore, Recommendation
    from ff_startsit.sources.journalists import (Expert, JournalistRow,
                                                 JournalistView)

    players = [Player("1", "Alpha", "KC", "RB")]
    monkeypatch.setattr(cli, "_get_roster", lambda args, settings, profile=None: players)
    monkeypatch.setattr(cli, "_resolve_week", lambda args, settings: 7)

    def fake_rank(settings, plyrs, week, log=False, signals=None):
        ps = PlayerScore(player=players[0])
        ps.final = 90.0
        ps.normalized = {"ecr": 90.0}
        return {"RB": Recommendation(week=week, scoring="ppr",
                                     weights={"ecr": 1.0}, scores=[ps])}

    monkeypatch.setattr(report, "rank_each_position", fake_rank)
    view = JournalistView(
        experts=[Expert("101", "Justin Boone")],
        by_position={"RB": [JournalistRow(players[0], 2.0, {"101": 2.0})]})
    jour_calls = {"n": 0}

    def fake_view(settings, plyrs, week):
        jour_calls["n"] += 1
        return view

    monkeypatch.setattr(report, "build_journalist_view", fake_view)

    report_path = tmp_path / "r.md"
    dash_path = tmp_path / "index.html"
    args = argparse.Namespace(report=report_path, dashboard=dash_path,
                              discord=False, url=None)
    rc = cli.cmd_publish(args, _settings(preferred_experts="101:Justin Boone"))

    assert rc == 0
    assert jour_calls["n"] == 1  # one journalist pass feeds both outputs
    assert "## Preferred journalists" in report_path.read_text()
    assert "Preferred journalists" in dash_path.read_text()


def test_publish_all_leagues_combines_every_league(tmp_path, monkeypatch):
    import argparse

    from ff_startsit import report
    from ff_startsit.models import Player, PlayerScore, Recommendation
    from ff_startsit.output import discord as discord_mod

    rosters = {"111": [Player("1", "AlphaWork", "KC", "RB")],
               "222": [Player("2", "BravoDyno", "BUF", "RB")]}

    def fake_get_roster(args, settings, profile=None):
        return rosters[profile.league_id]

    monkeypatch.setattr(cli, "_get_roster", fake_get_roster)
    monkeypatch.setattr(cli, "_resolve_week", lambda args, settings: 7)

    def fake_rank(settings, plyrs, week, log=False, signals=None):
        ps = PlayerScore(player=plyrs[0])
        ps.final = 90.0
        ps.normalized = {"ecr": 90.0}
        return {"RB": Recommendation(week=week, scoring=settings.scoring,
                                     weights={"ecr": 1.0}, scores=[ps])}

    monkeypatch.setattr(report, "rank_each_position", fake_rank)
    monkeypatch.setattr(report, "build_journalist_view",
                        lambda settings, plyrs, week: None)

    sent = {"payload": None}
    monkeypatch.setattr(discord_mod, "send_discord",
                        lambda url, payload, **kw: sent.__setitem__("payload", payload))

    report_path = tmp_path / "r.md"
    dash_path = tmp_path / "site" / "index.html"
    args = argparse.Namespace(report=report_path, dashboard=dash_path,
                              discord=True, url=None, all_leagues=True)
    settings = _multi_settings(discord_webhook_url="https://discord.test/webhook")

    rc = cli.cmd_publish(args, settings)

    assert rc == 0
    digest = report_path.read_text()
    assert "## work — PPR" in digest and "## dynasty — HALF" in digest
    assert "AlphaWork" in digest and "BravoDyno" in digest
    html = dash_path.read_text()
    assert html.count("<details class='league'") == 2
    # One Discord message, one embed per league.
    assert len(sent["payload"]["embeds"]) == 2


def test_publish_all_leagues_skips_a_failing_league(tmp_path, monkeypatch):
    import argparse

    from ff_startsit import report
    from ff_startsit.models import Player, PlayerScore, Recommendation

    def fake_get_roster(args, settings, profile=None):
        if profile.league_id == "222":
            raise RosterError("cookies expired")
        return [Player("1", "AlphaWork", "KC", "RB")]

    monkeypatch.setattr(cli, "_get_roster", fake_get_roster)
    monkeypatch.setattr(cli, "_resolve_week", lambda args, settings: 7)

    def fake_rank(settings, plyrs, week, log=False, signals=None):
        ps = PlayerScore(player=plyrs[0])
        ps.final = 90.0
        ps.normalized = {"ecr": 90.0}
        return {"RB": Recommendation(week=week, scoring=settings.scoring,
                                     weights={"ecr": 1.0}, scores=[ps])}

    monkeypatch.setattr(report, "rank_each_position", fake_rank)
    monkeypatch.setattr(report, "build_journalist_view",
                        lambda settings, plyrs, week: None)

    report_path = tmp_path / "r.md"
    args = argparse.Namespace(report=report_path, dashboard=None,
                              discord=False, url=None, all_leagues=True)
    rc = cli.cmd_publish(args, _multi_settings())

    assert rc == 0                       # the healthy league still publishes
    digest = report_path.read_text()
    assert "## work — PPR" in digest and "dynasty" not in digest


# --- roster cache freshness ---------------------------------------------

class _StubProvider:
    """A roster provider that can be made to fail on demand."""

    name = "manual"

    def __init__(self, players, fail=False):
        self._players = players
        self.fail = fail
        self.fetches = 0

    def cache_tag(self):
        return "stub"

    def get_roster_players(self):
        self.fetches += 1
        if self.fail:
            raise RosterError("ESPN cookies expired")
        return self._players


def _stub_args(**kw):
    import argparse
    base = dict(source=None, league=None, team=None, league_name=None,
                refresh=False, offline=False)
    base.update(kw)
    return argparse.Namespace(**base)


def _with_stub(monkeypatch, provider):
    monkeypatch.setattr(cli, "build_roster_provider",
                        lambda *a, **kw: provider)


def test_stale_roster_is_used_when_the_fetch_fails(tmp_path, monkeypatch, capsys):
    """A stale cache beats no lineup at all.

    The TTL decides when to prefer a fetch, not when to fail -- expired cookies
    shouldn't turn every command into an error when last night's roster is
    sitting on disk.
    """
    players = [Player(key="1", name="Alpha", team="KC", position="RB")]
    settings = _settings(data_dir=tmp_path, roster_ttl=3600)

    ok = _StubProvider(players)
    _with_stub(monkeypatch, ok)
    cli._get_roster(_stub_args(), settings)          # populate the cache
    path = cli._roster_path(settings, ok)
    os.utime(path, (0, 0))                            # make it ancient

    broken = _StubProvider(players, fail=True)
    _with_stub(monkeypatch, broken)
    got = cli._get_roster(_stub_args(), settings)
    assert [p.key for p in got] == ["1"]
    assert broken.fetches == 1                        # it did try
    assert "falling back to the cached roster" in capsys.readouterr().err


def test_fetch_failure_without_any_cache_still_raises(tmp_path, monkeypatch):
    settings = _settings(data_dir=tmp_path, roster_ttl=3600)
    _with_stub(monkeypatch, _StubProvider([], fail=True))
    with pytest.raises(RosterError):
        cli._get_roster(_stub_args(), settings)


def test_offline_accepts_a_stale_cache(tmp_path, monkeypatch, capsys):
    """--offline means 'do not go to the network', not 'insist on fresh'."""
    players = [Player(key="1", name="Alpha", team="KC", position="RB")]
    settings = _settings(data_dir=tmp_path, roster_ttl=3600)

    provider = _StubProvider(players)
    _with_stub(monkeypatch, provider)
    cli._get_roster(_stub_args(), settings)
    os.utime(cli._roster_path(settings, provider), (0, 0))

    before = provider.fetches
    got = cli._get_roster(_stub_args(offline=True), settings)
    assert [p.key for p in got] == ["1"]
    assert provider.fetches == before                 # never went to the network
    assert "stale cached roster" in capsys.readouterr().err


def test_refresh_and_offline_together_is_an_error(tmp_path, monkeypatch):
    settings = _settings(data_dir=tmp_path)
    _with_stub(monkeypatch, _StubProvider([]))
    with pytest.raises(RosterError):
        cli._get_roster(_stub_args(refresh=True, offline=True), settings)
