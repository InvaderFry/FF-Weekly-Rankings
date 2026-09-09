"""The signal set and the weight table have to name the same signals.

CLAUDE.md's "four places" rule: a new Signal needs a weight in the defaults in
``load_settings``, in the ``Settings.weights`` field default, in the ``FF_WEIGHT_*``
env parsing, and in what ``_validate_weights`` falls back to — all while being
registered in ``pipeline.build_signals``. Prose was the only thing holding those
in sync.

The failure is quiet, which is what makes it worth a test. A signal registered in
``build_signals`` but missing from the weight table still runs — it fetches, it
spends its API quota, it lands in the results log — and then contributes exactly
0 to every blend, because ``blend`` weights by name and an absent name weighs
nothing. Nothing warns. The inverse is quieter still: a weight for a signal that
no longer exists silently shrinks every other signal's share of the total.
"""

import pytest

from ff_startsit.config import Settings, load_settings
from ff_startsit.pipeline import build_signals


def _registered_names(tmp_path) -> set[str]:
    """The live signal set — explicitly not the preseason sample fill, which is a
    demo fixture rather than the registry."""
    signals = build_signals(Settings(data_dir=tmp_path), preseason=False)
    assert not any(s.is_sample for s in signals)
    return {s.name for s in signals}


def test_every_registered_signal_has_a_weight(tmp_path):
    """`build_signals` vs the `Settings.weights` field default."""
    assert _registered_names(tmp_path) == set(Settings().weights)


def test_the_loaded_defaults_name_the_same_signals(tmp_path, monkeypatch):
    """`load_settings`' own default table is a *second* copy of that dict, and a
    signal added to one and not the other is weighted 0 on every real run while
    every `Settings()`-constructed test keeps passing."""
    monkeypatch.setenv("FF_DATA_DIR", str(tmp_path))
    assert set(load_settings().weights) == _registered_names(tmp_path)
    assert load_settings().weights == Settings().weights


def test_every_signal_has_an_env_override(tmp_path, monkeypatch):
    """The documented precedence ends in `FF_WEIGHT_*`, so a signal the env
    parsing forgot cannot be tuned or disabled without editing the source."""
    monkeypatch.setenv("FF_DATA_DIR", str(tmp_path))
    for name in sorted(_registered_names(tmp_path)):
        monkeypatch.setenv(f"FF_WEIGHT_{name.upper()}", "0.37")
        assert load_settings().weights[name] == pytest.approx(0.37), (
            f"FF_WEIGHT_{name.upper()} is not parsed by load_settings"
        )
        monkeypatch.delenv(f"FF_WEIGHT_{name.upper()}")


def test_the_validation_fallback_covers_every_signal(tmp_path, monkeypatch):
    """`_validate_weights` rejects an all-zero set and falls back to defaults.
    That fallback is a signal set of its own: if it omitted one, bad config would
    degrade into a blend permanently missing a signal rather than into the
    documented defaults."""
    monkeypatch.setenv("FF_DATA_DIR", str(tmp_path))
    for name in _registered_names(tmp_path):
        monkeypatch.setenv(f"FF_WEIGHT_{name.upper()}", "0")

    weights = load_settings().weights                  # all-zero -> defaults
    assert set(weights) == _registered_names(tmp_path)
    assert weights == Settings().weights
    assert sum(weights.values()) > 0


def test_the_weights_are_a_usable_blend(tmp_path):
    """Cheap guards on the table itself: a negative or all-zero shipped default
    is what `_validate_weights` exists to reject at runtime, and shipping one
    would mean the defaults never survive their own validation."""
    weights = Settings().weights
    assert weights, "no signal carries any blend weight"
    assert all(w >= 0 for w in weights.values())
    assert sum(weights.values()) == pytest.approx(1.0)


def test_the_raw_gap_floors_name_only_real_signals(tmp_path):
    """`close_call_raw_gaps` is deliberately a *subset* — injury is a bucketed
    status with no continuous scale, so it stays absent and abstains — but a
    floor keyed to a signal that does not exist is a veto that can never fire,
    which reads as a working guard. Weather carries a gap now (PR2's fix for
    the presentational blind spot in `Recommendation.flat_signals`), but its
    0.10 blend weight still can't clear `min_disagree_weight`, so the gap can
    only feed that presentational note, never the flag itself."""
    assert set(Settings().close_call_raw_gaps) <= _registered_names(tmp_path)
    assert "injury" not in Settings().close_call_raw_gaps
    assert "weather" in Settings().close_call_raw_gaps
