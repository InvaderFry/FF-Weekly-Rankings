"""Orchestration: assemble signals, fetch, blend, log.

Kept separate from the CLI so the end-to-end flow is importable and testable with
fake signals.
"""

from __future__ import annotations

import sys
from typing import Iterable, Optional, Sequence

from .config import Settings
from .engine.blend import blend
from .models import Player, Recommendation
from .results_log import log_recommendation
from .season import is_preseason, is_rehearsal_window
from .sources.base import Signal
from .sources.ecr import ECRSignal
from .sources.injury import InjurySignal
from .sources.schedule import ScheduleProvider
from .sources.vegas import VegasSignal
from .sources.weather import WeatherSignal


def build_signals(settings: Settings, season: Optional[int] = None,
                  preseason: Optional[bool] = None) -> list[Signal]:
    """The v1 signal set. Add a usage signal here for #7 — nothing else changes.

    Before Week 1 there is no live ECR/Vegas data, so (unless disabled via
    ``FF_PRESEASON_FILL=0``) preseason runs get the bundled sample signals
    instead of an all-``None`` blend. ``preseason`` is injectable for tests;
    ``None`` means "detect from today's date".
    """
    if preseason is None:
        preseason = is_preseason() and not is_rehearsal_window()
    if preseason and settings.preseason_fill:
        from .sources.sample import build_sample_signals
        return build_sample_signals()
    # One provider shared by every signal that needs game context, so the week's
    # schedule is fetched once and both signals agree on who plays whom, where.
    schedule = ScheduleProvider(season=season, cache_dir=settings.data_dir)
    return [
        ECRSignal(api_key=settings.fantasypros_api_key, scoring=settings.scoring,
                  season=season),
        VegasSignal(api_key=settings.odds_api_key, schedule=schedule,
                    cache_dir=settings.data_dir),
        InjurySignal(data_dir=settings.data_dir, enabled=settings.injury_enabled),
        WeatherSignal(enabled=settings.weather_enabled, schedule=schedule),
    ]


def flex_signals(signals: Sequence[Signal]) -> Optional[list[Signal]]:
    """Swap the ECR signal for its pooled sibling, keeping the rest by reference.

    Vegas, injury and weather are position-agnostic — they key off team or the
    Sleeper id — so they work unchanged on a mixed RB/WR/TE candidate set, and
    reusing the same instances means their caches (and the Odds API quota) carry
    over: the pooled pass costs one extra HTTP call in total.

    Returns ``None`` when there is no live ECR signal to pool, which is the case
    for preseason sample runs.
    """
    out: list[Signal] = []
    found = False
    for sig in signals:
        pooled = getattr(sig, "pooled", None)
        if callable(pooled) and not getattr(sig, "is_sample", False):
            out.append(pooled())
            found = True
        else:
            out.append(sig)
    return out if found else None


def recommend(
    settings: Settings,
    players: Sequence[Player],
    week: int,
    signals: Optional[Iterable[Signal]] = None,
    command: str = "",
    log: bool = True,
    exclude_unavailable: bool = False,
    starter_count: Optional[int] = None,
) -> Recommendation:
    """Fetch every available signal for ``players`` and blend into a ranking.

    ``exclude_unavailable`` holds players a signal has ruled out (an OUT/IR-type
    designation) out of the candidate set: they keep their flags and appear
    unscored at the bottom rather than being ranked. It defaults **off** because
    the waiver pass needs those players scored — ``find_stashes`` recommends
    stashing exactly the players who cannot play this week. The start/sit
    commands, where "can he play" and "should I start him" are the same
    question, pass ``True``.

    ``starter_count``, when given, additionally flags a close call at the
    boundary of the last starting slot (rank N vs N+1), not just the overall
    top two — see ``engine.blend._flag_starter_boundary``. Callers that know
    the league's real starting slots supply it from those; ``None`` (the
    default) leaves every caller's behavior exactly as before.
    """
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

    # Same rule as the sample-data guard above, applied to the other way a run's
    # values can fail to mean what the logged row would claim: a signal that
    # served a different week than the one asked for. Only knowable after the
    # fetch, since it is the scrape itself that reveals the mismatch.
    if log and any(getattr(s, "served_wrong_week", False) for s in signals):
        log = False
        print(f"warning: not logging this week-{week} run — a signal served data "
              "for a different week, which would mislead `calibrate` and "
              "`backtest` permanently.", file=sys.stderr)

    # Which players a signal says cannot play at all. Asked of the signals rather
    # than decided here, so `engine/` and `pipeline` both stay signal-agnostic and
    # a future availability signal needs no edit in either — see Signal.rules_out.
    # ``getattr`` for the same reason ``is_sample`` and ``served_wrong_week``
    # above use it: ``signals`` is duck-typed, so a caller may pass something
    # that is not a ``Signal`` subclass and predates this hook.
    unavailable_keys: set[str] = set()
    if exclude_unavailable:
        for sig in signals:
            rules_out = getattr(sig, "rules_out", None)
            if rules_out is None:
                continue
            for pkey, sv in signal_values.get(sig.name, {}).items():
                if rules_out(sv):
                    unavailable_keys.add(pkey)

    rec = blend(
        week=week,
        scoring=settings.scoring,
        players=players,
        signal_values=signal_values,
        higher_is_better=higher_is_better,
        weights=settings.weights,
        close_call_threshold=settings.close_call_threshold,
        min_disagree_weight=settings.min_disagree_weight,
        close_call_raw_gaps=settings.close_call_raw_gaps,
        unavailable_keys=unavailable_keys,
        disagree_exempt=settings.disagree_exempt,
        dead_heat_exempt=settings.presentational_gaps,
        starter_count=starter_count,
    )

    for sig in signals:
        for detail in getattr(sig, "source_status", []):
            if detail not in rec.source_status:
                rec.source_status.append(detail)
        schedule = getattr(sig, "schedule", None)
        for detail in getattr(schedule, "source_status", []):
            if detail not in rec.source_status:
                rec.source_status.append(detail)

    # The fourth kind of run that is never logged, for the same reason as the
    # three above: the row would not mean what it claims. `calibrate` scores
    # *pairwise* concordance within one decision and `backtest` reports top-pick
    # hit-rate, so a lone candidate contributes no pair and records a pick that
    # was the only option. Week 1 logged 30 such rows out of 54: a corpus that
    # looked twice the size of the evidence in it.
    #
    # Both consumers already refuse these rows on their own — `learner
    # .join_outcomes` and `backtest` each require two joined candidates before a
    # decision counts, so the floors and the hit-rate were never actually
    # inflated by them (`tests/test_calibrate_floors.py` pins that, including for
    # the rows already on the append-only `calibration-data` branch, which this
    # guard cannot retract). Not logging them is about the corpus meaning what it
    # says: a log whose row count is twice its evidence misleads every future
    # reader of it, starting with the human deciding whether there is enough data
    # to calibrate on.
    if log and rec.unranked:
        log = False

    if log:
        log_recommendation(rec, settings.results_log_path, command=command,
                           league=settings.league_label)
    return rec
