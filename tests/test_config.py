import json

from ff_startsit.config import load_settings


def _clear_weight_env(monkeypatch):
    for name in ("FF_WEIGHT_ECR", "FF_WEIGHT_VEGAS", "FF_WEIGHT_INJURY"):
        monkeypatch.delenv(name, raising=False)


def test_defaults_when_no_file_or_env(tmp_path, monkeypatch):
    _clear_weight_env(monkeypatch)
    monkeypatch.setenv("FF_DATA_DIR", str(tmp_path))
    weights = load_settings().weights
    assert weights == {"ecr": 0.65, "vegas": 0.20, "injury": 0.15}


def test_learned_weights_file_overrides_defaults(tmp_path, monkeypatch):
    _clear_weight_env(monkeypatch)
    monkeypatch.setenv("FF_DATA_DIR", str(tmp_path))
    (tmp_path / "learned_weights.json").write_text(
        json.dumps({"ecr": 0.4, "vegas": 0.5, "injury": 0.1}))
    weights = load_settings().weights
    assert weights == {"ecr": 0.4, "vegas": 0.5, "injury": 0.1}


def test_env_overrides_learned_file(tmp_path, monkeypatch):
    _clear_weight_env(monkeypatch)
    monkeypatch.setenv("FF_DATA_DIR", str(tmp_path))
    (tmp_path / "learned_weights.json").write_text(
        json.dumps({"ecr": 0.4, "vegas": 0.5, "injury": 0.1}))
    monkeypatch.setenv("FF_WEIGHT_VEGAS", "0.9")  # explicit env wins for that signal
    weights = load_settings().weights
    assert weights["vegas"] == 0.9
    assert weights["ecr"] == 0.4   # file value still stands where env is silent


def test_corrupt_learned_file_falls_back_to_defaults(tmp_path, monkeypatch):
    _clear_weight_env(monkeypatch)
    monkeypatch.setenv("FF_DATA_DIR", str(tmp_path))
    (tmp_path / "learned_weights.json").write_text("{not valid json")
    weights = load_settings().weights
    assert weights == {"ecr": 0.65, "vegas": 0.20, "injury": 0.15}
