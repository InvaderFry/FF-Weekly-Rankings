"""Core data structures shared across the app.

These are deliberately plain dataclasses so the engine (normalize/blend) stays a
set of pure functions over simple values — easy to unit test and easy for a
future #7 optimizer to consume.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


def _fmt_raw(value: float) -> str:
    """Round a raw signal value to 2dp for display, without a trailing point/zeros.

    ``f"{value:g}"`` defaults to 6 significant figures, which is how a Vegas
    implied-total gap of 1.222222 ended up printed verbatim in a close-call
    note. No signal here carries meaningful precision past a hundredth, so
    round first, then strip the noise ``:.2f`` leaves behind (``2.0`` ->
    ``"2.00"`` -> ``"2"``). Shared by ``output.render.flat_signal_note`` and
    ``engine.blend._flag_raw_dead_heat``, the two places that print a raw gap.
    """
    text = f"{round(value, 2):.2f}".rstrip("0").rstrip(".")
    return text or "0"


@dataclass(frozen=True)
class Player:
    """A roster player, canonicalized from Sleeper.

    ``key`` (the Sleeper player id) is the join key every signal returns values
    against, so ECR and Vegas never need to agree on names — only on this id
    after matching.
    """

    key: str
    name: str
    team: Optional[str]      # standardized abbreviation (e.g. "KC"), or None on bye/FA
    position: str            # QB/RB/WR/TE/K/DEF


@dataclass(frozen=True)
class Game:
    """A single NFL game's betting line."""

    home_team: str           # standardized abbreviation
    away_team: str
    total: float             # over/under
    home_spread: float       # negative => home favored
    kickoff: Optional[datetime] = None   # from the book's commence_time, if given

    def implied_total(self, team: str) -> Optional[float]:
        """Implied points for ``team``: total/2 - team_spread/2."""
        if team == self.home_team:
            spread = self.home_spread
        elif team == self.away_team:
            spread = -self.home_spread
        else:
            return None
        return self.total / 2.0 - spread / 2.0


@dataclass(frozen=True)
class GameContext:
    """One scheduled game: who, where, when, and under what roof.

    The shared answer to "what is this player's game?", so weather forecasts the
    venue the game is actually played at rather than the player's own home
    stadium, and Vegas can tell one week's line from another.
    """

    home_team: str           # standardized abbreviation
    away_team: str
    kickoff: Optional[datetime] = None   # timezone-aware UTC
    venue_id: str = ""
    venue_name: str = ""
    indoor: bool = False                 # the feed's per-game roof flag
    neutral_site: bool = False

    def opponent(self, team: str) -> Optional[str]:
        if team == self.home_team:
            return self.away_team
        if team == self.away_team:
            return self.home_team
        return None

    def is_home(self, team: str) -> bool:
        return team == self.home_team

    def has(self, team: str) -> bool:
        return team in (self.home_team, self.away_team)


@dataclass(frozen=True)
class SignalValue:
    """One signal's reading for one player.

    ``raw`` is in the signal's native units (ECR rank, implied points, ...).
    ``available`` is False when the signal has nothing for this player (bye week,
    unmatched name, missing line) — the blender then falls back to the remaining
    signals for that player.
    """

    raw: Optional[float]
    available: bool = True
    note: str = ""


@dataclass
class PlayerScore:
    """A player's signal readings plus the blended result."""

    player: Player
    raw: dict[str, SignalValue] = field(default_factory=dict)        # signal name -> reading
    normalized: dict[str, float] = field(default_factory=dict)       # signal name -> 0..100
    final: Optional[float] = None
    flags: list[str] = field(default_factory=list)
    # Overall rest-of-season rank, only populated by the waiver pass. Never
    # enters the weekly blend or calibration corpus.
    season_rank: Optional[float] = None


@dataclass
class Recommendation:
    """Result of a rank/compare run over a candidate set of players."""

    week: int
    scoring: str
    weights: dict[str, float]
    scores: list[PlayerScore]          # ordered best -> worst by ``final``
    close_call: bool = False
    notes: list[str] = field(default_factory=list)
    #: signal name -> the raw separation below which that signal is treated as
    #: not separating players at all (``Settings.close_call_raw_gaps``, e.g. 3.0
    #: ECR ranks, 1.5 implied points). Carried on the recommendation so a
    #: renderer can answer "is this column's spread real?" without reaching back
    #: into config. Empty for callers that pass no gaps, which keeps every
    #: existing construction of this class valid.
    raw_gaps: dict[str, float] = field(default_factory=dict)

    @property
    def unranked(self) -> bool:
        """True when there was nothing to rank this candidate against.

        ``normalize.to_0_100`` is min-max *within the candidate set*, so a lone
        candidate has an empty range and every signal comes back at the midpoint.
        A table then reads ``ECR 50 | INJURY 50 | VEGAS 50 | WEATHER 50`` for a
        player whose real ECR may be TE1 — four placeholders wearing the shape of
        readings. Renderers blank the per-signal columns on this rather than
        print them.

        Deliberately a *presentation* fact, not a scoring one: ``final`` stays a
        number because ``report.build_lineup`` fills slots from it, and your only
        tight end still has to be startable.
        """
        return len([s for s in self.scores if s.final is not None]) < 2

    @property
    def lone_candidate(self) -> bool:
        """True when this section is one player and nothing else.

        Stricter than ``unranked``, deliberately. ``unranked`` is also true for a
        position holding one healthy body and two on IR — but those rows carry
        "not startable: ruled out this week", which is exactly what a roster owner
        needs to see, so that table stays. This is only the case where the section
        is a header, an empty table, a note explaining the empty table, and a
        verdict that was never in doubt: nine of the eighteen sections in a
        three-league digest, none carrying a reading. Renderers collapse it to the
        one sentence that was ever in it.

        A presentation fact like ``unranked``, for the same reason: ``final``
        stays a number because ``report.build_lineup`` fills slots from it.
        """
        return len(self.scores) == 1 and self.scores[0].final is not None

    def flat_signals(self) -> list[tuple[str, float, float]]:
        """Signals whose 0-100 column overstates the gap it is drawn from.

        ``normalize.to_0_100`` is min-max *within the candidate set* and has no
        minimum-span floor, so the best candidate is 100 and the worst is 0
        however little separates them: five backs inside half an implied point
        still render as a 0-vs-100 blowout. The blend is right to work in that
        space — it only ever needs the ordering — but a reader looking at the
        column cannot tell a real fade from a rounding artifact, and the raw
        value is the only scale that knows the difference.

        Returns ``(signal, spread, gap)`` for every signal that carries a
        configured raw gap, reads a usable raw value for at least two *scored*
        candidates, and spans no more than that gap across all of them. A
        signal with no configured gap (injury) is a bucketed status with no
        continuous scale, so it abstains here exactly as it does in
        ``blend._flag_raw_dead_heat``. Weather carries a gap (12.0 of its 0-100
        conditions score) purely for this presentational check — its blend
        weight still sits under the disagreement floor, so it cannot flag or
        veto a close call, only earn the "read with care" note this method
        drives.

        Deliberately about the whole candidate set, not the top two: the flag is
        already the dead-heat condition's job. This answers the different
        question of whether a *column* means what its numbers suggest.
        """
        scored = [s for s in self.scores if s.final is not None]
        flat: list[tuple[str, float, float]] = []
        for name, gap in sorted(self.raw_gaps.items()):
            values = []
            for s in scored:
                sv = s.raw.get(name)
                if sv is not None and sv.available and sv.raw is not None:
                    values.append(float(sv.raw))
            if len(values) < 2:
                continue
            spread = max(values) - min(values)
            if spread <= gap:
                flat.append((name, spread, gap))
        return flat
