"""The annotation layer must survive report/CLI wiring without changing scores."""
from dataclasses import replace
from types import SimpleNamespace

from ff_startsit import cli, report
from ff_startsit.config import Settings, LeagueProfile, load_settings
from ff_startsit.models import Player, SignalValue
from ff_startsit.sources.analysts import AnalystRanks
from ff_startsit.sources.base import Signal


class Ranks(Signal):
    name = 'ecr'
    higher_is_better = False
    def is_available(self):
        return True
    def fetch(self, week, players):
        return {p.key: SignalValue(float(p.key) + 1) for p in players}


class Analyst:
    def __init__(self, *args):
        self.calls = []
    def fetch(self, players, week, scoring):
        self.calls.append((week, scoring))
        return AnalystRanks('Justin Boone', scoring, by_position={
            'WR': {p.key: 30 - float(p.key) * 6 for p in players}})


def players():
    return [Player(str(i), f'Player {i}', 'KC', 'WR') for i in range(4)]


def test_report_uses_actual_slot_counts_without_changing_blend(tmp_path):
    settings = Settings(data_dir=tmp_path, analysts='boone')
    plain = report.rank_each_position(replace(settings, analysts=''), players(), 1,
                                      signals=[Ranks()], slots=['WR'] * 3)
    annotated = report.rank_each_position(settings, players(), 1, signals=[Ranks()],
        slots=['WR'] * 3, analyst_fetcher=Analyst())
    a, b = plain['WR'], annotated['WR']
    assert a.scores == b.scores and a.notes == b.notes and a.close_call == b.close_call
    assert len(b.analyst_conflicts) == 2
    assert b.analyst_conflicts[1].leader == 'Player 2'
    assert b.analyst_conflicts[1].preferred == 'Player 3'
    assert b.source_status[-1][0] == ('analyst', 'Justin Boone', '')


def test_disabled_report_does_not_construct_fetcher(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError('disabled analyst attempted a fetch')
    monkeypatch.setattr(report, 'AnalystFetcher', unexpected)
    assert not report.rank_each_position(Settings(), players(), 1, signals=[Ranks()])['WR'].analyst_conflicts


def test_sample_signals_do_not_compare_real_analyst_ranks():
    signal = Ranks(); signal.is_sample = True
    analyst = Analyst()
    rec = report.rank_each_position(Settings(analysts='boone'), players(), 1,
                                    signals=[signal], analyst_fetcher=analyst)['WR']
    assert not analyst.calls and not rec.analyst_conflicts


def test_multileague_cli_shares_fetcher_and_passes_league_scoring(monkeypatch, tmp_path):
    import ff_startsit.sources.analysts as module
    instances = []
    class Tracked(Analyst):
        def __init__(self, *args):
            super().__init__(); instances.append(self)
    monkeypatch.setattr(module, 'AnalystFetcher', Tracked)
    monkeypatch.setattr(cli, '_get_roster', lambda *args: players())
    monkeypatch.setattr(report, 'build_signals', lambda settings: [Ranks()])
    monkeypatch.setattr(report, 'build_journalist_view', lambda *args: None)
    settings = Settings(analysts='boone', data_dir=tmp_path, leagues=[
        LeagueProfile(name='Half', source='manual', scoring='half'),
        LeagueProfile(name='Full', source='manual', scoring='ppr')])
    bundles = cli._league_bundles(SimpleNamespace(log=False), settings, 1)
    assert len(instances) == 1
    assert instances[0].calls == [(1, 'half'), (1, 'ppr')]
    assert [b.scoring for b in bundles] == ['half', 'ppr']
    assert [b.recs['WR'].source_status[-1][0] for b in bundles] == [
        ('analyst', 'Justin Boone', 'Half'), ('analyst', 'Justin Boone', 'Full')]


def test_config_defaults_and_invalid_values(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv('FF_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('FF_ANALYSTS', 'not-boone')
    monkeypatch.setenv('FF_ANALYST_MIN_GAP', 'nope')
    settings = load_settings(env_file=tmp_path / 'absent')
    assert settings.analysts == '' and settings.analyst_min_gap == 5
    assert 'FF_ANALYSTS' in capsys.readouterr().out
    monkeypatch.setenv('FF_ANALYSTS', ' BOONE ')
    monkeypatch.setenv('FF_ANALYST_MIN_GAP', '2')
    settings = load_settings(env_file=tmp_path / 'absent')
    assert settings.analysts == 'boone' and settings.analyst_min_gap == 2
    for bad in ['-1', 'nan', 'inf']:
        monkeypatch.setenv('FF_ANALYST_MIN_GAP', bad)
        assert load_settings(env_file=tmp_path / 'absent').analyst_min_gap == 5
    for disabled in ['', 'off', 'false', '0', 'no']:
        monkeypatch.setenv('FF_ANALYSTS', disabled)
        assert load_settings(env_file=tmp_path / 'absent').analysts == ''
