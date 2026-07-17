"""Bundled sample signals for preseason runs.

Before Week 1 there are no weekly ECR rankings and no Vegas lines, so a live
run scores every player ``None`` and every output shows "(no option)". When
``pipeline.build_signals`` detects preseason (and ``FF_PRESEASON_FILL`` isn't
off), it swaps in these signals instead: same names as the live ones so the
configured blend weights apply unchanged, values drawn from
``data/sample_signals.json`` and assigned to the real roster deterministically
(sorted by ``player.key``); every output carries the preseason banner so the
run is clearly labeled. No network, ever — and ``pipeline.recommend`` refuses
to log sample runs so they never pollute the #7 calibration data.
"""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Iterable

from ..models import Player, SignalValue
from .base import Signal


def _load_sample_data() -> dict:
    resource = files("ff_startsit.data").joinpath("sample_signals.json")
    return json.loads(resource.read_text())


class SampleSignal(Signal):
    """A signal that deals plausible per-position values to the actual roster."""

    is_sample = True

    def __init__(self, name: str, higher_is_better: bool,
                 by_position: dict[str, list[float]]):
        self.name = name
        self.higher_is_better = higher_is_better
        self._by_position = by_position

    def is_available(self) -> bool:
        return True

    def fetch(self, week: int, players: Iterable[Player]) -> dict[str, SignalValue]:
        players = list(players)
        out: dict[str, SignalValue] = {}
        by_pos: dict[str, list[Player]] = {}
        for p in players:
            by_pos.setdefault(p.position, []).append(p)
        for pos, group in by_pos.items():
            values = self._by_position.get(pos, [])
            # Stable order so the same roster gets the same sample lineup
            # run after run.
            # No per-value note: blend surfaces notes as flags/alerts, which
            # would mark every row. The preseason banner labels the whole run.
            for i, player in enumerate(sorted(group, key=lambda p: p.key)):
                out[player.key] = SignalValue(raw=self._value_at(values, i))
        return out

    @staticmethod
    def _value_at(values: list[float], i: int) -> float:
        """Extend past the configured list so every player gets a value."""
        if not values:
            return 50.0
        if i < len(values):
            return float(values[i])
        step = values[-1] - values[-2] if len(values) > 1 else 1.0
        return float(values[-1] + step * (i - len(values) + 1))


def build_sample_signals() -> list[Signal]:
    """One SampleSignal per entry in the bundled JSON (names match blend weights)."""
    data = _load_sample_data()
    return [
        SampleSignal(name, spec["higher_is_better"], spec["by_position"])
        for name, spec in data.items()
        if isinstance(spec, dict) and "by_position" in spec
    ]
