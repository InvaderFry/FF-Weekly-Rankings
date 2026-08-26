"""The `waivers` command end to end, offline: fan-out, skips, and the log guard."""

import pytest

from ff_startsit.waivers.build import build_bundle as cli_build_bundle

from ff_startsit import cli
from ff_startsit.config import LeagueProfile, Settings
from ff_startsit.models import Player, SignalValue
from ff_startsit.roster.base import RosterError
from ff_startsit.sources.base import Signal
from ff_startsit.sources.schedule import ScheduleProvider
from ff_startsit.waivers.base import LeagueViewProvider
from ff_startsit.waivers.models import (ACQ_FAAB, FantasyTeam, LeagueRules,
                                        PoolPlayer)

RANKS = {"m1": 3, "m2": 5, "m3": 8, "m4": 60, "m5": 12, "m6": 30, "m7": 9,
         "m8": 6, "m9": 4, "m10": 85, "t1": 25, "t2": 40, "t3": 7,
         "f1": 20, "f2": 75}


def _p(key, name, pos, team="KC"):
    return Player(key=key, name=name, team=team, position=pos)


MINE = [_p("m1", "My Qb", "QB"), _p("m2", "My Rb1", "RB"), _p("m3", "My Rb2", "RB"),
        _p("m4", "My Rb3", "RB"), _p("m5", "My Wr1", "WR"), _p("m6", "My Wr2", "WR"),
        _p("m7", "My Te", "TE"), _p("m8", "My K", "K"), _p("m9", "Kansas City", "DEF"),
        # A genuine bench body: without one, nothing is droppable and no add
        # can be recommended, which is correct behaviour but tests nothing.
        _p("m10", "My Wr3", "WR")]
THEIRS = [_p("t1", "Their Rb", "RB"), _p("t2", "Their Wr", "WR"), _p("t3", "Their Te", "TE")]
POOL = [PoolPlayer(_p("f1", "Free Wr", "WR"), percent_owned=35.0),
        PoolPlayer(_p("f2", "Hurt Rb", "RB"), percent_owned=3.0, injury_status="IR")]


class _FakeECR(Signal):
    name = "ecr"
    higher_is_better = False

    def is_available(self):
        return True

    def fetch(self, week, players):
        return {p.key: SignalValue(raw=float(RANKS[p.key]) if p.key in RANKS else None,
                                   available=p.key in RANKS) for p in players}


class _FakeProvider(LeagueViewProvider):
    name = "espn"

    def get_roster_players(self):
        return list(MINE)

    def cache_tag(self):
        return "fake"

    def get_league_teams(self):
        return [FantasyTeam("1", "My Squad", tuple(MINE), is_mine=True, faab_spent=20.0),
                FantasyTeam("2", "Rival FC", tuple(THEIRS))]

    def get_free_agents(self, week, limit=150):
        return list(POOL)

    def get_league_rules(self):
        return LeagueRules(acquisition_type=ACQ_FAAB, faab_budget=100.0,
                           roster_slots={"QB": 1, "RB": 2, "WR": 2, "TE": 1,
                                         "K": 1, "DEF": 1})


class _NoLeagueView:
    """A manual-CSV-style provider: a roster, but no league behind it."""

    name = "manual"

    def get_roster_players(self):
        return list(MINE)

    def cache_tag(self):
        return "manual"


class _Args:
    def __init__(self, **kw):
        defaults = dict(source=None, league=None, team=None, league_name=None,
                        refresh=False, offline=False, week=9, report=None,
                        dashboard=None, discord=False, url=None,
                        all_leagues=False, limit=None, no_trades=False,
                        no_columns=True, rehearse=False)
        defaults.update(kw)
        self.__dict__.update(defaults)


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    """Pin every network edge: signals, schedule, week, roster and provider."""
    monkeypatch.setattr(cli, "_resolve_week", lambda args, settings: 9)
    monkeypatch.setattr(cli, "_get_roster", lambda args, s, p=None: list(MINE))
    monkeypatch.setattr("ff_startsit.waivers.build.build_signals",
                        lambda settings, **kw: [_FakeECR()])
    monkeypatch.setattr(ScheduleProvider, "for_week", lambda self, week: {})
    monkeypatch.setattr("ff_startsit.waivers.build.journalist_ranks",
                        lambda settings, players, week: {})
    # Pin the calendar too: build_bundle refuses before Week 1, and these cases
    # are about the in-season command.
    monkeypatch.setattr("ff_startsit.waivers.build.is_preseason",
                        lambda today=None: False)
    monkeypatch.setattr(cli.season, "waiver_banner",
                        lambda rehearse=False, today=None: None)


def _settings(tmp_path, leagues=None):
    return Settings(roster_source="espn", espn_league_id="111",
                    data_dir=tmp_path / "cache",
                    leagues=leagues or [LeagueProfile(name="default", source="espn",
                                                      league_id="111")])


def test_waivers_runs_and_prints_a_digest(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "build_roster_provider",
                        lambda *a, **k: _FakeProvider())
    assert cli.cmd_waivers(_Args(), _settings(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "Week 9 waiver wire" in out and "Free Wr" in out


def test_waivers_never_writes_the_results_log(monkeypatch, tmp_path):
    """The #7 invariant, at the command level: `waivers` has no --log flag and
    must leave the append-only decision log untouched."""
    monkeypatch.setattr(cli, "build_roster_provider",
                        lambda *a, **k: _FakeProvider())
    settings = _settings(tmp_path)
    settings.results_log_path.parent.mkdir(parents=True, exist_ok=True)
    settings.results_log_path.write_text('{"pre": "existing"}\n')

    cli.cmd_waivers(_Args(), settings)

    assert settings.results_log_path.read_text() == '{"pre": "existing"}\n'


def test_the_waivers_parser_has_no_log_flag():
    parser = cli._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["waivers", "--log", "x.jsonl"])


def test_writes_report_and_dashboard_files(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "build_roster_provider",
                        lambda *a, **k: _FakeProvider())
    md = tmp_path / "out" / "waivers.md"
    html = tmp_path / "out" / "waivers.html"
    cli.cmd_waivers(_Args(report=md, dashboard=html), _settings(tmp_path))
    assert "Week 9 waiver wire" in md.read_text()
    assert html.read_text().startswith("<!doctype html>")


def test_a_source_without_a_league_view_is_skipped_with_a_reason(
        monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "build_roster_provider",
                        lambda *a, **k: _NoLeagueView())
    assert cli.cmd_waivers(_Args(), _settings(tmp_path)) == 1
    err = capsys.readouterr().err
    assert "can't see a free-agent pool" in err


def test_all_leagues_skips_a_failing_league_and_still_reports(
        monkeypatch, tmp_path, capsys):
    leagues = [LeagueProfile(name="work", source="espn", league_id="1"),
               LeagueProfile(name="broken", source="espn", league_id="2")]

    def _provider(settings, source=None, league=None, team=None, profile=None):
        if profile is not None and profile.name == "broken":
            raise RosterError("ESPN denied access (401/403)")
        return _FakeProvider()

    monkeypatch.setattr(cli, "build_roster_provider", _provider)
    args = _Args(all_leagues=True)
    assert cli.cmd_waivers(args, _settings(tmp_path, leagues)) == 0
    captured = capsys.readouterr()
    assert "skipping league 'broken'" in captured.err
    assert "work" in captured.out


def test_no_scoreable_league_exits_nonzero(monkeypatch, tmp_path):
    def _provider(*a, **k):
        raise RosterError("nope")

    monkeypatch.setattr(cli, "build_roster_provider", _provider)
    assert cli.cmd_waivers(_Args(), _settings(tmp_path)) == 1


def test_no_trades_flag_drops_the_section(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "build_roster_provider",
                        lambda *a, **k: _FakeProvider())
    cli.cmd_waivers(_Args(no_trades=True), _settings(tmp_path))
    assert "Trade ideas" not in capsys.readouterr().out


def test_discord_is_skipped_without_a_webhook(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "build_roster_provider",
                        lambda *a, **k: _FakeProvider())
    assert cli.cmd_waivers(_Args(discord=True), _settings(tmp_path)) == 0
    assert "DISCORD_WEBHOOK_URL is not set" in capsys.readouterr().err


def test_a_discord_failure_does_not_sink_the_run(monkeypatch, tmp_path, capsys):
    """Same contract as publish: the digest and page are what the workflow
    depends on."""
    monkeypatch.setattr(cli, "build_roster_provider",
                        lambda *a, **k: _FakeProvider())
    monkeypatch.setattr("ff_startsit.output.discord.send_discord",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("429")))
    settings = _settings(tmp_path)
    settings.discord_webhook_url = "https://discord.test/hook"
    assert cli.cmd_waivers(_Args(discord=True), settings) == 0
    assert "Discord notification failed" in capsys.readouterr().err


def test_preseason_command_warns_and_suggests_nothing(monkeypatch, tmp_path, capsys):
    """The whole path that produced a 'Week 3 waivers' message in August: the
    command must say the season hasn't started and name no adds or drops."""
    monkeypatch.setattr(cli, "build_roster_provider",
                        lambda *a, **k: _FakeProvider())
    monkeypatch.setattr("ff_startsit.waivers.build.is_preseason",
                        lambda today=None: True)
    monkeypatch.setattr("ff_startsit.waivers.build.is_rehearsal_window",
                        lambda today=None: False)
    monkeypatch.setattr(cli.season, "waiver_banner",
                        lambda rehearse=False, today=None: cli.season.WAIVER_BANNER)

    assert cli.cmd_waivers(_Args(), _settings(tmp_path)) == 0

    captured = capsys.readouterr()
    assert "PRESEASON" in captured.err          # the Actions log says it too
    assert "PRESEASON" in captured.out          # and so does the digest
    assert "Free Wr" not in captured.out        # no add is named


def test_rehearse_flag_reaches_the_builder_and_names_the_situation(
        monkeypatch, tmp_path, capsys):
    """The manual dress rehearsal: --rehearse must both reach the builder and
    say which of the two preseason situations this is."""
    monkeypatch.setattr(cli, "build_roster_provider",
                        lambda *a, **k: _FakeProvider())
    monkeypatch.setattr("ff_startsit.waivers.build.is_preseason",
                        lambda today=None: True)
    monkeypatch.setattr(cli.season, "waiver_banner",
                        lambda rehearse=False, today=None: (
                            cli.season.REHEARSAL_BANNER if rehearse
                            else cli.season.WAIVER_BANNER))

    seen = {}
    real = cli_build_bundle

    def _spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)

    monkeypatch.setattr("ff_startsit.waivers.build.build_bundle", _spy)
    assert cli.cmd_waivers(_Args(rehearse=True), _settings(tmp_path)) == 0

    assert seen["rehearse"] is True
    captured = capsys.readouterr()
    assert "DRESS REHEARSAL" in captured.err     # not the refusal notice
    assert "Free Wr" in captured.out             # and it really scored the pool
