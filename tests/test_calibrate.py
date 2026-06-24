import json

from ff_startsit.calibrate.learner import (
    calibrate,
    concordance,
    hit_rate,
    signals_in,
    simplex,
)
from ff_startsit.calibrate.log_reader import load_decisions


def _write_log(path, weeks=5):
    """A log where 'vegas' perfectly predicts outcomes and 'ecr' anti-predicts."""
    outcomes = {}
    rows = []
    for wk in range(1, weeks + 1):
        cands = []
        for i in range(4):
            key = f"{wk}-{i}"
            cands.append({
                "key": key, "name": f"P{wk}{i}", "team": "KC", "position": "RB",
                "final": 0.0,
                "normalized": {"ecr": 100.0 - i * 25.0, "vegas": i * 25.0},
                "raw": {}, "flags": [],
            })
            outcomes[key] = float(i)  # actual points rise with i, matching vegas
        rows.append({
            "ts": "2024-10-01T12:00:00+00:00", "command": "rank --pos RB",
            "week": wk, "scoring": "ppr",
            "weights": {"ecr": 0.65, "vegas": 0.20, "injury": 0.15},
            "close_call": False, "notes": [], "pick": "P0", "candidates": cands,
        })
    path.write_text("\n".join(json.dumps(r) for r in rows))
    return outcomes


def _provider(outcomes):
    return lambda season, week, scoring: (lambda key, name, pos: outcomes.get(key))


def test_simplex_enumerates_normalized_grid():
    vecs = list(simplex(["a", "b"], 0.5))
    sums = {round(sum(v.values()), 6) for v in vecs}
    assert sums == {1.0}
    # 3 vectors at step 0.5 over 2 signals: (0,1),(0.5,0.5),(1,0).
    assert len(vecs) == 3
    # 3 signals at 0.05 -> the documented 231 combinations.
    assert len(list(simplex(["a", "b", "c"], 0.05))) == 231


def test_learner_recovers_predictive_signal(tmp_path):
    log = tmp_path / "results_log.jsonl"
    outcomes = _write_log(log)
    decisions = load_decisions(log)

    assert signals_in(decisions) == ["ecr", "vegas"]  # canonical order, injury absent

    result = calibrate(decisions, _provider(outcomes),
                       base_weights={"ecr": 0.65, "vegas": 0.20, "injury": 0.15},
                       step=0.05, min_pairs=5)

    assert result.pairs_used == 30          # 5 decisions * C(4,2)
    assert result.decisions_used == 5
    assert result.best_weights["vegas"] == 1.0
    assert result.best_weights["ecr"] == 0.0
    assert result.best_concordance == 1.0
    assert result.best_hit_rate == 1.0
    # The ecr-heavy current weights get the ordering exactly backwards here.
    assert result.current_concordance == 0.0
    assert result.enough_data is True


def test_concordance_and_hit_rate_math():
    # One decision: vegas orders correctly, so vegas=1 is perfect, ecr=1 is inverted.
    joined = [[
        ({"ecr": 100.0, "vegas": 0.0}, 1.0),
        ({"ecr": 0.0, "vegas": 100.0}, 9.0),
    ]]
    conc_vegas, pairs = concordance(joined, {"vegas": 1.0})
    assert (conc_vegas, pairs) == (1.0, 1)
    conc_ecr, _ = concordance(joined, {"ecr": 1.0})
    assert conc_ecr == 0.0
    assert hit_rate(joined, {"vegas": 1.0}) == 1.0
    assert hit_rate(joined, {"ecr": 1.0}) == 0.0


def test_min_pairs_guard_marks_thin_data(tmp_path):
    log = tmp_path / "results_log.jsonl"
    outcomes = _write_log(log, weeks=1)  # only C(4,2) = 6 pairs
    decisions = load_decisions(log)
    result = calibrate(decisions, _provider(outcomes),
                       base_weights={"ecr": 0.65, "vegas": 0.20},
                       step=0.1, min_pairs=30)
    assert result.pairs_used == 6
    assert result.enough_data is False


def test_partial_signal_candidates_excluded_for_fixed_denominator(tmp_path):
    """A candidate missing a tuned signal (e.g. a bye-week player with no Vegas
    line) is dropped, so every weighting is scored on the same comparable set."""
    rows = [{
        "ts": "2024-10-01T12:00:00+00:00", "command": "rank --pos RB",
        "week": 1, "scoring": "ppr",
        "weights": {"ecr": 0.65, "vegas": 0.20}, "close_call": False,
        "notes": [], "pick": "A", "candidates": [
            {"key": "a", "name": "A", "team": "KC", "position": "RB",
             "final": 0.0, "normalized": {"ecr": 90.0, "vegas": 80.0},
             "raw": {}, "flags": []},
            {"key": "b", "name": "B", "team": "KC", "position": "RB",
             "final": 0.0, "normalized": {"ecr": 60.0, "vegas": 40.0},
             "raw": {}, "flags": []},
            # Bye week: ECR only, no Vegas line -> excluded from the comparison.
            {"key": "c", "name": "C", "team": None, "position": "RB",
             "final": 0.0, "normalized": {"ecr": 30.0},
             "raw": {}, "flags": []},
        ],
    }]
    log = tmp_path / "results_log.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in rows))
    outcomes = {"a": 20.0, "b": 10.0, "c": 5.0}

    decisions = load_decisions(log)
    result = calibrate(decisions, _provider(outcomes),
                       base_weights={"ecr": 0.65, "vegas": 0.20}, step=0.5,
                       min_pairs=1)
    # Only A and B are comparable (C lacks vegas): C(2,2) = 1 pair, not C(3,2)=3.
    assert result.pairs_used == 1


def test_unjoinable_outcomes_yield_no_pairs(tmp_path):
    log = tmp_path / "results_log.jsonl"
    _write_log(log)
    decisions = load_decisions(log)
    # Provider that can't resolve anything (e.g. outcomes not posted yet).
    empty = calibrate(decisions, lambda s, w, sc: (lambda k, n, p: None),
                      base_weights={"ecr": 0.65, "vegas": 0.20}, step=0.5)
    assert empty.pairs_used == 0
    assert empty.decisions_used == 0
