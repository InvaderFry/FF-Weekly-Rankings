"""Weighted ensemble of signals + honest close-call flagging.

The blend is a weighted average of each signal's normalized (0-100) score. When a
signal has no value for a player (bye, unmatched), its weight is dropped and the
remaining signals are renormalized — so a player with only ECR still gets a fair
score rather than being penalized for missing Vegas data.

A comparison is flagged "too close to call" when the top two final scores are
within a threshold, OR when a signal that carries real weight disagrees
*materially* on the ordering of the top two. This is the product's core promise:
surface the coin-flips instead of faking confidence — which only works if the
flag stays rare enough to mean something. A disagreement from a signal weighted
at 10% (or at 0%) is not evidence the pick is a coin-flip, so both a weight
share and a gap size have to clear a floor before it counts.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Optional

from ..models import Player, PlayerScore, Recommendation, SignalValue
from .normalize import to_0_100


def weighted_final(normalized: Mapping[str, float],
                   weights: Mapping[str, float]) -> Optional[float]:
    """Blend one player's normalized (0-100) signal scores into a final score.

    A weighted average over only the signals present in ``normalized`` — a missing
    signal (bye, unmatched) is simply absent, and the remaining weights re-normalize
    so the player is never penalized for missing data. Returns ``None`` when no
    weighted signal is available. This is the single source of truth shared by the
    live blend and the #7 calibrator, which re-blends logged ``normalized`` values
    under trial weights.
    """
    acc = 0.0
    wsum = 0.0
    for sig_name, norm in normalized.items():
        w = float(weights.get(sig_name, 0.0))
        acc += w * norm
        wsum += w
    return round(acc / wsum, 2) if wsum > 0 else None


def blend(
    week: int,
    scoring: str,
    players: Iterable[Player],
    signal_values: Mapping[str, Mapping[str, SignalValue]],
    higher_is_better: Mapping[str, bool],
    weights: Mapping[str, float],
    close_call_threshold: float,
    min_disagree_weight: float = 0.0,
) -> Recommendation:
    """Combine per-signal readings into a ranked, flagged recommendation.

    ``min_disagree_weight`` is the minimum share of total blend weight a signal
    must carry before its disagreement can flag a close call (0.0 = any signal,
    the historical behavior).
    """
    players = list(players)

    # 1. Normalize each signal within the candidate set.
    normalized: dict[str, dict[str, float]] = {}
    for sig_name, values in signal_values.items():
        raw = {pk: sv.raw if sv.available else None for pk, sv in values.items()}
        normalized[sig_name] = to_0_100(raw, higher_is_better.get(sig_name, True))

    # 2. Build a PlayerScore per player.
    scores: list[PlayerScore] = []
    for p in players:
        ps = PlayerScore(player=p)
        for sig_name in signal_values:
            sv = signal_values[sig_name].get(p.key, SignalValue(raw=None, available=False))
            ps.raw[sig_name] = sv
            # Surface any note as a flag — whether the value is missing (bye,
            # unmatched) or present but noteworthy (e.g. an injury designation).
            if sv.note:
                ps.flags.append(f"{sig_name}: {sv.note}")
            norm = normalized.get(sig_name, {}).get(p.key)
            if norm is None:
                continue
            ps.normalized[sig_name] = norm
        ps.final = weighted_final(ps.normalized, weights)
        scores.append(ps)

    # 3. Order best -> worst (players with no score sink to the bottom).
    scores.sort(key=lambda s: (s.final is not None, s.final), reverse=True)

    rec = Recommendation(week=week, scoring=scoring, weights=dict(weights), scores=scores)
    _flag_close_call(rec, normalized, close_call_threshold, min_disagree_weight)
    return rec


def _flag_close_call(rec: Recommendation, normalized: Mapping[str, Mapping[str, float]],
                     threshold: float, min_disagree_weight: float = 0.0) -> None:
    scored = [s for s in rec.scores if s.final is not None]
    if len(scored) < 2:
        return
    top, second = scored[0], scored[1]

    if abs(top.final - second.final) <= threshold:
        rec.close_call = True
        rec.notes.append(
            f"Too close to call: {top.player.name} ({top.final}) vs "
            f"{second.player.name} ({second.final}) within {threshold} pts."
        )

    # Signal disagreement: does a signal that actually carries weight rank the
    # runner-up meaningfully above the leader? Both floors matter — without them
    # a 0.01-point flip on a zero-weight signal flags as loudly as ECR does, and
    # a flag that fires on everything tells the user nothing.
    total_weight = sum(w for w in rec.weights.values() if w > 0)
    for sig_name, norms in normalized.items():
        a = norms.get(top.player.key)
        b = norms.get(second.player.key)
        if a is None or b is None:
            continue
        if b - a < threshold:
            continue  # runner-up isn't ahead, or not by enough to mean anything
        share = (rec.weights.get(sig_name, 0.0) / total_weight) if total_weight > 0 else 0.0
        if share < min_disagree_weight:
            continue  # too lightly weighted to have plausibly changed the pick
        rec.close_call = True
        rec.notes.append(
            f"Signals disagree: {sig_name} favors {second.player.name} "
            f"while the blend favors {top.player.name}."
        )
