"""Orchestration: assemble signals, fetch, blend, log.

Kept separate from the CLI so the end-to-end flow is importable and testable with
fake signals.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

from .config import Settings
from .engine.blend import blend
from .models import Player, Recommendation
from .results_log import log_recommendation
from .sources.base import Signal
from .sources.ecr import ECRSignal
from .sources.vegas import VegasSignal


def build_signals(settings: Settings, season: Optional[int] = None) -> list[Signal]:
    """The v1 signal set. Add a usage signal here for #7 — nothing else changes."""
    return [
        ECRSignal(api_key=settings.fantasypros_api_key, scoring=settings.scoring,
                  season=season),
        VegasSignal(api_key=settings.odds_api_key),
    ]


def recommend(
    settings: Settings,
    players: Sequence[Player],
    week: int,
    signals: Optional[Iterable[Signal]] = None,
    command: str = "",
    log: bool = True,
) -> Recommendation:
    """Fetch every available signal for ``players`` and blend into a ranking."""
    signals = list(signals) if signals is not None else build_signals(settings)

    signal_values = {}
    higher_is_better = {}
    for sig in signals:
        if not sig.is_available():
            # Still record an all-unavailable map so the note surfaces to the user.
            from .sources.base import unavailable_for
            signal_values[sig.name] = unavailable_for(players, f"{sig.name} unavailable")
        else:
            signal_values[sig.name] = sig.fetch(week, players)
        higher_is_better[sig.name] = sig.higher_is_better

    rec = blend(
        week=week,
        scoring=settings.scoring,
        players=players,
        signal_values=signal_values,
        higher_is_better=higher_is_better,
        weights=settings.weights,
        close_call_threshold=settings.close_call_threshold,
    )

    if log:
        log_recommendation(rec, settings.results_log_path, command=command)
    return rec
