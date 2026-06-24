import json
from pathlib import Path

from ff_startsit.models import Player
from ff_startsit.sources.injury import (
    InjurySignal,
    assign,
    is_noteworthy,
    parse_injury_rows,
    score_for_status,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _meta() -> dict:
    return json.loads((FIXTURES / "sleeper_injury.json").read_text())


def test_score_for_status_maps_designations():
    assert score_for_status(None) == 100.0
    assert score_for_status("") == 100.0
    assert score_for_status("Active") == 100.0
    assert score_for_status("Questionable") == 75.0
    assert score_for_status("Doubtful") == 35.0
    assert score_for_status("Out") == 0.0
    assert score_for_status("IR") == 0.0
    # Case-insensitive.
    assert score_for_status("out") == 0.0


def test_is_noteworthy_only_for_non_healthy():
    assert is_noteworthy("Out") is True
    assert is_noteworthy("Questionable") is True
    assert not is_noteworthy("")
    assert not is_noteworthy(None)
    assert not is_noteworthy("Active")


def test_parse_injury_rows_keeps_fantasy_positions():
    rows = parse_injury_rows(_meta())
    # OL ("Practice Squad") is dropped; the rest (4 RB + K + DEF) stay.
    positions = sorted(r.position for r in rows)
    assert "OL" not in positions
    assert positions.count("RB") == 4
    assert "K" in positions and "DEF" in positions


def test_assign_scores_and_flags_injured_players():
    players = [
        Player(key="1", name="Patrick Runner", team="KC", position="RB"),
        Player(key="2", name="Chicago Back", team="CHI", position="RB"),
        Player(key="3", name="Buffalo Rusher", team="BUF", position="RB"),
        Player(key="4", name="Doubtful Dan", team="DAL", position="RB"),
    ]
    out = assign(players, parse_injury_rows(_meta()))

    # Healthy player: full score, no flag note.
    assert out["1"].available and out["1"].raw == 100.0 and out["1"].note == ""
    # Questionable: scored down, flagged.
    assert out["2"].available and out["2"].raw == 75.0 and out["2"].note
    # Out: scored to zero, flagged.
    assert out["3"].available and out["3"].raw == 0.0 and "Out" in out["3"].note
    # Doubtful: scored down, flagged.
    assert out["4"].available and out["4"].raw == 35.0 and out["4"].note


def test_assign_unmatched_player_is_unavailable():
    players = [Player(key="9", name="Nobody Here", team="SF", position="RB")]
    out = assign(players, parse_injury_rows(_meta()))
    # No injury record -> unavailable (blend falls back to other signals), no flag.
    assert not out["9"].available
    assert out["9"].note == ""


def test_signal_disabled_marks_all_unavailable():
    sig = InjurySignal(data_dir=Path("."), enabled=False)
    players = [Player(key="1", name="Patrick Runner", team="KC", position="RB")]
    out = sig.fetch(3, players)
    assert not out["1"].available


def test_signal_fetch_uses_injected_meta_and_caches():
    class FakeClient:
        def __init__(self, meta):
            self.meta = meta
            self.calls = 0

        def load_player_metadata(self):
            self.calls += 1
            return self.meta

    client = FakeClient(_meta())
    sig = InjurySignal(data_dir=Path("."), client=client)
    players = [Player(key="3", name="Buffalo Rusher", team="BUF", position="RB")]

    out = sig.fetch(3, players)
    assert out["3"].raw == 0.0
    # Second fetch reuses the cached metadata (one network call only).
    sig.fetch(3, players)
    assert client.calls == 1
