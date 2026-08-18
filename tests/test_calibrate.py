import json

from ff_startsit.calibrate.learner import (
    JoinedDecision,
    calibrate,
    concordance,
    hit_rate,
    signals_in,
    simplex,
)
from ff_startsit.calibrate.log_reader import dedupe_decisions, load_decisions


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
    joined = [JoinedDecision(season="2024", week=3, rows=[
        ({"ecr": 100.0, "vegas": 0.0}, 1.0),
        ({"ecr": 0.0, "vegas": 100.0}, 9.0),
    ])]
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


def test_malformed_row_is_skipped_not_fatal(tmp_path):
    """The log is append-only, so one corrupt row would poison it forever.

    Only JSON *syntax* errors used to be survivable; a non-numeric score or an
    unparseable week aborted the whole load and discarded every valid decision.
    """
    log = tmp_path / "results_log.jsonl"
    _write_log(log, weeks=2)
    good = log.read_text().splitlines()

    bad_score = json.loads(good[0])
    bad_score["candidates"][0]["normalized"]["ecr"] = "n/a"
    bad_week = json.loads(good[0])
    bad_week["week"] = "week five"
    bad_weight = json.loads(good[0])
    bad_weight["weights"]["ecr"] = [1, 2]

    log.write_text("\n".join([
        good[0],
        json.dumps(bad_score),
        "[1, 2]",                  # valid JSON, wrong shape
        json.dumps(bad_week),
        json.dumps(bad_weight),
        "{not json at all",
        good[1],
    ]))

    decisions = load_decisions(log)
    assert len(decisions) == 2
    assert [d.week for d in decisions] == [1, 2]


def test_week_filter_survives_a_row_with_an_unparseable_week(tmp_path):
    log = tmp_path / "results_log.jsonl"
    _write_log(log, weeks=3)
    rows = log.read_text().splitlines()
    broken = json.loads(rows[0])
    broken["week"] = {"nested": "object"}
    log.write_text("\n".join([json.dumps(broken)] + rows))

    assert [d.week for d in load_decisions(log, week=2)] == [2]


def _one_big_decision(path, candidates=9):
    """One ranking with many players: the shape that used to clear --min-pairs.

    C(9,2) = 36 correlated pairs from a single slate, single injury report,
    single weather system.
    """
    outcomes, cands = {}, []
    for i in range(candidates):
        key = f"1-{i}"
        cands.append({"key": key, "name": f"P{i}", "position": "RB",
                      "normalized": {"ecr": 100.0 - i * 10.0, "vegas": i * 10.0}})
        outcomes[key] = float(i)
    path.write_text(json.dumps({
        "ts": "2024-10-01T12:00:00+00:00", "week": 1, "scoring": "ppr",
        "weights": {"ecr": 0.65, "vegas": 0.20}, "candidates": cands,
    }))
    return outcomes


def test_one_ranking_clears_min_pairs_but_is_not_enough_data(tmp_path):
    """The review's exact case: 36 pairs from one decision beats a floor of 30."""
    log = tmp_path / "results_log.jsonl"
    outcomes = _one_big_decision(log)
    result = calibrate(load_decisions(log), _provider(outcomes),
                       base_weights={"ecr": 0.65, "vegas": 0.20}, step=0.25)

    assert result.pairs_used == 36 and result.pairs_used >= 30
    assert result.weeks_used == 1 and result.decisions_used == 1
    assert result.enough_data is False
    assert any("week" in s for s in result.shortfalls)


def test_evidence_spread_across_weeks_is_enough_data(tmp_path):
    log = tmp_path / "results_log.jsonl"
    outcomes = _write_log(log, weeks=5)
    result = calibrate(load_decisions(log), _provider(outcomes),
                       base_weights={"ecr": 0.65, "vegas": 0.20}, step=0.25)

    assert (result.weeks_used, result.decisions_used) == (5, 5)
    assert result.enough_data is True
    assert result.shortfalls == []


def test_shortfalls_name_every_floor_that_failed(tmp_path):
    log = tmp_path / "results_log.jsonl"
    outcomes = _write_log(log, weeks=2)      # 2 weeks, 2 decisions, 12 pairs
    result = calibrate(load_decisions(log), _provider(outcomes),
                       base_weights={"ecr": 0.65, "vegas": 0.20}, step=0.25)

    assert result.enough_data is False
    joined = " ".join(result.shortfalls)
    assert "12 comparable pairs (need 30)" in joined
    assert "2 joined decisions (need 5)" in joined
    assert "2 distinct week(s) (need 3)" in joined


def test_dedupe_collapses_a_repeated_run(tmp_path):
    """Re-running the same question in the same week is one piece of evidence."""
    log = tmp_path / "results_log.jsonl"
    outcomes = _write_log(log, weeks=5)
    rows = log.read_text().splitlines()
    log.write_text("\n".join(rows + [rows[0], rows[2]]))   # two exact re-runs

    raw = load_decisions(log)
    deduped = dedupe_decisions(raw)
    assert (len(raw), len(deduped)) == (7, 5)

    result = calibrate(deduped, _provider(outcomes),
                       base_weights={"ecr": 0.65, "vegas": 0.20}, step=0.25)
    inflated = calibrate(raw, _provider(outcomes),
                         base_weights={"ecr": 0.65, "vegas": 0.20}, step=0.25)
    assert (result.pairs_used, result.decisions_used) == (30, 5)
    assert inflated.pairs_used == 42          # what the duplicates would have bought


def test_dedupe_keeps_the_freshest_run_of_a_repeat(tmp_path):
    """A Sunday re-run has newer injury and weather reads than Thursday's."""
    log = tmp_path / "results_log.jsonl"
    _write_log(log, weeks=1)
    first = json.loads(log.read_text().splitlines()[0])
    later = json.loads(json.dumps(first))
    later["candidates"][0]["normalized"]["ecr"] = 42.0
    log.write_text("\n".join([json.dumps(first), json.dumps(later)]))

    deduped = dedupe_decisions(load_decisions(log))
    assert len(deduped) == 1
    assert deduped[0].candidates[0].normalized["ecr"] == 42.0
