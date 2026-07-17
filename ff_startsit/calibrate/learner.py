"""The #7 learner: fit blend weights that best predicted real outcomes.

The decision log stored each candidate's per-signal ``normalized`` (0-100) score, so
any trial weight vector can be re-blended from logged data alone — no re-fetching.
We join those re-blended scores to actual fantasy points and search the weight
simplex (pure-Python grid search, zero new deps) for the vector that best orders the
candidates the way the real results did.

Objective: **pairwise ranking concordance** — across every pair of candidates within
a decision whose actual points differ, the fraction the blend ordered correctly
(dense and stable on small samples). To keep weightings comparable, we score only
candidates that carry every tuned signal (see ``join_outcomes``), so the pair
denominator is identical for the current weights and every trial. We also report
**top-pick hit-rate** — how often the #1 candidate was the week's actual best — as
the intuitive headline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterator, Mapping, Optional, Sequence

from ..engine.blend import weighted_final
from .log_reader import Decision

# A decision reduced to its joined candidates: (normalized scores, actual points).
JoinedDecision = list[tuple[dict[str, float], float]]

# Resolve a candidate's actual points: (key, name, position) -> points or None.
OutcomeLookup = Callable[[str, str, str], Optional[float]]
# Provide the OutcomeLookup for a given (season, week, scoring), or None if missing.
OutcomeProvider = Callable[[str, int, str], Optional[OutcomeLookup]]


@dataclass
class CalibrationResult:
    signals: list[str]
    best_weights: dict[str, float]
    best_concordance: float
    current_weights: dict[str, float]
    current_concordance: float
    best_hit_rate: float
    current_hit_rate: float
    decisions_used: int
    pairs_used: int
    enough_data: bool = field(default=False)


def signals_in(decisions: Sequence[Decision]) -> list[str]:
    """The signal names that actually appear in the logged candidates, ordered.

    Known signals keep their canonical blend order; any others follow, sorted.
    """
    seen: set[str] = set()
    for d in decisions:
        for c in d.candidates:
            seen.update(c.normalized.keys())
    canonical = [s for s in ("ecr", "vegas", "injury", "weather") if s in seen]
    extra = sorted(seen - set(canonical))
    return canonical + extra


def join_outcomes(decisions: Sequence[Decision], provider: OutcomeProvider,
                  signals: Sequence[str]) -> list[JoinedDecision]:
    """Attach actual points to each candidate, keeping decisions with >=2 joined.

    Only candidates carrying *every* tuned signal in ``signals`` are kept, so the
    comparable-pair set is identical under every weight vector — without this, a
    weighting that leans on a sparse signal (e.g. a bye-week player with no Vegas
    line) would be scored on a smaller, different denominator than the others and
    look spuriously better. This trades a few partial-signal candidates (typically
    just byes) for an apples-to-apples objective.
    """
    needed = set(signals)
    joined: list[JoinedDecision] = []
    # Cache the per-(season, week, scoring) lookup so we fetch each slice once.
    cache: dict[tuple[str, int, str], Optional[OutcomeLookup]] = {}
    for d in decisions:
        slice_key = (d.season, d.week, d.scoring)
        if slice_key not in cache:
            cache[slice_key] = provider(d.season, d.week, d.scoring)
        lookup = cache[slice_key]
        if lookup is None:
            continue
        rows: JoinedDecision = []
        for c in d.candidates:
            if not needed.issubset(c.normalized):
                continue
            pts = lookup(c.key, c.name, c.position)
            if pts is None:
                continue
            rows.append((c.normalized, pts))
        if len(rows) >= 2:
            joined.append(rows)
    return joined


def concordance(joined: Sequence[JoinedDecision],
                weights: Mapping[str, float]) -> tuple[float, int]:
    """Fraction of comparable candidate pairs the weights ordered correctly.

    Returns ``(score, pairs)``. A pair is comparable when the two actual points
    differ and both candidates produce a final under ``weights``. A tie in the
    blended finals counts as half credit. ``score`` is 0.0 when no pair is comparable.
    """
    agree = 0.0
    total = 0
    for decision in joined:
        finals = [(weighted_final(norm, weights), pts) for norm, pts in decision]
        for i in range(len(finals)):
            fi, ai = finals[i]
            if fi is None:
                continue
            for j in range(i + 1, len(finals)):
                fj, aj = finals[j]
                if fj is None or ai == aj:
                    continue
                total += 1
                if fi == fj:
                    agree += 0.5
                elif (fi > fj) == (ai > aj):
                    agree += 1.0
    return (agree / total if total else 0.0), total


def hit_rate(joined: Sequence[JoinedDecision],
             weights: Mapping[str, float]) -> float:
    """Fraction of decisions whose top-scored candidate was the actual best."""
    hits = 0
    used = 0
    for decision in joined:
        scored = [(weighted_final(norm, weights), pts) for norm, pts in decision]
        scored = [(f, p) for f, p in scored if f is not None]
        if not scored:
            continue
        used += 1
        top_actual = max(p for _, p in scored)
        picked = max(scored, key=lambda fp: fp[0])
        if picked[1] == top_actual:
            hits += 1
    return hits / used if used else 0.0


def simplex(signals: Sequence[str], step: float) -> Iterator[dict[str, float]]:
    """Yield every non-negative weight vector over ``signals`` summing to 1.0.

    Enumerated on an integer grid (``n = round(1/step)`` parts) to avoid float drift;
    e.g. step 0.05 over 3 signals yields 231 vectors.
    """
    n = max(1, round(1.0 / step))
    k = len(signals)
    if k == 0:
        return

    def compositions(parts: int, total: int) -> Iterator[list[int]]:
        if parts == 1:
            yield [total]
            return
        for first in range(total + 1):
            for rest in compositions(parts - 1, total - first):
                yield [first] + rest

    for combo in compositions(k, n):
        yield {sig: round(count / n, 4) for sig, count in zip(signals, combo)}


def calibrate(decisions: Sequence[Decision], provider: OutcomeProvider, *,
              base_weights: Mapping[str, float], step: float = 0.05,
              min_pairs: int = 30) -> CalibrationResult:
    """Join logged decisions to outcomes and grid-search the best blend weights."""
    signals = signals_in(decisions)
    joined = join_outcomes(decisions, provider, signals)

    current = {s: float(base_weights.get(s, 0.0)) for s in signals}
    # ``join_outcomes`` fixed the comparable set, so ``pairs`` is the shared
    # denominator for every weighting — sound to report and to gate --write on.
    current_conc, pairs = concordance(joined, current)
    current_hr = hit_rate(joined, current)

    best_weights = dict(current)
    best_conc = current_conc
    best_hr = current_hr
    for trial in simplex(signals, step):
        conc, _ = concordance(joined, trial)
        if conc > best_conc:
            best_conc, best_weights = conc, trial
            best_hr = hit_rate(joined, trial)
        elif conc == best_conc:
            # Tie on the objective: prefer the more accurate top pick.
            hr = hit_rate(joined, trial)
            if hr > best_hr:
                best_hr, best_weights = hr, trial

    return CalibrationResult(
        signals=signals,
        best_weights=best_weights,
        best_concordance=round(best_conc, 4),
        current_weights=current,
        current_concordance=round(current_conc, 4),
        best_hit_rate=round(best_hr, 4),
        current_hit_rate=round(current_hr, 4),
        decisions_used=len(joined),
        pairs_used=pairs,
        enough_data=pairs >= min_pairs,
    )
