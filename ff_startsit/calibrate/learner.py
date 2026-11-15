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

#: How consistent the held-out gain must be across weeks before ``--write``:
#: mean per-week gain over its standard error. A mean alone let pure-noise
#: corpora write about a third of the time in simulation (one extreme vector
#: fits every fold the same way, so in-sample luck carries straight into the
#: held-out score); requiring the gain to show up week after week cut that to
#: about 1 in 20 while a strong real signal still wrote every time.
MIN_HOLDOUT_T = 2.0

# One logged decision reduced to the candidates that joined to a real outcome.
# It keeps ``season``/``week`` because the --write gate has to count *independent*
# evidence, and a bare list of rows cannot say which week it came from.
JoinedRow = tuple[dict[str, float], float]   # (normalized scores, actual points)


@dataclass
class JoinedDecision:
    season: str
    week: int
    rows: list[JoinedRow]

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
    weeks_used: int = 0
    enough_data: bool = field(default=False)
    #: Which evidence floors the corpus failed, phrased for the user. Empty when
    #: ``enough_data`` is True. "30 pairs but only 1 week" is actionable; a bare
    #: "not enough data" leaves the user guessing what to go collect.
    shortfalls: list[str] = field(default_factory=list)
    #: Leave-one-week-out validation (``holdout``): for each week, the weights
    #: fitted on every *other* week, scored on that week, averaged across weeks.
    #: ``None`` when no week could be held out (one week, or no pairs).
    holdout_concordance: Optional[float] = None
    #: The current weights scored on the same held-out weeks, for comparison.
    holdout_current_concordance: Optional[float] = None
    holdout_weeks: int = 0
    #: Per held-out week, the fitted weights' concordance minus the current
    #: weights' — what the consistency check reads.
    holdout_gains: list[float] = field(default_factory=list)

    def holdout_consistency(self) -> Optional[float]:
        """Mean per-week held-out gain over its standard error, or None with
        fewer than two held-out weeks. Identical positive gains every week are
        perfectly consistent (``inf``); identical non-positive ones are not."""
        gains = self.holdout_gains
        if len(gains) < 2:
            return None
        mean = sum(gains) / len(gains)
        var = sum((g - mean) ** 2 for g in gains) / (len(gains) - 1)
        if var == 0:
            return float("inf") if mean > 0 else 0.0
        return mean / (var / len(gains)) ** 0.5

    def holdout_gain(self) -> Optional[float]:
        if self.holdout_concordance is None or self.holdout_current_concordance is None:
            return None
        return round(self.holdout_concordance - self.holdout_current_concordance, 4)

    def holdout_shortfall(self, min_gain: float) -> Optional[str]:
        """Why the held-out check refuses a write, or None when it passes."""
        gain = self.holdout_gain()
        if gain is None:
            return ("no week could be held out to check the fit (needs at least "
                    "two weeks with comparable pairs)")
        if gain < min_gain:
            return (f"on weeks it was not fitted to, the learned weights scored "
                    f"{self.holdout_concordance:.3f} against the current weights' "
                    f"{self.holdout_current_concordance:.3f} (gain {gain:+.3f}, "
                    f"need +{min_gain:.3f})")
        t = self.holdout_consistency()
        if t is None:
            return ("only one week could be held out, so there is no telling a "
                    "consistent gain from one lucky week")
        if t < MIN_HOLDOUT_T:
            return (f"the held-out gain (mean {gain:+.3f}) was not consistent across "
                    f"{self.holdout_weeks} weeks: {t:.1f} standard errors above zero, "
                    f"need {MIN_HOLDOUT_T:.0f}")
        return None


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
        rows: list[JoinedRow] = []
        for c in d.candidates:
            if not needed.issubset(c.normalized):
                continue
            pts = lookup(c.key, c.name, c.position)
            if pts is None:
                continue
            rows.append((c.normalized, pts))
        if len(rows) >= 2:
            joined.append(JoinedDecision(season=d.season, week=d.week, rows=rows))
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
        finals = [(weighted_final(norm, weights), pts) for norm, pts in decision.rows]
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
        scored = [(weighted_final(norm, weights), pts) for norm, pts in decision.rows]
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
              min_pairs: int = 30, min_decisions: int = 5,
              min_weeks: int = 3) -> CalibrationResult:
    """Join logged decisions to outcomes and grid-search the best blend weights.

    The three floors gate ``--write`` together because pairs alone cannot tell one
    week from ten: a single nine-player ranking yields 36 pairs, all drawn from one
    slate, one set of injuries, one weather system. Fitting weights to that is
    fitting them to a week. ``min_weeks`` is the floor that carries the meaning —
    a whole-roster ``publish --log`` pass adds decisions quickly but only ever one
    week at a time.

    ``min_decisions`` is deliberately low: it exists to block the one-giant-ranking
    case, not to demand volume. Five weekly RB rankings are five independent reads
    on the same question — real evidence, even though it is only five rows.
    """
    signals = signals_in(decisions)
    joined = join_outcomes(decisions, provider, signals)

    current = {s: float(base_weights.get(s, 0.0)) for s in signals}
    # ``join_outcomes`` fixed the comparable set, so ``pairs`` is the shared
    # denominator for every weighting — sound to report and to gate --write on.
    current_conc, pairs = concordance(joined, current)
    current_hr = hit_rate(joined, current)
    best_weights, best_conc, best_hr = grid_best(joined, signals, step, current)
    held, held_current, gains = holdout(joined, signals, step, current)

    weeks = len({(j.season, j.week) for j in joined})
    shortfalls = []
    if pairs < min_pairs:
        shortfalls.append(f"{pairs} comparable pairs (need {min_pairs})")
    if len(joined) < min_decisions:
        shortfalls.append(f"{len(joined)} joined decisions (need {min_decisions})")
    if weeks < min_weeks:
        shortfalls.append(f"{weeks} distinct week(s) (need {min_weeks})")

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
        weeks_used=weeks,
        enough_data=not shortfalls,
        shortfalls=shortfalls,
        holdout_concordance=None if held is None else round(held, 4),
        holdout_current_concordance=None if held_current is None else round(held_current, 4),
        holdout_weeks=len(gains),
        holdout_gains=[round(g, 4) for g in gains],
    )


def grid_best(joined: Sequence[JoinedDecision], signals: Sequence[str], step: float,
              current: Mapping[str, float]) -> tuple[dict[str, float], float, float]:
    """``(weights, concordance, hit_rate)`` of the best grid vector on ``joined``.

    Starts from ``current``, so a trial must strictly beat it on concordance
    (or tie it and beat its hit-rate) to replace it.
    """
    best_weights = dict(current)
    best_conc, _ = concordance(joined, current)
    best_hr = hit_rate(joined, current)
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
    return best_weights, best_conc, best_hr


def holdout(joined: Sequence[JoinedDecision], signals: Sequence[str], step: float,
            current: Mapping[str, float]) -> tuple[Optional[float], Optional[float], list[float]]:
    """Leave-one-week-out: ``(fitted mean, current mean, per-week gains)``.

    For each week, the grid is fitted on every other week and the result scored
    on that week alone; the current weights are scored on the same week. The
    in-sample best of ~1,771 vectors beats the current weights by chance on
    almost any corpus, so only a gain that survives on weeks the fit never saw
    is evidence. Weeks are the unit because a week's decisions share one slate,
    one injury report and one weather system. Each week counts equally, and a
    week whose held-out set has no comparable pair (or that leaves nothing to
    fit on) is skipped.
    """
    weeks = sorted({(j.season, j.week) for j in joined})
    fitted_scores: list[float] = []
    current_scores: list[float] = []
    for wk in weeks:
        train = [j for j in joined if (j.season, j.week) != wk]
        test = [j for j in joined if (j.season, j.week) == wk]
        if not train:
            continue
        test_current, pairs = concordance(test, current)
        if not pairs:
            continue
        fit, _, _ = grid_best(train, signals, step, current)
        fitted_scores.append(concordance(test, fit)[0])
        current_scores.append(test_current)
    if not fitted_scores:
        return None, None, []
    n = len(fitted_scores)
    gains = [f - c for f, c in zip(fitted_scores, current_scores)]
    return sum(fitted_scores) / n, sum(current_scores) / n, gains
