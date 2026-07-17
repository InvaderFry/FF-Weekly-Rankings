import argparse
import json

from ff_startsit.calibrate.backtest import backtest
from ff_startsit.calibrate.log_reader import load_decisions
from ff_startsit.cli import cmd_backtest
from ff_startsit.config import Settings


def _cand(key, ecr, name=None):
    return {"key": key, "name": name or key.upper(), "team": "KC", "position": "RB",
            "final": 0.0, "normalized": {"ecr": ecr}, "raw": {}, "flags": []}


def _write_log(path):
    """Two confident-correct decisions + one flagged close call that missed."""
    rows = [
        {"ts": "2024-10-01T12:00:00+00:00", "command": "rank", "week": 1,
         "scoring": "ppr", "weights": {"ecr": 1.0}, "close_call": False,
         "notes": [], "pick": "A",
         "candidates": [_cand("a", 90.0), _cand("b", 50.0)]},
        {"ts": "2024-10-08T12:00:00+00:00", "command": "rank", "week": 2,
         "scoring": "ppr", "weights": {"ecr": 1.0}, "close_call": False,
         "notes": [], "pick": "C",
         "candidates": [_cand("c", 80.0), _cand("d", 40.0)]},
        {"ts": "2024-10-15T12:00:00+00:00", "command": "rank", "week": 3,
         "scoring": "ppr", "weights": {"ecr": 1.0}, "close_call": True,
         "notes": [], "pick": "E",
         "candidates": [_cand("e", 90.0), _cand("f", 40.0)]},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows))
    # Actual points: a>b and c>d (picks were right); e<f (flagged pick missed).
    return {"a": 20.0, "b": 10.0, "c": 30.0, "d": 5.0, "e": 2.0, "f": 12.0}


def _provider(outcomes):
    return lambda season, week, scoring: (lambda key, name, pos: outcomes.get(key))


def test_backtest_scores_hits_and_close_call_split(tmp_path):
    log = tmp_path / "results_log.jsonl"
    outcomes = _write_log(log)
    decisions = load_decisions(log)

    result = backtest(decisions, _provider(outcomes), base_weights={"ecr": 1.0})

    assert result.decisions_used == 3
    assert result.hits == 2
    assert result.hit_rate == 2 / 3
    # Only the missed decision left points on the bench: (12 - 2) / 3 decisions.
    assert result.avg_points_lost == round(10.0 / 3, 2)
    # Honesty split: confident picks were both right, the flagged one was wrong.
    assert (result.confident_n, result.confident_hits) == (2, 0 + 2)
    assert result.confident_hit_rate == 1.0
    assert (result.close_call_n, result.close_call_hits) == (1, 0)
    assert result.close_call_hit_rate == 0.0
    assert len(result.weeks) == 3


def test_backtest_uses_logged_weights_over_base(tmp_path):
    """A decision's own logged weights drive the replay, not the current base."""
    rows = [{
        "ts": "2024-10-01T12:00:00+00:00", "command": "rank", "week": 1,
        "scoring": "ppr", "weights": {"vegas": 1.0}, "close_call": False,
        "notes": [], "pick": "A", "candidates": [
            {"key": "a", "name": "A", "team": "KC", "position": "RB", "final": 0.0,
             "normalized": {"ecr": 10.0, "vegas": 90.0}, "raw": {}, "flags": []},
            {"key": "b", "name": "B", "team": "KC", "position": "RB", "final": 0.0,
             "normalized": {"ecr": 90.0, "vegas": 10.0}, "raw": {}, "flags": []},
        ]}]
    log = tmp_path / "results_log.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in rows))
    # Vegas favors A and A actually scored more -> logged vegas weights => hit.
    outcomes = {"a": 25.0, "b": 5.0}
    result = backtest(load_decisions(log), _provider(outcomes),
                      base_weights={"ecr": 1.0})  # base would have picked B
    assert result.hits == 1


def test_backtest_skips_decisions_with_fewer_than_two_outcomes(tmp_path):
    log = tmp_path / "results_log.jsonl"
    _write_log(log)
    decisions = load_decisions(log)
    # Provider that resolves nothing -> no decision is evaluatable.
    empty = backtest(decisions, lambda s, w, sc: (lambda k, n, p: None),
                     base_weights={"ecr": 1.0})
    assert empty.decisions_used == 0
    assert empty.hit_rate == 0.0


def test_load_decisions_parses_weights_and_close_call(tmp_path):
    rows = [
        {"ts": "2024-10-01T12:00:00+00:00", "week": 1, "scoring": "ppr",
         "weights": {"ecr": 0.6, "weather": 0.1}, "close_call": True,
         "candidates": [_cand("a", 90.0)]},
        # An older row without the fields still parses with safe defaults.
        {"ts": "2024-10-08T12:00:00+00:00", "week": 2, "scoring": "ppr",
         "candidates": [_cand("b", 50.0)]},
    ]
    log = tmp_path / "results_log.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in rows))
    decisions = load_decisions(log)
    assert decisions[0].weights == {"ecr": 0.6, "weather": 0.1}
    assert decisions[0].close_call is True
    assert decisions[1].weights == {}
    assert decisions[1].close_call is False


# --- CLI ------------------------------------------------------------------
def _args(log, **over):
    base = dict(season=None, week=None, log=log)
    base.update(over)
    return argparse.Namespace(**base)


def test_cmd_backtest_prints_report(tmp_path, capsys):
    log = tmp_path / "log.jsonl"
    outcomes = _write_log(log)
    settings = Settings(data_dir=tmp_path)
    rc = cmd_backtest(_args(log), settings, outcome_provider=_provider(outcomes))
    out = capsys.readouterr().out
    assert rc == 0
    assert "top-pick hit-rate" in out
    assert "close-call honesty" in out


def test_cmd_backtest_empty_log_errors(tmp_path):
    log = tmp_path / "empty.jsonl"
    log.write_text("")
    settings = Settings(data_dir=tmp_path)
    rc = cmd_backtest(_args(log), settings, outcome_provider=lambda *a: None)
    assert rc == 1


def test_cmd_backtest_unjoinable_errors(tmp_path):
    log = tmp_path / "log.jsonl"
    _write_log(log)
    settings = Settings(data_dir=tmp_path)
    rc = cmd_backtest(_args(log), settings,
                      outcome_provider=lambda s, w, sc: (lambda k, n, p: None))
    assert rc == 1
