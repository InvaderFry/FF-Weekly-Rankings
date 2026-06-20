from ff_startsit.engine.blend import blend
from ff_startsit.engine.normalize import NEUTRAL, to_0_100
from ff_startsit.models import Player, SignalValue


def test_to_0_100_lower_is_better():
    out = to_0_100({"a": 1.0, "b": 8.0, "c": 15.0}, higher_is_better=False)
    assert out["a"] == 100.0   # best (lowest) rank
    assert out["c"] == 0.0     # worst rank
    assert 0 < out["b"] < 100


def test_to_0_100_higher_is_better_and_skips_none():
    out = to_0_100({"a": 16.0, "b": 25.5, "c": None}, higher_is_better=True)
    assert out["b"] == 100.0
    assert out["a"] == 0.0
    assert "c" not in out


def test_to_0_100_flat_set_is_neutral():
    out = to_0_100({"a": 5.0, "b": 5.0}, higher_is_better=True)
    assert out["a"] == out["b"] == NEUTRAL


def _players():
    return [
        Player(key="1", name="Alpha", team="KC", position="RB"),
        Player(key="2", name="Bravo", team="CHI", position="RB"),
    ]


def test_blend_weights_and_ranks():
    players = _players()
    signal_values = {
        "ecr": {"1": SignalValue(1.0), "2": SignalValue(8.0)},          # Alpha better
        "vegas": {"1": SignalValue(25.5), "2": SignalValue(16.0)},      # Alpha better
    }
    rec = blend(
        week=3, scoring="ppr", players=players,
        signal_values=signal_values,
        higher_is_better={"ecr": False, "vegas": True},
        weights={"ecr": 0.75, "vegas": 0.25},
        close_call_threshold=5.0,
    )
    assert [s.player.key for s in rec.scores] == ["1", "2"]
    assert rec.scores[0].final == 100.0   # Alpha tops both signals
    assert rec.scores[1].final == 0.0
    assert rec.close_call is False


def test_blend_flags_signal_disagreement():
    players = _players()
    # ECR favors Alpha; Vegas favors Bravo -> disagreement -> close call.
    signal_values = {
        "ecr": {"1": SignalValue(1.0), "2": SignalValue(8.0)},
        "vegas": {"1": SignalValue(16.0), "2": SignalValue(25.5)},
    }
    rec = blend(
        week=3, scoring="ppr", players=players,
        signal_values=signal_values,
        higher_is_better={"ecr": False, "vegas": True},
        weights={"ecr": 0.75, "vegas": 0.25},
        close_call_threshold=1.0,
    )
    assert rec.close_call is True
    assert any("disagree" in n.lower() for n in rec.notes)


def test_blend_falls_back_to_available_signal():
    players = _players()
    signal_values = {
        "ecr": {"1": SignalValue(1.0), "2": SignalValue(8.0)},
        "vegas": {
            "1": SignalValue(None, available=False, note="bye / no team"),
            "2": SignalValue(16.0),
        },
    }
    rec = blend(
        week=3, scoring="ppr", players=players,
        signal_values=signal_values,
        higher_is_better={"ecr": False, "vegas": True},
        weights={"ecr": 0.75, "vegas": 0.25},
        close_call_threshold=5.0,
    )
    alpha = next(s for s in rec.scores if s.player.key == "1")
    # Alpha has no Vegas -> scored on ECR alone, and its flag is surfaced.
    assert "vegas" not in alpha.normalized
    assert any("bye" in f.lower() for f in alpha.flags)
    assert alpha.final is not None
