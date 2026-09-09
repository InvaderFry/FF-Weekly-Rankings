"""Single-candidate rows must never count as evidence.

``pipeline.recommend`` stopped writing them, but the log is append-only: the
rows already on the ``calibration-data`` branch cannot be retracted, and 30 of
the first 62 there are exactly this shape. So the guarantee that matters is not
"they are no longer written" — it is that **reading** them changes nothing.

Both consumers enforce that independently (``learner.join_outcomes`` and
``backtest`` each require two joined candidates), which is easy to break by
accident and silent when broken: a corpus would simply look twice its real size
and a hit-rate would drift upward with no failing test. Offline throughout.
"""

from __future__ import annotations

from ff_startsit.calibrate.backtest import backtest
from ff_startsit.calibrate.learner import calibrate
from ff_startsit.calibrate.log_reader import Candidate, Decision

WEIGHTS = {"ecr": 0.6, "vegas": 0.4}


def _cand(key: str, ecr: float, vegas: float) -> Candidate:
    return Candidate(key=key, name=f"P{key}", position="RB",
                     normalized={"ecr": ecr, "vegas": vegas})


def _decision(week: int, keys: list[str], close_call: bool = False) -> Decision:
    return Decision(week=week, season="2026", scoring="ppr", weights=dict(WEIGHTS),
                    close_call=close_call,
                    candidates=[_cand(k, 100.0 - 10 * i, 50.0)
                                for i, k in enumerate(keys)])


def _points(mapping: dict[str, float]):
    def provider(season, week, scoring):
        def lookup(key, name, position):
            return mapping.get(key)
        return lookup
    return provider


def test_lone_rows_do_not_count_toward_the_write_floors():
    """A hundred lone rows are still zero evidence."""
    real = [_decision(w, ["a", "b"]) for w in (1, 2, 3)]
    lone = [_decision(1, [f"solo{i}"]) for i in range(100)]
    provider = _points({"a": 20.0, "b": 5.0,
                        **{f"solo{i}": 12.0 for i in range(100)}})

    with_lone = calibrate(real + lone, provider, base_weights=WEIGHTS)
    without = calibrate(real, provider, base_weights=WEIGHTS)

    assert with_lone.decisions_used == without.decisions_used == 3
    assert with_lone.pairs_used == without.pairs_used
    assert with_lone.weeks_used == without.weeks_used == 3


def test_lone_rows_do_not_move_the_backtest_hit_rate():
    """A pick that was the only option always "hits" — it must never be counted."""
    # One real decision, deliberately a miss, so any leaked lone row would show
    # up as a hit-rate above zero.
    real = [_decision(1, ["a", "b"])]
    lone = [_decision(1, [f"solo{i}"]) for i in range(20)]
    provider = _points({"a": 1.0, "b": 99.0,
                        **{f"solo{i}": 30.0 for i in range(20)}})

    result = backtest(real + lone, provider, base_weights=WEIGHTS)

    assert result.decisions_used == 1
    assert result.hits == 0
    assert result.hit_rate == 0.0


def test_lone_rows_do_not_pad_the_confident_bucket():
    """The confident/close-call split is the honesty check; unflagged lone rows
    would quietly inflate the confident side of it."""
    real = [_decision(1, ["a", "b"], close_call=True)]
    lone = [_decision(1, [f"solo{i}"]) for i in range(10)]
    provider = _points({"a": 20.0, "b": 5.0,
                        **{f"solo{i}": 9.0 for i in range(10)}})

    result = backtest(real + lone, provider, base_weights=WEIGHTS)

    assert result.confident_n == 0
    assert result.close_call_n == 1
