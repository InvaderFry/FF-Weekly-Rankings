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


def _with_boundary(settings, recs, slots):
    """Run the post-lineup boundary pass, as `score_week` does.

    The boundary belongs to the lineup, not to a positional count -- a count
    cannot see a flex slot and names the player the FLEX is about to start.
    """
    report.flag_starter_boundaries(
        settings, recs, report.build_lineup(report.scored(recs), slots=slots))
    return recs


def test_report_uses_actual_slot_counts_without_changing_blend(tmp_path):
    settings = Settings(data_dir=tmp_path, analysts='boone')
    slots = ['WR'] * 3
    plain = _with_boundary(settings, report.rank_each_position(
        replace(settings, analysts=''), players(), 1, signals=[Ranks()]), slots)
    annotated = _with_boundary(settings, report.rank_each_position(
        settings, players(), 1, signals=[Ranks()],
        analyst_fetcher=Analyst()), slots)
    a, b = plain['WR'], annotated['WR']
    assert a.scores == b.scores and a.notes == b.notes and a.close_call == b.close_call
    # Three WR slots and four receivers, so the last man in is Player 2 and the
    # first man out is Player 3 -- the same pair the old starter count found
    # here, because this league has no flex slot to move it.
    assert len(b.analyst_conflicts) == 2
    assert b.analyst_conflicts[1].boundary is True
    assert b.analyst_conflicts[1].leader == 'Player 2'
    assert b.analyst_conflicts[1].preferred == 'Player 3'
    assert b.source_status[-1][0] == ('analyst', 'Justin Boone', '')


def test_analyst_boundary_is_silent_when_the_flex_starts_the_runner_up(tmp_path):
    """The live false alarm, in the Boone half of the check.

    workTG's Week 1 report said Boone "would flip your last starting spot:
    Kenneth Walker III over Kyren Williams" -- while Walker was the FLEX pick
    and both were in the lineup. Two WR slots plus a FLEX start three of these
    four, so the only real boundary is Player 2 vs Player 3, and no conflict
    may name the pair a positional count would have picked (1 vs 2).
    """
    settings = Settings(data_dir=tmp_path, analysts='boone')
    slots = ['WR', 'WR', 'FLEX']
    recs = _with_boundary(settings, report.rank_each_position(
        settings, players(), 1, signals=[Ranks()],
        analyst_fetcher=Analyst()), slots)
    boundary = [c for c in recs['WR'].analyst_conflicts if c.boundary]
    assert len(boundary) == 1
    assert boundary[0].leader == 'Player 2' and boundary[0].preferred == 'Player 3'


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


class NotPosted(Analyst):
    """He has published nothing at WR yet — the ordinary Thursday shape."""
    def fetch(self, players, week, scoring):
        ranks = AnalystRanks('Justin Boone', scoring)
        ranks.unavailable['WR'] = (
            f'no {"full" if scoring == "ppr" else "half"} PPR list published for this position')
        return ranks


class Broken(Analyst):
    """A site-level failure, which reads the same at every position."""
    def fetch(self, players, week, scoring):
        ranks = AnalystRanks('Justin Boone', scoring)
        ranks.unavailable['WR'] = 'author index unreachable'
        return ranks


def _wr(fetcher, scoring='ppr', **kwargs):
    settings = Settings(analysts='boone', scoring=scoring, **kwargs)
    return report.rank_each_position(settings, players(), 1, signals=[Ranks()],
                                     analyst_fetcher=fetcher)['WR']


def test_unposted_position_says_so_instead_of_reading_as_agreement():
    rec = _wr(NotPosted())
    assert rec.analyst_note == 'Justin Boone has not posted his Week 1 full-PPR WR rankings yet.'
    assert 'half' in _wr(NotPosted(), scoring='half').analyst_note


def test_site_level_failure_stays_in_data_status_only():
    # Repeating one site-wide reason under every position is the duplication
    # ``_merge_source_status`` exists to prevent; Data status carries it once.
    rec = _wr(Broken())
    assert rec.analyst_note is None
    assert any('author index unreachable' in line for _, line in rec.source_status)


def test_available_position_carries_no_unposted_note():
    assert _wr(Analyst()).analyst_note is None


def test_unposted_note_changes_nothing_that_is_logged():
    plain = report.rank_each_position(Settings(analysts=''), players(), 1,
                                      signals=[Ranks()])['WR']
    rec = _wr(NotPosted())
    assert rec.notes == plain.notes and rec.close_call == plain.close_call
    assert [s.final for s in rec.scores] == [s.final for s in plain.scores]


# --- the single-position commands get the comparison too ---------------------

def _rank_rec(monkeypatch, settings, ranks_by_pos, pos='WR', unavailable=None):
    """Drive `cli._annotate_analyst` with a stubbed fetcher."""
    from ff_startsit import cli
    from ff_startsit.models import PlayerScore, Recommendation
    from ff_startsit.sources.analysts import AnalystRanks

    class Fetcher:
        def __init__(self, *a, **k):
            pass

        def fetch(self, players, week, scoring):
            return AnalystRanks('Justin Boone', scoring, by_position=ranks_by_pos,
                                unavailable=unavailable or {})

    monkeypatch.setattr('ff_startsit.sources.analysts.AnalystFetcher', Fetcher)
    ps = players()
    scores = []
    for i, p in enumerate(ps):
        s = PlayerScore(player=p)
        s.final = 100.0 - i * 10          # best -> worst, same order as `players()`
        scores.append(s)
    rec = Recommendation(week=1, scoring='ppr', weights={'ecr': 1.0}, scores=scores)
    cli._annotate_analyst(settings, rec, ps, 1, pos)
    return rec


def test_rank_command_gets_the_analyst_comparison(monkeypatch, tmp_path):
    """`/rank WR` showed nothing while the digest beside it flagged a conflict.

    `cmd_rank` and `cmd_compare` call `pipeline.recommend` directly rather than
    `rank_each_position`, so the annotation never reached them -- the same gap
    `cmd_lineup` had with the unscored-lineup fix.
    """
    settings = Settings(data_dir=tmp_path, analysts='boone')
    # Boone prefers the blend's runner-up by a wide margin -> a material conflict.
    rec = _rank_rec(monkeypatch, settings, {'WR': {'0': 30.0, '1': 4.0}})
    assert [c.boundary for c in rec.analyst_conflicts] == [False]
    assert rec.analyst_conflicts[0].preferred == 'Player 1'
    assert rec.analyst_name == 'Justin Boone'
    assert rec.source_status and rec.source_status[-1][0][0] == 'analyst'


def test_rank_command_makes_no_last_starting_spot_claim(monkeypatch, tmp_path):
    """No lineup, no boundary claim.

    A positional count here would name the player a FLEX slot starts, which is
    the false alarm the whole-roster path was just fixed to stop making.
    """
    settings = Settings(data_dir=tmp_path, analysts='boone')
    rec = _rank_rec(monkeypatch, settings,
                    {'WR': {'0': 1.0, '1': 2.0, '2': 30.0, '3': 4.0}})
    assert not any(c.boundary for c in rec.analyst_conflicts)


def test_rank_command_is_silent_when_the_analyst_is_disabled(monkeypatch, tmp_path):
    settings = Settings(data_dir=tmp_path, analysts='')
    rec = _rank_rec(monkeypatch, settings, {'WR': {'0': 30.0, '1': 4.0}})
    assert not rec.analyst_conflicts and not rec.analyst_ranks


def test_rank_command_survives_a_broken_analyst_fetch(monkeypatch, tmp_path, capsys):
    """An annotation must never sink the ranking it annotates."""
    from ff_startsit import cli
    from ff_startsit.models import PlayerScore, Recommendation

    class Boom:
        def __init__(self, *a, **k):
            pass

        def fetch(self, *a, **k):
            raise RuntimeError('yahoo down')

    monkeypatch.setattr('ff_startsit.sources.analysts.AnalystFetcher', Boom)
    ps = players()
    scores = [PlayerScore(player=p) for p in ps]
    for i, s in enumerate(scores):
        s.final = 100.0 - i * 10
    rec = Recommendation(week=1, scoring='ppr', weights={'ecr': 1.0}, scores=scores)
    cli._annotate_analyst(Settings(data_dir=tmp_path, analysts='boone'), rec, ps, 1, 'WR')
    assert not rec.analyst_conflicts
    assert 'yahoo down' in capsys.readouterr().err
