"""Backtest: how did the tool's picks actually do — and is the close-call flag honest?

Where ``learner.calibrate`` *searches* for better weights, ``backtest`` *reports*
how the picks the tool already made turned out. It replays each logged decision
under the weights that run actually used (re-blending the stored ``normalized``
scores — never re-fetching), joins the top pick to real weekly points from the
same Sleeper outcome source calibration uses, and asks two questions:

1. **Accuracy** — how often was the #1 pick the week's actual best, and how many
   points were left on the bench when it wasn't.
2. **Honesty of the close-call flag** — the product's core promise. Split the
   decisions into *confident* (not flagged) and *close call* (flagged), and
   compare hit-rates. A well-calibrated flag should hit clearly more often on the
   confident set than on the coin-flips it warned about.

Pure over an injected ``OutcomeProvider`` (same shape as the learner's), so it
runs fully offline in tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

from ..engine.blend import weighted_final
from .learner import OutcomeProvider
from .log_reader import Decision


@dataclass
class WeekStat:
    season: str
    week: int
    decisions: int
    hits: int

    @property
    def hit_rate(self) -> float:
        return self.hits / self.decisions if self.decisions else 0.0


@dataclass
class BacktestResult:
    decisions_used: int
    candidates_joined: int
    hits: int
    avg_points_lost: float
    confident_n: int
    confident_hits: int
    close_call_n: int
    close_call_hits: int
    weeks: list[WeekStat] = field(default_factory=list)

    @property
    def hit_rate(self) -> float:
        return self.hits / self.decisions_used if self.decisions_used else 0.0

    @property
    def confident_hit_rate(self) -> float:
        return self.confident_hits / self.confident_n if self.confident_n else 0.0

    @property
    def close_call_hit_rate(self) -> float:
        return self.close_call_hits / self.close_call_n if self.close_call_n else 0.0


def backtest(decisions: Sequence[Decision], provider: OutcomeProvider, *,
             base_weights: Mapping[str, float]) -> BacktestResult:
    """Replay logged decisions against real outcomes under their own weights.

    A decision counts only when at least two of its candidates join to actual
    points (you need a contest to have a pick that could be right or wrong).
    """
    # Cache the per-(season, week, scoring) outcome lookup so each slice is fetched once.
    cache: dict[tuple[str, int, str], Optional[object]] = {}

    decisions_used = 0
    candidates_joined = 0
    hits = 0
    total_points_lost = 0.0
    confident_n = confident_hits = 0
    close_call_n = close_call_hits = 0
    week_stats: dict[tuple[str, int], WeekStat] = {}

    for d in decisions:
        slice_key = (d.season, d.week, d.scoring)
        if slice_key not in cache:
            cache[slice_key] = provider(d.season, d.week, d.scoring)
        lookup = cache[slice_key]
        if lookup is None:
            continue

        weights = d.weights or dict(base_weights)
        joined: list[tuple[float, float]] = []  # (blended final, actual points)
        for c in d.candidates:
            final = weighted_final(c.normalized, weights)
            if final is None:
                continue
            pts = lookup(c.key, c.name, c.position)
            if pts is None:
                continue
            joined.append((final, pts))

        if len(joined) < 2:
            continue

        decisions_used += 1
        candidates_joined += len(joined)
        best_actual = max(pts for _, pts in joined)
        pick_final, pick_actual = max(joined, key=lambda fp: fp[0])
        hit = pick_actual == best_actual
        total_points_lost += best_actual - pick_actual

        if hit:
            hits += 1
        if d.close_call:
            close_call_n += 1
            close_call_hits += int(hit)
        else:
            confident_n += 1
            confident_hits += int(hit)

        wk_key = (d.season, d.week)
        stat = week_stats.get(wk_key)
        if stat is None:
            stat = week_stats[wk_key] = WeekStat(d.season, d.week, 0, 0)
        stat.decisions += 1
        stat.hits += int(hit)

    avg_points_lost = round(total_points_lost / decisions_used, 2) if decisions_used else 0.0
    weeks = sorted(week_stats.values(), key=lambda s: (s.season, s.week))
    return BacktestResult(
        decisions_used=decisions_used,
        candidates_joined=candidates_joined,
        hits=hits,
        avg_points_lost=avg_points_lost,
        confident_n=confident_n,
        confident_hits=confident_hits,
        close_call_n=close_call_n,
        close_call_hits=close_call_hits,
        weeks=weeks,
    )
