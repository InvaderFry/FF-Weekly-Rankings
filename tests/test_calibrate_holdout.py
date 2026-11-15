"""`calibrate --write` must beat the current weights out of sample.

Four signals at step 0.05 is 1,771 trial vectors against a 30-pair floor, and
any in-sample gain above zero used to allow a write. The best of that many
vectors beats the current weights by chance on almost any corpus, and a learned
file replaces the whole weight set. So the write is gated on leave-one-week-out
validation: for each week, fit on the others and score the held-out week.
"""
import argparse
import json
import random

from ff_startsit.calibrate.learner import calibrate
from ff_startsit.calibrate.log_reader import load_decisions
from ff_startsit.cli import _build_parser, cmd_calibrate
from ff_startsit.config import Settings

CURRENT = {"ecr": 0.5, "vegas": 0.5}


def _write_log(path, outcome_for, weeks=8, decisions=3, candidates=4, seed=7):
    rng = random.Random(seed)
    outcomes, rows = {}, []
    for wk in range(1, weeks + 1):
        for d in range(decisions):
            cands = []
            for i in range(candidates):
                key = f"{wk}-{d}-{i}"
                norm = {"ecr": round(rng.uniform(0, 100), 1),
                        "vegas": round(rng.uniform(0, 100), 1)}
                cands.append({"key": key, "name": key, "team": "KC", "position": "RB",
                              "final": 0.0, "normalized": norm, "raw": {}, "flags": []})
                outcomes[key] = outcome_for(norm, rng)
            rows.append({"ts": "2024-10-01T12:00:00+00:00", "command": "rank",
                         "week": wk, "scoring": "ppr", "weights": CURRENT,
                         "close_call": False, "notes": [], "pick": "x",
                         "candidates": cands})
    path.write_text("\n".join(json.dumps(r) for r in rows))
    return outcomes


def _noise(norm, rng):
    return round(rng.uniform(0, 30), 1)


def _ecr_predicts(norm, rng):
    return round(norm["ecr"] / 4 + rng.uniform(0, 3), 1)


def _provider(outcomes):
    return lambda season, week, scoring: (lambda key, name, pos: outcomes.get(key))


def _args(log, **over):
    base = dict(season=None, week=None, step=0.05, min_pairs=30, min_decisions=5,
                min_weeks=3, min_gain=0.02, log=log, write=True)
    base.update(over)
    return argparse.Namespace(**base)


def test_pure_noise_refuses_to_write(tmp_path, capsys):
    log = tmp_path / "log.jsonl"
    outcomes = _write_log(log, _noise)
    result = calibrate(load_decisions(log), _provider(outcomes), base_weights=CURRENT)
    # The trap: in sample, the grid's best beats the current weights anyway.
    assert result.enough_data
    assert result.best_concordance > result.current_concordance

    settings = Settings(data_dir=tmp_path, weights=dict(CURRENT))
    rc = cmd_calibrate(_args(log), settings, outcome_provider=_provider(outcomes))
    assert rc == 1
    assert not settings.learned_weights_path.exists()
    err = capsys.readouterr().err
    assert "held-out" in err and "Not writing" in err


def _would_write(result, min_gain=0.02):
    return (result.enough_data and result.best_concordance > result.current_concordance
            and result.holdout_shortfall(min_gain) is None)


def test_noise_rarely_clears_the_gate_across_many_corpora(tmp_path):
    """One seed proves little either way. Over 40 pure-noise corpora, a mean
    held-out gain >= 0.02 alone let about a third write; with the consistency
    requirement 2 of these 40 do (measured, and pinned here so a loosened
    gate fails), while the in-sample gain would have written nearly all."""
    in_sample = wrote = 0
    for seed in range(40):
        log = tmp_path / f"{seed}.jsonl"
        outcomes = _write_log(log, _noise, seed=seed)
        result = calibrate(load_decisions(log), _provider(outcomes), base_weights=CURRENT)
        in_sample += result.best_concordance > result.current_concordance
        wrote += _would_write(result)
    assert in_sample >= 35
    assert wrote <= 2


def test_a_strong_signal_clears_the_gate_on_every_corpus(tmp_path):
    for seed in range(10):
        log = tmp_path / f"{seed}.jsonl"
        outcomes = _write_log(log, _ecr_predicts, seed=seed)
        result = calibrate(load_decisions(log), _provider(outcomes), base_weights=CURRENT)
        assert _would_write(result), (seed, result.holdout_shortfall(0.02))


def test_one_lucky_week_is_not_consistency(tmp_path):
    """The mean can clear --min-gain on one big week while the rest gain
    nothing; the consistency check is what refuses that."""
    from ff_startsit.calibrate.learner import CalibrationResult

    result = CalibrationResult(
        signals=["ecr"], best_weights={}, best_concordance=0.7, current_weights={},
        current_concordance=0.5, best_hit_rate=0.0, current_hit_rate=0.0,
        decisions_used=8, pairs_used=100, weeks_used=4, enough_data=True,
        holdout_concordance=0.56, holdout_current_concordance=0.5, holdout_weeks=4,
        holdout_gains=[0.24, 0.0, 0.0, 0.0])
    reason = result.holdout_shortfall(0.02)
    assert reason and "not consistent" in reason


def test_a_consistent_signal_across_weeks_writes(tmp_path, capsys):
    log = tmp_path / "log.jsonl"
    outcomes = _write_log(log, _ecr_predicts)
    settings = Settings(data_dir=tmp_path, weights=dict(CURRENT))
    rc = cmd_calibrate(_args(log), settings, outcome_provider=_provider(outcomes))
    assert rc == 0
    learned = json.loads(settings.learned_weights_path.read_text())
    assert learned["ecr"] > learned.get("vegas", 0.0)
    out = capsys.readouterr().out
    assert "held-out" in out


def test_held_out_numbers_are_reported_beside_in_sample(tmp_path):
    log = tmp_path / "log.jsonl"
    outcomes = _write_log(log, _ecr_predicts)
    result = calibrate(load_decisions(log), _provider(outcomes), base_weights=CURRENT)
    assert result.holdout_weeks == 8
    assert result.holdout_concordance is not None
    assert result.holdout_current_concordance is not None
    assert result.holdout_concordance - result.holdout_current_concordance >= 0.02


def test_min_gain_is_a_flag_defaulting_to_two_hundredths():
    args = _build_parser().parse_args(["calibrate"])
    assert args.min_gain == 0.02
    args = _build_parser().parse_args(["calibrate", "--min-gain", "0.05"])
    assert args.min_gain == 0.05
