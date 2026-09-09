import json
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

    def fake_rank(settings, plyrs, week, log=False, signals=None, slots=None):
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

    def fake_rank(settings, plyrs, week, log=False, signals=None, slots=None):
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

    def fake_rank(settings, plyrs, week, log=False, signals=None, slots=None):
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

    def fake_rank(settings, plyrs, week, log=False, signals=None, slots=None):
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

    def fake_rank(settings, plyrs, week, log=False, signals=None, slots=None):
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
    assert "## work — PPR" in digest and "## dynasty —" not in digest
    assert "Skipped dynasty: cookies expired" in digest
    assert "1 of 2 included — INCOMPLETE" in digest


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


def _mixed_roster():
    return [
        Player("1", "Runner One", "KC", "RB"),
        Player("2", "Catcher Two", "SF", "WR"),
        Player("3", "Passer Three", "BUF", "QB"),
    ]


def _compare_args(*names, md=False):
    import argparse
    return argparse.Namespace(players=list(names), md=md, source=None, league=None,
                              team=None, week=None, league_name=None)


def test_compare_refuses_positions_that_cannot_be_pooled(monkeypatch, capsys):
    """A QB rank of 1 and an RB rank of 1 come from different populations.

    `normalize.to_0_100` is min-max within the candidate set, so both would land
    on 100 and the blend would answer with a number that does not mean what it
    says. Refuse rather than mislead.
    """
    monkeypatch.setattr(cli, "_get_roster", lambda args, settings, profile=None: _mixed_roster())
    monkeypatch.setattr(cli, "_resolve_week", lambda args, settings: 7)

    rc = cli.cmd_compare(_compare_args("Runner One", "Passer Three"), _settings())
    assert rc == 1
    err = capsys.readouterr().err
    assert "QB/RB" in err
    assert "would not be comparable" in err


def test_compare_uses_the_pooled_flex_basis_for_mixed_flex_players(monkeypatch, capsys):
    """RB vs WR is the real 'who do I flex' question — pool it, don't refuse it."""
    from ff_startsit import report
    from ff_startsit.models import PlayerScore, Recommendation

    monkeypatch.setattr(cli, "_get_roster", lambda args, settings, profile=None: _mixed_roster())
    monkeypatch.setattr(cli, "_resolve_week", lambda args, settings: 7)

    seen = {}

    def fake_pooled(settings, cands, week, signals, command):
        seen["positions"] = sorted(p.position for p in cands)
        seen["command"] = command
        ps = PlayerScore(player=cands[0])
        ps.final, ps.normalized = 91.0, {"ecr": 91.0}
        return Recommendation(week=week, scoring="ppr", weights={"ecr": 1.0},
                              scores=[ps]), None

    monkeypatch.setattr(report, "rank_pooled", fake_pooled)

    rc = cli.cmd_compare(_compare_args("Runner One", "Catcher Two"), _settings())
    assert rc == 0
    assert seen["positions"] == ["RB", "WR"]
    assert seen["command"] == "compare:pooled"
    # The reader is told the score is on the pooled basis, as the FLEX slot does.
    assert "cross-position RB/WR/TE ranking" in capsys.readouterr().err


def test_compare_refuses_when_the_pooled_ranking_is_untrustworthy(monkeypatch, capsys):
    """Falling back to the per-position blend here would be the invalid answer."""
    from ff_startsit import report

    monkeypatch.setattr(cli, "_get_roster", lambda args, settings, profile=None: _mixed_roster())
    monkeypatch.setattr(cli, "_resolve_week", lambda args, settings: 7)
    monkeypatch.setattr(report, "rank_pooled",
                        lambda *a, **k: (None, "FantasyPros FLEX ranking returned too few matches"))

    rc = cli.cmd_compare(_compare_args("Runner One", "Catcher Two"), _settings())
    assert rc == 1
    assert "too few matches" in capsys.readouterr().err


def test_compare_within_one_position_is_unchanged(monkeypatch):
    """Same-position compare must keep logging through the normal path."""
    from ff_startsit.models import PlayerScore, Recommendation

    players = [Player("1", "Runner One", "KC", "RB"), Player("2", "Runner Two", "SF", "RB")]
    monkeypatch.setattr(cli, "_get_roster", lambda args, settings, profile=None: players)
    monkeypatch.setattr(cli, "_resolve_week", lambda args, settings: 7)

    seen = {}

    def fake_recommend(settings, cands, week, **kw):
        seen.update(kw)
        ps = PlayerScore(player=cands[0])
        ps.final, ps.normalized = 90.0, {"ecr": 90.0}
        return Recommendation(week=week, scoring="ppr", weights={"ecr": 1.0}, scores=[ps])

    monkeypatch.setattr(cli, "recommend", fake_recommend)

    rc = cli.cmd_compare(_compare_args("Runner One", "Runner Two"), _settings())
    assert rc == 0
    assert seen["command"] == "compare"
    assert "signals" not in seen          # per-position ECR, not the pooled sibling


def test_notify_warns_instead_of_raising_on_discord_failure(tmp_path, monkeypatch, capsys):
    """CLAUDE.md: `publish`/`notify` warn-and-continue on Discord failure."""
    import argparse

    from ff_startsit import report
    from ff_startsit.models import PlayerScore, Recommendation
    from ff_startsit.output import discord as discord_mod

    players = [Player("1", "Alpha", "KC", "RB")]
    monkeypatch.setattr(cli, "_get_roster", lambda args, settings, profile=None: players)
    monkeypatch.setattr(cli, "_resolve_week", lambda args, settings: 7)

    def fake_rank(settings, plyrs, week, log=False, signals=None, slots=None):
        ps = PlayerScore(player=players[0])
        ps.final, ps.normalized = 90.0, {"ecr": 90.0}
        return {"RB": Recommendation(week=week, scoring="ppr",
                                     weights={"ecr": 1.0}, scores=[ps])}

    monkeypatch.setattr(report, "rank_each_position", fake_rank)
    monkeypatch.setattr(discord_mod, "send_discord",
                        lambda url, payload, **kw: (_ for _ in ()).throw(RuntimeError("webhook 404")))

    args = argparse.Namespace(source=None, league=None, team=None, week=None,
                              league_name=None, url=None)
    rc = cli.cmd_notify(args, _settings(discord_webhook_url="https://discord.test/webhook"))
    assert rc == 1
    assert "Discord notification failed" in capsys.readouterr().err


def test_network_failure_is_reported_not_raised(monkeypatch, capsys):
    """`sync` bypasses `_get_roster`, so nothing else catches its transport errors."""
    import requests

    def boom(args, settings):
        raise requests.ConnectionError("dns is down")

    monkeypatch.setattr(cli, "cmd_sync", boom)
    monkeypatch.setattr(cli, "load_settings", lambda: _settings())

    rc = cli.main(["sync"])
    assert rc == 2
    assert "network request failed" in capsys.readouterr().err


# --- rank: the flagship command, end to end --------------------------------
# `rank` is the one user-facing command with no CLI-level test — `publish`,
# `compare`, `notify`, `journalists` and the waiver commands all have one. The
# pipeline underneath is well covered, so what is pinned here is the wiring:
# the DST/DEF fold, the empty-position exit, and the export flags.

class _RankSignal:
    """A signal serving canned ranks, injected in place of the live set."""

    name = "ecr"
    higher_is_better = False
    is_sample = False
    served_wrong_week = False

    def __init__(self, ranks):
        self.ranks = ranks

    def is_available(self):
        return True

    def fetch(self, week, players):
        from ff_startsit.models import SignalValue
        return {p.key: SignalValue(self.ranks[p.key]) if p.key in self.ranks
                else SignalValue(None, available=False, note="no rank")
                for p in players}


def _rank_roster():
    return [
        Player("1", "Runner One", "KC", "RB"),
        Player("2", "Runner Two", "SF", "RB"),
        Player("3", "Passer Three", "BUF", "QB"),
        Player("KC", "Kansas City", "KC", "DEF"),
    ]


def _rank_args(pos="RB", **kw):
    import argparse
    base = dict(pos=pos, week=5, md=False, csv=None, json=None, source=None,
                league=None, team=None, league_name=None)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def _ranked(monkeypatch, tmp_path):
    """Stub the roster and the live signal set; everything between stays real."""
    from ff_startsit import pipeline

    # The banner is date-driven and covered by test_preseason.py; silencing it
    # keeps these assertions about `rank`'s own output in every calendar month.
    monkeypatch.setattr(cli, "_print_preseason_banner", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "_get_roster",
                        lambda args, settings, profile=None: _rank_roster())
    monkeypatch.setattr(pipeline, "build_signals",
                        lambda *a, **kw: [_RankSignal({"1": 4.0, "2": 19.0,
                                                       "KC": 6.0})])
    return _settings(data_dir=tmp_path, weights={"ecr": 1.0})


def test_rank_prints_the_position_it_was_asked_for(_ranked, capsys):
    rc = cli.cmd_rank(_rank_args("RB"), _ranked)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Runner One" in out and "Runner Two" in out
    assert "Passer Three" not in out          # a different position entirely


def test_rank_accepts_dst_as_a_spelling_of_def(_ranked, capsys):
    """Every source names team defenses differently and DST is what users type;
    rejecting it would say "no DST players on your roster" to somebody holding
    one."""
    assert cli.cmd_rank(_rank_args("DST"), _ranked) == 0
    assert "Kansas City" in capsys.readouterr().out


def test_rank_is_case_insensitive(_ranked, capsys):
    assert cli.cmd_rank(_rank_args("rb"), _ranked) == 0
    assert "Runner One" in capsys.readouterr().out


def test_rank_of_an_empty_position_exits_nonzero_with_a_next_step(_ranked, capsys):
    """An empty table would read as "nobody is startable" rather than "the roster
    never loaded"."""
    rc = cli.cmd_rank(_rank_args("TE"), _ranked)
    assert rc == 1
    err = capsys.readouterr().err
    assert "No TE players" in err
    assert "sync" in err                      # says what to do about it


def test_rank_markdown_is_a_table_not_a_rendered_one(_ranked, capsys):
    cli.cmd_rank(_rank_args("RB", md=True), _ranked)
    out = capsys.readouterr().out
    assert out.lstrip().startswith("### Week 5 RB")   # a markdown heading
    assert "|" in out and "Runner One" in out


def test_rank_writes_the_csv_it_was_asked_for(_ranked, tmp_path, capsys):
    """`--csv`/`--json` are the machine-readable surface and the only callers of
    `render.to_rows`."""
    import csv as csv_mod

    out_path = tmp_path / "nested" / "rb.csv"
    assert cli.cmd_rank(_rank_args("RB", csv=out_path), _ranked) == 0
    assert f"Wrote {out_path}" in capsys.readouterr().out

    rows = list(csv_mod.DictReader(out_path.open()))
    assert [r["player"] for r in rows] == ["Runner One", "Runner Two"]
    assert [r["rank"] for r in rows] == ["1", "2"]
    assert rows[0]["norm_ecr"]                 # the per-signal columns are there


def test_rank_writes_the_json_it_was_asked_for(_ranked, tmp_path, capsys):
    out_path = tmp_path / "nested" / "rb.json"
    assert cli.cmd_rank(_rank_args("RB", json=out_path), _ranked) == 0
    assert f"Wrote {out_path}" in capsys.readouterr().out

    payload = json.loads(out_path.read_text())
    assert payload["week"] == 5
    assert payload["scoring"] == _ranked.scoring
    assert payload["weights"] == {"ecr": 1.0}
    assert [s["player"] for s in payload["scores"]] == ["Runner One", "Runner Two"]
    assert isinstance(payload["close_call"], bool)


def test_a_ranked_player_missing_a_signal_still_exports(_ranked, monkeypatch,
                                                       tmp_path):
    """A bye-week player has no ECR at all, so the exports have to tolerate a
    ragged set of per-signal columns rather than assuming every row has each."""
    import csv as csv_mod

    from ff_startsit import pipeline

    roster = _rank_roster() + [Player("9", "Bye Body", None, "RB")]
    monkeypatch.setattr(cli, "_get_roster",
                        lambda args, settings, profile=None: roster)
    monkeypatch.setattr(pipeline, "build_signals",
                        lambda *a, **kw: [_RankSignal({"1": 4.0, "2": 19.0})])

    out_path = tmp_path / "ragged.csv"
    assert cli.cmd_rank(_rank_args("RB", csv=out_path), _ranked) == 0

    rows = list(csv_mod.DictReader(out_path.open()))
    assert {r["player"] for r in rows} == {"Runner One", "Runner Two", "Bye Body"}
    bye = next(r for r in rows if r["player"] == "Bye Body")
    assert bye["final"] == "" and bye["norm_ecr"] == ""
    assert bye["flags"]                       # it says why, rather than ranking last


def test_rank_logs_one_decision_per_run(_ranked):
    """`rank` is a logged command — it is where most of the calibration corpus
    comes from."""
    from ff_startsit.calibrate.log_reader import load_decisions

    cli.cmd_rank(_rank_args("RB"), _ranked)
    decisions = load_decisions(_ranked.results_log_path)
    assert len(decisions) == 1
    assert decisions[0].week == 5
    assert {c.name for c in decisions[0].candidates} == {"Runner One", "Runner Two"}


class _RankInjury:
    """Injury readings with the real ``rules_out`` semantics (0.0 = cannot play)."""

    name = "injury"
    higher_is_better = True
    is_sample = False
    served_wrong_week = False

    def __init__(self, scores):
        self.scores = scores

    def is_available(self):
        return True

    def fetch(self, week, players):
        from ff_startsit.models import SignalValue
        return {p.key: SignalValue(self.scores.get(p.key, 100.0)) for p in players}

    def rules_out(self, value):
        return value.available and value.raw == 0.0


@pytest.fixture
def _ranked_with_an_ir_back(monkeypatch, tmp_path):
    """Runner Two is on IR and unranked but owns the best Vegas-ish read.

    The live Week 1 shape: with ECR missing its 0.60 is re-normalized away and
    the remaining signals rank a player who cannot take a snap.
    """
    from ff_startsit import pipeline

    monkeypatch.setattr(cli, "_print_preseason_banner", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "_get_roster",
                        lambda args, settings, profile=None: _rank_roster())
    monkeypatch.setattr(pipeline, "build_signals", lambda *a, **kw: [
        _RankSignal({"1": 4.0}),                       # only Runner One is ranked
        _RankInjury({"1": 100.0, "2": 0.0}),           # Runner Two is out
    ])
    return _settings(data_dir=tmp_path,
                     weights={"ecr": 0.60, "injury": 0.12})


def test_rank_does_not_rank_a_player_who_cannot_play(_ranked_with_an_ir_back,
                                                     capsys):
    """Pins the call site, not just the engine: `cmd_rank` must opt in to
    `exclude_unavailable`, or an IR player is ranked and startable again."""
    # Markdown, not the rich table: the table truncates the flags column to fit
    # the terminal, which would make this assertion about width, not behaviour.
    assert cli.cmd_rank(_rank_args("RB", md=True), _ranked_with_an_ir_back) == 0
    out = capsys.readouterr().out

    # He is still listed — this is your roster, he should not vanish...
    assert "Runner Two" in out
    # ...but he is not the pick, and he is marked as unstartable.
    assert "not startable" in out
    one = out.index("Runner One")
    two = out.index("Runner Two")
    assert one < two, "the healthy, ranked back must outrank the IR one"
