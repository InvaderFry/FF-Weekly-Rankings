import argparse
import json

from ff_startsit.cli import cmd_calibrate
from ff_startsit.config import Settings


def _write_log(path):
    outcomes = {}
    rows = []
    for wk in range(1, 6):
        cands = []
        for i in range(4):
            key = f"{wk}-{i}"
            cands.append({
                "key": key, "name": f"P{wk}{i}", "team": "KC", "position": "RB",
                "final": 0.0,
                "normalized": {"ecr": 100.0 - i * 25.0, "vegas": i * 25.0},
                "raw": {}, "flags": [],
            })
            outcomes[key] = float(i)
        rows.append({
            "ts": "2024-10-01T12:00:00+00:00", "command": "rank", "week": wk,
            "scoring": "ppr",
            "weights": {"ecr": 0.65, "vegas": 0.20, "injury": 0.15},
            "close_call": False, "notes": [], "pick": "P0", "candidates": cands,
        })
    path.write_text("\n".join(json.dumps(r) for r in rows))
    return outcomes


def _args(log, **over):
    base = dict(season=None, week=None, step=0.05, min_pairs=5, min_decisions=5,
                min_weeks=3, log=log, write=False)
    base.update(over)
    return argparse.Namespace(**base)


def test_calibrate_reports_current_vs_learned(tmp_path, capsys):
    log = tmp_path / "log.jsonl"
    outcomes = _write_log(log)
    settings = Settings(data_dir=tmp_path)
    provider = lambda s, w, sc: (lambda k, n, p: outcomes.get(k))

    rc = cmd_calibrate(_args(log), settings, outcome_provider=provider)
    out = capsys.readouterr().out
    assert rc == 0
    assert "current  weights" in out
    assert "learned  weights" in out
    assert "vegas=1.00" in out          # the predictive signal
    # Without --write nothing is persisted.
    assert not settings.learned_weights_path.exists()


def test_calibrate_write_persists_valid_weights(tmp_path):
    log = tmp_path / "log.jsonl"
    outcomes = _write_log(log)
    settings = Settings(data_dir=tmp_path)
    provider = lambda s, w, sc: (lambda k, n, p: outcomes.get(k))

    rc = cmd_calibrate(_args(log, write=True), settings, outcome_provider=provider)
    assert rc == 0
    saved = json.loads(settings.learned_weights_path.read_text())
    assert saved["vegas"] == 1.0
    assert sum(saved.values()) == 1.0


def test_calibrate_empty_log_errors(tmp_path):
    log = tmp_path / "empty.jsonl"
    log.write_text("")
    settings = Settings(data_dir=tmp_path)
    rc = cmd_calibrate(_args(log), settings, outcome_provider=lambda *a: None)
    assert rc == 1


def test_calibrate_write_blocked_on_thin_data(tmp_path):
    log = tmp_path / "log.jsonl"
    outcomes = _write_log(log)
    settings = Settings(data_dir=tmp_path)
    provider = lambda s, w, sc: (lambda k, n, p: outcomes.get(k))
    # 30 pairs available but min_pairs set high -> refuse to write.
    rc = cmd_calibrate(_args(log, write=True, min_pairs=999), settings,
                       outcome_provider=provider)
    assert rc == 1
    assert not settings.learned_weights_path.exists()


def _parse_calibrate(argv):
    from ff_startsit.cli import _build_parser
    return _build_parser().parse_args(["calibrate", *argv])


def test_grid_step_must_be_a_usable_resolution():
    """`--step 0` used to reach `1.0 / step` — after the outcome joins had
    already gone to the network — and surface as a ZeroDivisionError. A negative
    step was worse: it silently collapsed the grid to its one-hot corners."""
    import pytest

    for bad in ("0", "0.0", "-0.05", "1.5", "abc"):
        with pytest.raises(SystemExit):
            _parse_calibrate(["--step", bad])


def test_grid_step_accepts_valid_resolutions():
    assert _parse_calibrate(["--step", "0.05"]).step == 0.05
    assert _parse_calibrate(["--step", "1"]).step == 1.0
    assert _parse_calibrate([]).step == 0.05


def _one_week_log(path):
    """One week's ranking with enough players to clear a pair floor on its own."""
    outcomes, cands = {}, []
    for i in range(9):
        key = f"1-{i}"
        cands.append({"key": key, "name": f"P{i}", "team": "KC", "position": "RB",
                      "final": 0.0, "normalized": {"ecr": 100.0 - i * 10.0,
                                                   "vegas": i * 10.0},
                      "raw": {}, "flags": []})
        outcomes[key] = float(i)
    path.write_text(json.dumps({
        "ts": "2024-10-01T12:00:00+00:00", "command": "rank", "week": 1,
        "scoring": "ppr", "weights": {"ecr": 0.65, "vegas": 0.20},
        "close_call": False, "notes": [], "pick": "P0", "candidates": cands,
    }))
    return outcomes


def test_write_refused_on_one_week_names_the_week_floor(tmp_path, capsys):
    """A refusal has to say what to go collect, not just 'not enough data'."""
    log = tmp_path / "log.jsonl"
    outcomes = _one_week_log(log)
    settings = Settings(weights={"ecr": 0.65, "vegas": 0.20}, data_dir=tmp_path)

    rc = cmd_calibrate(_args(log, write=True, min_pairs=5), settings,
                       outcome_provider=lambda s, w, sc: (lambda k, n, p: outcomes.get(k)))

    assert rc == 1
    err = capsys.readouterr().err
    assert "1 distinct week(s) (need 3)" in err
    assert not (tmp_path / "learned_weights.json").exists()


def test_evidence_floors_reject_non_positive_values():
    import pytest

    from ff_startsit.cli import _build_parser

    for flag in ("--min-weeks", "--min-decisions", "--min-pairs"):
        for bad in ("0", "-1", "two"):
            with pytest.raises(SystemExit):
                _build_parser().parse_args(["calibrate", flag, bad])


def test_evidence_floor_defaults():
    from ff_startsit.cli import _build_parser

    args = _build_parser().parse_args(["calibrate"])
    assert (args.min_pairs, args.min_decisions, args.min_weeks) == (30, 5, 3)
