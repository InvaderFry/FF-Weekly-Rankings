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
    base = dict(season=None, week=None, step=0.05, min_pairs=5, log=log, write=False)
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
