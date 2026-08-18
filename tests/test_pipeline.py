import json

from ff_startsit.config import Settings
from ff_startsit.models import Player, SignalValue
from ff_startsit.pipeline import recommend
from ff_startsit.sources.base import Signal


class FakeECR(Signal):
    name = "ecr"
    higher_is_better = False

    def __init__(self, ranks):
        self.ranks = ranks

    def is_available(self):
        return True

    def fetch(self, week, players):
        return {p.key: SignalValue(self.ranks[p.key]) for p in players}


class FakeVegas(Signal):
    name = "vegas"
    higher_is_better = True

    def is_available(self):
        return False  # exercises the unavailable path

    def fetch(self, week, players):
        raise AssertionError("should not be called when unavailable")


def test_recommend_blends_and_logs(tmp_path):
    settings = Settings(weights={"ecr": 0.75, "vegas": 0.25}, data_dir=tmp_path)
    players = [
        Player(key="1", name="Alpha", team="KC", position="RB"),
        Player(key="2", name="Bravo", team="CHI", position="RB"),
    ]
    signals = [FakeECR({"1": 1.0, "2": 8.0}), FakeVegas()]

    rec = recommend(settings, players, week=3, signals=signals, command="rank")

    assert rec.scores[0].player.key == "1"     # better ECR rank wins
    # Vegas was unavailable -> only ECR contributed.
    assert "vegas" not in rec.scores[0].normalized

    # A row was appended to the results log (the #7 hook).
    log_path = settings.results_log_path
    assert log_path.exists()
    row = json.loads(log_path.read_text().strip().splitlines()[-1])
    assert row["command"] == "rank"
    assert row["pick"] == "Alpha"
    assert row["week"] == 3


class _PoolableECR(Signal):
    """An ECR-shaped signal exposing .pooled(), like the real one."""

    name = "ecr"
    higher_is_better = False

    def __init__(self, ranks, pooled_ranks=None, pool=False):
        self.ranks = ranks
        self.pooled_ranks = pooled_ranks or {}
        self.pool = pool

    def pooled(self):
        return _PoolableECR(self.ranks, self.pooled_ranks, pool=True)

    def is_available(self):
        return True

    def fetch(self, week, players):
        src = self.pooled_ranks if self.pool else self.ranks
        return {p.key: SignalValue(src[p.key]) if p.key in src
                else SignalValue(None, available=False, note="no ECR rank")
                for p in players}


class _CountingVegas(Signal):
    name = "vegas"
    higher_is_better = True

    def __init__(self):
        self.fetches = 0

    def is_available(self):
        return True

    def fetch(self, week, players):
        self.fetches += 1
        return {p.key: SignalValue(20.0) for p in players}


def test_flex_signals_swaps_ecr_and_keeps_the_rest_by_reference():
    """The other signals must be reused, not rebuilt.

    Sharing the instances is what keeps their caches warm -- and what keeps the
    pooled pass from spending a second Odds API credit.
    """
    from ff_startsit.pipeline import flex_signals

    ecr, vegas = _PoolableECR({"1": 1.0}), _CountingVegas()
    out = flex_signals([ecr, vegas])
    assert out is not None
    by_name = {s.name: s for s in out}
    assert by_name["vegas"] is vegas          # same object, same cache
    assert by_name["ecr"] is not ecr          # a pooled sibling
    assert by_name["ecr"].pool is True


def test_flex_signals_returns_none_without_a_poolable_ecr():
    """Preseason sample runs have no real ECR, so there is nothing to pool."""
    from ff_startsit.pipeline import flex_signals

    class _Sample(Signal):
        name = "ecr"
        higher_is_better = False
        is_sample = True

        def is_available(self):
            return True

        def fetch(self, week, players):
            return {}

    assert flex_signals([_Sample(), _CountingVegas()]) is None


def _flex_players():
    return [
        Player(key="1", name="Alpha", team="KC", position="RB"),
        Player(key="2", name="Bravo", team="CHI", position="WR"),
        Player(key="3", name="Charlie", team="BUF", position="TE"),
        Player(key="4", name="Delta", team="SF", position="WR"),
        Player(key="5", name="Echo", team="NE", position="QB"),   # not flex-eligible
    ]


def test_rank_flex_pool_scores_only_flex_eligible_players(tmp_path):
    from ff_startsit.report import rank_flex_pool

    settings = Settings(weights={"ecr": 1.0}, data_dir=tmp_path)
    ecr = _PoolableECR({}, pooled_ranks={"1": 2.0, "2": 1.0, "3": 4.0, "4": 3.0})
    rec, note = rank_flex_pool(settings, _flex_players(), 3, signals=[ecr])
    assert note is None and rec is not None
    # QB excluded; ranked by the pooled cross-position value.
    assert [s.player.key for s in rec.scores] == ["2", "1", "4", "3"]


def test_rank_flex_pool_refuses_when_ecr_coverage_is_thin(tmp_path):
    """A failed FLEX fetch must not silently pick on the other signals alone.

    ECRSignal returns [] on a request failure, and the blender happily carries
    on with whatever else is available -- so without this gate the FLEX slot
    would be chosen by implied team total, health and weather, invisibly.
    """
    from ff_startsit.report import rank_flex_pool

    settings = Settings(weights={"ecr": 0.6, "vegas": 0.4}, data_dir=tmp_path)
    # Only 1 of 4 flex candidates matched -> 25% coverage, below the 50% floor.
    ecr = _PoolableECR({}, pooled_ranks={"1": 1.0})
    rec, note = rank_flex_pool(settings, _flex_players(), 3,
                               signals=[ecr, _CountingVegas()])
    assert rec is None
    assert "too few matches" in note


def test_rank_flex_pool_without_signals_degrades_quietly(tmp_path):
    from ff_startsit.report import rank_flex_pool

    settings = Settings(data_dir=tmp_path)
    rec, note = rank_flex_pool(settings, _flex_players(), 3, signals=None)
    assert rec is None and note


def test_rank_flex_pool_is_never_logged(tmp_path):
    """A pooled row would re-log the same players under a second frame.

    The calibrator scores pairwise concordance within one logged decision, so
    that would double-weight those players in the grid search.
    """
    from ff_startsit.report import rank_flex_pool

    settings = Settings(weights={"ecr": 1.0}, data_dir=tmp_path)
    ecr = _PoolableECR({}, pooled_ranks={"1": 2.0, "2": 1.0, "3": 4.0, "4": 3.0})
    rank_flex_pool(settings, _flex_players(), 3, signals=[ecr])
    assert not settings.results_log_path.exists()


def _logging_score_week(tmp_path, monkeypatch, **kw):
    """score_week over the flex roster with a fake ECR — offline, like every test."""
    from ff_startsit import report

    settings = Settings(weights={"ecr": 1.0}, data_dir=tmp_path)
    ecr = _PoolableECR({"1": 1.0, "2": 2.0, "3": 3.0, "4": 4.0, "5": 1.0},
                       pooled_ranks={"1": 2.0, "2": 1.0, "3": 4.0, "4": 3.0})
    monkeypatch.setattr(report, "build_signals", lambda settings: [ecr])
    ws = report.score_week(settings, _flex_players(), 3, **kw)
    return settings, ws


def test_score_week_logs_only_when_asked(tmp_path, monkeypatch):
    """Whole-roster runs feed the #7 calibrator only on an explicit opt-in.

    `publish`/`report` score every position every week, so logging them by
    default would swamp the corpus with decisions nobody acted on.
    """
    settings, _ = _logging_score_week(tmp_path, monkeypatch)
    assert not settings.results_log_path.exists()


def test_score_week_log_excludes_the_pooled_flex_pass(tmp_path, monkeypatch):
    """Even when logging is on, the pooled pass stays out of the corpus."""
    import json

    settings, ws = _logging_score_week(tmp_path, monkeypatch, log=True)

    # The pooled pass really ran — otherwise this test would pass vacuously.
    assert ws.flex is not None and ws.flex_note is None

    rows = [json.loads(ln) for ln in
            settings.results_log_path.read_text().strip().splitlines()]
    assert rows, "log=True should have written the per-position decisions"
    assert {r["command"] for r in rows} == {"report"}
    # Every logged decision is within one position group, never the pooled set.
    for row in rows:
        assert len({c["position"] for c in row["candidates"]}) == 1
