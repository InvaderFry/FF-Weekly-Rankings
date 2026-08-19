import json

from ff_startsit.config import load_settings


DEFAULT_WEIGHTS = {"ecr": 0.60, "vegas": 0.18, "injury": 0.12, "weather": 0.10}


def _clear_weight_env(monkeypatch):
    for name in ("FF_WEIGHT_ECR", "FF_WEIGHT_VEGAS", "FF_WEIGHT_INJURY",
                 "FF_WEIGHT_WEATHER"):
        monkeypatch.delenv(name, raising=False)


def test_defaults_when_no_file_or_env(tmp_path, monkeypatch):
    _clear_weight_env(monkeypatch)
    monkeypatch.setenv("FF_DATA_DIR", str(tmp_path))
    weights = load_settings().weights
    assert weights == DEFAULT_WEIGHTS


def test_learned_weights_file_overrides_defaults(tmp_path, monkeypatch):
    _clear_weight_env(monkeypatch)
    monkeypatch.setenv("FF_DATA_DIR", str(tmp_path))
    (tmp_path / "learned_weights.json").write_text(
        json.dumps({"ecr": 0.4, "vegas": 0.4, "injury": 0.1, "weather": 0.1}))
    weights = load_settings().weights
    assert weights == {"ecr": 0.4, "vegas": 0.4, "injury": 0.1, "weather": 0.1}


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
    assert weights == DEFAULT_WEIGHTS


def test_nan_weight_env_falls_back_to_defaults(tmp_path, monkeypatch):
    """NaN slips past both sign and sum guards, so it needs its own rejection.

    Left alone it reaches ``weighted_final``, whose ``wsum > 0`` test is False
    against a NaN sum -- scoring every player ``None``.
    """
    _clear_weight_env(monkeypatch)
    monkeypatch.setenv("FF_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FF_WEIGHT_ECR", "nan")
    assert load_settings().weights == DEFAULT_WEIGHTS


def test_inf_weight_env_falls_back_to_defaults(tmp_path, monkeypatch):
    _clear_weight_env(monkeypatch)
    monkeypatch.setenv("FF_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FF_WEIGHT_VEGAS", "inf")
    assert load_settings().weights == DEFAULT_WEIGHTS


def test_non_finite_learned_weights_are_ignored(tmp_path, monkeypatch):
    """json.loads accepts bare NaN/Infinity, so the learned file is a third door."""
    _clear_weight_env(monkeypatch)
    monkeypatch.setenv("FF_DATA_DIR", str(tmp_path))
    (tmp_path / "learned_weights.json").write_text(
        '{"ecr": NaN, "vegas": 0.4}')
    weights = load_settings().weights
    assert weights["ecr"] == DEFAULT_WEIGHTS["ecr"]   # dropped, default stands
    assert weights["vegas"] == 0.4                    # the finite entry survives


def test_nan_threshold_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("FF_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FF_CLOSE_CALL_THRESHOLD", "nan")
    assert load_settings().close_call_threshold == 5.0


def test_preferred_experts_default_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("FF_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("FF_PREFERRED_EXPERTS", raising=False)
    assert load_settings().preferred_experts == ""


def test_preferred_experts_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FF_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FF_PREFERRED_EXPERTS", "101:Justin Boone,102:Jamey Eisenberg")
    assert load_settings().preferred_experts == "101:Justin Boone,102:Jamey Eisenberg"


def test_partial_learned_weights_keep_the_learned_ratio(tmp_path, monkeypatch):
    """`calibrate --write` writes only the signals it observed.

    Patching that subset into the full defaults used to leave the un-learned
    defaults in place, so a learned 70/30 summed to 1.22 and reached the blend
    as 57/25 alongside two weights the grid search never endorsed.
    """
    _clear_weight_env(monkeypatch)
    monkeypatch.setenv("FF_DATA_DIR", str(tmp_path))
    (tmp_path / "learned_weights.json").write_text(json.dumps({"ecr": 0.70, "vegas": 0.30}))
    weights = load_settings().weights
    assert weights == {"ecr": 0.70, "vegas": 0.30, "injury": 0.0, "weather": 0.0}
    assert sum(weights.values()) == 1.0


def test_full_learned_weights_are_used_verbatim(tmp_path, monkeypatch):
    _clear_weight_env(monkeypatch)
    monkeypatch.setenv("FF_DATA_DIR", str(tmp_path))
    learned = {"ecr": 0.5, "vegas": 0.2, "injury": 0.2, "weather": 0.1}
    (tmp_path / "learned_weights.json").write_text(json.dumps(learned))
    assert load_settings().weights == learned


def test_env_still_overrides_a_partial_learned_file(tmp_path, monkeypatch):
    """Documented precedence: defaults < learned file < explicit FF_WEIGHT_*."""
    _clear_weight_env(monkeypatch)
    monkeypatch.setenv("FF_DATA_DIR", str(tmp_path))
    (tmp_path / "learned_weights.json").write_text(json.dumps({"ecr": 0.70, "vegas": 0.30}))
    monkeypatch.setenv("FF_WEIGHT_WEATHER", "0.25")
    weights = load_settings().weights
    assert weights["weather"] == 0.25
    assert weights["ecr"] == 0.70


# --- waiver/trade knobs ---------------------------------------------------
def test_waiver_settings_have_defaults(monkeypatch):
    for var in ("FF_WAIVER_LIMIT", "FF_WAIVER_MAX_ADDS", "FF_MAX_TRADE_IDEAS",
                "FF_TRADE_SUGGESTIONS", "FF_COLUMN_SCRAPE"):
        monkeypatch.delenv(var, raising=False)
    s = load_settings()
    assert (s.waiver_limit, s.waiver_max_adds, s.max_trade_ideas) == (150, 8, 5)
    assert s.trade_suggestions and s.column_scrape


def test_waiver_settings_read_the_env(monkeypatch):
    monkeypatch.setenv("FF_WAIVER_LIMIT", "40")
    monkeypatch.setenv("FF_MAX_TRADE_IDEAS", "0")
    monkeypatch.setenv("FF_COLUMN_SCRAPE", "0")
    s = load_settings()
    assert s.waiver_limit == 40 and s.max_trade_ideas == 0
    assert s.column_scrape is False


def test_a_bad_waiver_limit_falls_back_loudly(monkeypatch, capsys):
    """Same fail-loud-but-graceful contract as the weights: a negative limit
    would slice the pool to nothing and silently produce a report with no adds."""
    monkeypatch.setenv("FF_WAIVER_LIMIT", "-5")
    assert load_settings().waiver_limit == 150
    assert "FF_WAIVER_LIMIT" in capsys.readouterr().out


def test_a_non_integer_waiver_limit_falls_back_loudly(monkeypatch, capsys):
    monkeypatch.setenv("FF_WAIVER_MAX_ADDS", "lots")
    assert load_settings().waiver_max_adds == 8
    assert "FF_WAIVER_MAX_ADDS" in capsys.readouterr().out


def test_the_waiver_knobs_are_not_blend_weights(monkeypatch):
    """The waiver pass adds no Signal, so CLAUDE.md's "four places" weight rule
    does not apply to it — and these settings must never reach the blend."""
    monkeypatch.setenv("FF_WAIVER_LIMIT", "40")
    monkeypatch.setenv("FF_TRADE_SUGGESTIONS", "0")
    assert set(load_settings().weights) == {"ecr", "vegas", "injury", "weather"}
