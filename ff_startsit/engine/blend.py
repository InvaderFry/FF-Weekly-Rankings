"""Weighted ensemble of signals + honest close-call flagging.

The blend is a weighted average of each signal's normalized (0-100) score. When a
signal has no value for a player (bye, unmatched), its weight is dropped and the
remaining signals are renormalized — so a player with only ECR still gets a fair
score rather than being penalized for missing Vegas data.

A comparison is flagged "too close to call" when the top two final scores are
within a threshold, OR when a signal that carries real weight disagrees
*materially* on the ordering of the top two, OR when no weighted signal separates
the two by a meaningful amount *in its own raw units*. This is the product's core
promise: surface the coin-flips instead of faking confidence — which only works if
the flag stays rare enough to mean something. A disagreement from a signal weighted
at 10% (or at 0%) is not evidence the pick is a coin-flip, so both a weight
share and a gap size have to clear a floor before it counts.

The third condition exists because the first two are blind in the case that needs
them most. ``normalize.to_0_100`` is min-max *within the candidate set*, so with two
candidates any nonzero difference becomes 0-vs-100: an ECR of 12.0 against 12.1
renders as a 100-point blowout, and ``threshold``, living in that same normalized
space, never trips. Reading the raw values is the only way to tell a tenth of a rank
from twenty. It is deliberately unanimous rather than any-of — if ECR is a dead heat
but Vegas separates them by four implied points, that is a real edge and flagging it
would be the false alarm.
"""

from __future__ import annotations

from typing import Callable, Iterable, Mapping, Optional

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
    close_call_raw_gaps: Optional[Mapping[str, float]] = None,
    unavailable_keys: Iterable[str] = (),
    disagree_exempt: Iterable[str] = (),
) -> Recommendation:
    """Combine per-signal readings into a ranked, flagged recommendation.

    ``min_disagree_weight`` is the minimum share of total blend weight a signal
    must carry before its disagreement can flag a close call (0.0 = any signal,
    the historical behavior).

    ``close_call_raw_gaps`` maps a signal name to the raw separation below which it
    is treated as not separating the top two at all. ``None`` (the default) skips
    the raw-scale check entirely, so existing callers keep their exact behavior.

    ``unavailable_keys`` names players who cannot play at all (an OUT/IR-type
    designation). They are held out of normalization and left unscored rather
    than dropped from the output: ``to_0_100`` is min-max *within the candidate
    set*, so a ruled-out player who happens to hold a signal's extreme rescales
    everyone else against a man who will not take a snap. They still appear,
    carrying their raw values and flags, and sink to the bottom on ``final is
    None`` — visible on your roster, never startable. Callers who *want* those
    players scored (the waiver pass ranks them to suggest stashes) pass nothing.

    ``disagree_exempt`` names signals whose disagreement counts regardless of
    ``min_disagree_weight``. A signal must still carry non-zero weight to flag
    anything, so a signal weighted to 0 stays silent whether exempt or not.
    """
    players = list(players)
    ruled_out = set(unavailable_keys)
    playable = [p for p in players if p.key not in ruled_out]
    # Guard: if every candidate is ruled out there is nothing to rescale against,
    # so score them normally rather than returning an empty ranking. "All my RBs
    # are out" is a real week, and an empty table answers it worse than a bad one.
    if not playable:
        ruled_out = set()

    # 1. Normalize each signal within the candidate set (ruled-out players excluded).
    normalized: dict[str, dict[str, float]] = {}
    for sig_name, values in signal_values.items():
        raw = {pk: sv.raw if sv.available else None
               for pk, sv in values.items() if pk not in ruled_out}
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
        if p.key in ruled_out:
            # Held out above, so ``normalized`` is empty and ``final`` is None.
            # Say *why* rather than leaving a blank score that reads like a
            # failed fetch — the two look identical in every renderer.
            ps.flags.append("not startable: ruled out this week")
        ps.final = weighted_final(ps.normalized, weights)
        scores.append(ps)

    # 3. Order best -> worst (players with no score sink to the bottom).
    scores.sort(key=lambda s: (s.final is not None, s.final), reverse=True)

    rec = Recommendation(week=week, scoring=scoring, weights=dict(weights), scores=scores)
    _flag_close_call(rec, normalized, close_call_threshold, min_disagree_weight,
                     close_call_raw_gaps, disagree_exempt)
    return rec


def _flag_close_call(rec: Recommendation, normalized: Mapping[str, Mapping[str, float]],
                     threshold: float, min_disagree_weight: float = 0.0,
                     raw_gaps: Optional[Mapping[str, float]] = None,
                     disagree_exempt: Iterable[str] = ()) -> None:
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

    total_weight = sum(w for w in rec.weights.values() if w > 0)
    exempt = set(disagree_exempt)

    def _share(sig_name: str) -> float:
        return (rec.weights.get(sig_name, 0.0) / total_weight) if total_weight > 0 else 0.0

    # Signal disagreement: does a signal that actually carries weight rank the
    # runner-up meaningfully above the leader? Both floors matter — without them
    # a 0.01-point flip on a zero-weight signal flags as loudly as ECR does, and
    # a flag that fires on everything tells the user nothing.
    #
    # ``exempt`` is the deliberate hole in the weight floor. Blend weight is a
    # poor proxy for how much a *status* signal's disagreement is worth: injury
    # is weighted low precisely because it is uninformative in the common case
    # (everyone healthy, so it says nothing and normalizes to a tie), which is
    # the opposite of what its weight should mean on the rare week it does
    # disagree. At 0.12 against a 0.15 floor it could never flag, so a
    # Questionable leader over a healthy runner-up passed silently — the exact
    # coin-flip this module exists to surface. The zero-weight check below is
    # kept for the case CLAUDE.md warns about: a learned-weights file that
    # zeroes a signal must silence it, exemption or not.
    for sig_name, norms in normalized.items():
        a = norms.get(top.player.key)
        b = norms.get(second.player.key)
        if a is None or b is None:
            continue
        # The runner-up must be strictly ahead *and* ahead by enough to mean
        # something. Both halves matter: `b - a < threshold` alone lets an exact
        # tie through when the threshold is 0, reporting a signal that scored the
        # two identically as "favoring" the runner-up.
        if b <= a or b - a < threshold:
            continue
        share = _share(sig_name)
        if share <= 0:
            continue  # a signal carrying no weight never flags, exempt or not
        if share < min_disagree_weight and sig_name not in exempt:
            continue  # too lightly weighted to have plausibly changed the pick
        rec.close_call = True
        rec.notes.append(
            f"Signals disagree: {sig_name} favors {second.player.name} "
            f"while the blend favors {top.player.name}."
        )

    _flag_raw_dead_heat(rec, top, second, raw_gaps or {}, min_disagree_weight, _share)


def _flag_raw_dead_heat(rec: Recommendation, top: PlayerScore, second: PlayerScore,
                        raw_gaps: Mapping[str, float], min_disagree_weight: float,
                        share_of: Callable[[str], float]) -> None:
    """Flag when nothing that carries weight separates the top two in raw units.

    Only signals that (a) carry at least ``min_disagree_weight`` of the blend,
    (b) have a configured raw gap, and (c) read a usable raw value for *both*
    players get a vote. Every voter has to call it a dead heat: one signal with a
    real separation is a real edge, and flagging that would be the false alarm the
    weight and gap floors elsewhere in this module exist to prevent.
    """
    separations: list[str] = []
    voted = False
    for sig_name, gap in raw_gaps.items():
        if share_of(sig_name) < min_disagree_weight:
            continue
        a = top.raw.get(sig_name)
        b = second.raw.get(sig_name)
        if (a is None or b is None or not a.available or not b.available
                or a.raw is None or b.raw is None):
            continue
        voted = True
        if abs(a.raw - b.raw) > gap:
            return  # this signal genuinely separates them
        separations.append(f"{sig_name} {a.raw:g} vs {b.raw:g}")

    if not voted:
        return
    rec.close_call = True
    rec.notes.append(
        f"Too close to call: nothing separates {top.player.name} from "
        f"{second.player.name} — {', '.join(separations)}."
    )
