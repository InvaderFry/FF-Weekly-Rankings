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
from .season import is_preseason
from .sources.base import Signal
from .sources.ecr import ECRSignal
from .sources.injury import InjurySignal
from .sources.vegas import VegasSignal


def build_signals(settings: Settings, season: Optional[int] = None,
                  preseason: Optional[bool] = None) -> list[Signal]:
    """The v1 signal set. Add a usage signal here for #7 — nothing else changes.

    Before Week 1 there is no live ECR/Vegas data, so (unless disabled via
    ``FF_PRESEASON_FILL=0``) preseason runs get the bundled sample signals
    instead of an all-``None`` blend. ``preseason`` is injectable for tests;
    ``None`` means "detect from today's date".
    """
    if preseason is None:
        preseason = is_preseason()
    if preseason and settings.preseason_fill:
        from .sources.sample import build_sample_signals
        return build_sample_signals()
    return [
        ECRSignal(api_key=settings.fantasypros_api_key, scoring=settings.scoring,
                  season=season),
        VegasSignal(api_key=settings.odds_api_key),
        InjurySignal(data_dir=settings.data_dir, enabled=settings.injury_enabled),
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

    # Sample (preseason) runs must never feed the #7 calibration log — the
    # learner would happily fit weights to made-up data.
    if any(getattr(s, "is_sample", False) for s in signals):
        log = False

    from .sources.base import unavailable_for

    signal_values = {}
    higher_is_better = {}
    for sig in signals:
        if not sig.is_available():
            # Still record an all-unavailable map so the note surfaces to the user.
            signal_values[sig.name] = unavailable_for(players, f"{sig.name} unavailable")
        else:
            try:
                signal_values[sig.name] = sig.fetch(week, players)
            except Exception:
                # A signal failing (e.g. network) must not crash the run — the
                # blend degrades to whatever other signals are available.
                signal_values[sig.name] = unavailable_for(players, f"{sig.name} fetch failed")
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
