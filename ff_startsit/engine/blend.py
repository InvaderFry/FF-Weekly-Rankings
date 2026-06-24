"""Weighted ensemble of signals + honest close-call flagging.

The blend is a weighted average of each signal's normalized (0-100) score. When a
signal has no value for a player (bye, unmatched), its weight is dropped and the
remaining signals are renormalized — so a player with only ECR still gets a fair
score rather than being penalized for missing Vegas data.

A comparison is flagged "too close to call" when the top two final scores are
within a threshold, OR when the signals disagree on the ordering of the top two.
This is the product's core promise: surface the coin-flips instead of faking
confidence.
"""

from __future__ import annotations

from typing import Iterable, Mapping

from ..models import Player, PlayerScore, Recommendation, SignalValue
from .normalize import to_0_100


def blend(
    week: int,
    scoring: str,
    players: Iterable[Player],
    signal_values: Mapping[str, Mapping[str, SignalValue]],
    higher_is_better: Mapping[str, bool],
    weights: Mapping[str, float],
    close_call_threshold: float,
) -> Recommendation:
    """Combine per-signal readings into a ranked, flagged recommendation."""
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
        wsum = 0.0
        acc = 0.0
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
            w = float(weights.get(sig_name, 0.0))
            acc += w * norm
            wsum += w
        ps.final = round(acc / wsum, 2) if wsum > 0 else None
        scores.append(ps)

    # 3. Order best -> worst (players with no score sink to the bottom).
    scores.sort(key=lambda s: (s.final is not None, s.final), reverse=True)

    rec = Recommendation(week=week, scoring=scoring, weights=dict(weights), scores=scores)
    _flag_close_call(rec, normalized, close_call_threshold)
    return rec


def _flag_close_call(rec: Recommendation, normalized: Mapping[str, Mapping[str, float]],
                     threshold: float) -> None:
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

    # Signal disagreement: does any signal rank the runner-up above the leader?
    for sig_name, norms in normalized.items():
        a = norms.get(top.player.key)
        b = norms.get(second.player.key)
        if a is None or b is None:
            continue
        if b > a:
            rec.close_call = True
            rec.notes.append(
                f"Signals disagree: {sig_name} favors {second.player.name} "
                f"while the blend favors {top.player.name}."
            )
