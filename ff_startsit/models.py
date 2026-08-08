"""Core data structures shared across the app.

These are deliberately plain dataclasses so the engine (normalize/blend) stays a
set of pure functions over simple values — easy to unit test and easy for a
future #7 optimizer to consume.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


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


@dataclass
class Recommendation:
    """Result of a rank/compare run over a candidate set of players."""

    week: int
    scoring: str
    weights: dict[str, float]
    scores: list[PlayerScore]          # ordered best -> worst by ``final``
    close_call: bool = False
    notes: list[str] = field(default_factory=list)
