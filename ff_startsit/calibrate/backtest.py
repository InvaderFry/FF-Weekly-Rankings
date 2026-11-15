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
    #: Decisions whose pick had no points while the week's stats existed — a
    #: late scratch, usually. Excluded from every hit-rate: scoring the
    #: runner-up instead would credit (or blame) a pick nobody made.
    pick_did_not_play: int = 0

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

    The pick is decided **before** outcomes are joined: the highest blended
    final among the candidates the weights score. Joining first and then taking
    the best of whoever had points promoted the runner-up whenever the pick was
    a late scratch, scoring a decision the tool never made. A pick with no
    points while the week's stats exist (another candidate in the decision
    joined) goes to ``pick_did_not_play`` instead.
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
    pick_did_not_play = 0

    for d in decisions:
        slice_key = (d.season, d.week, d.scoring)
        if slice_key not in cache:
            cache[slice_key] = provider(d.season, d.week, d.scoring)
        lookup = cache[slice_key]
        if lookup is None:
            continue

        weights = d.weights or dict(base_weights)
        scored = [(final, c) for c in d.candidates
                  if (final := weighted_final(c.normalized, weights)) is not None]
        if len(scored) < 2:
            continue          # no contest, so no pick that could be right or wrong
        pick = max(scored, key=lambda fc: fc[0])[1]

        joined: list[tuple[float, float]] = []  # (blended final, actual points)
        pick_actual: Optional[float] = None
        for final, c in scored:
            pts = lookup(c.key, c.name, c.position)
            if pts is None:
                continue
            joined.append((final, pts))
            if c is pick:
                pick_actual = pts

        if pick_actual is None:
            # Only a scratch when the week's stats demonstrably reached this
            # decision: someone else in it joined. Nobody joining is a decision
            # that can't be joined at all (stats not posted, a name mismatch).
            if joined:
                pick_did_not_play += 1
            continue
        if len(joined) < 2:
            continue

        decisions_used += 1
        candidates_joined += len(joined)
        best_actual = max(pts for _, pts in joined)
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
        pick_did_not_play=pick_did_not_play,
    )
