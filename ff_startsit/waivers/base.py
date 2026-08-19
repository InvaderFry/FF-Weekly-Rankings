"""The league-view seam: seeing the whole league, not just your team.

``roster/base.py:RosterProvider`` has exactly one abstract method
(``get_roster_players``) and three implementations. Adding abstract methods
there would break ``ManualProvider`` — a hand-edited CSV has no league behind it
and never will. So the waiver/trade capability is a *separate, optional* ABC
that ESPN and Sleeper implement alongside ``RosterProvider``, and the CLI probes
for with ``isinstance``: a league whose source can't see a player pool is
skipped with a clear message, not an exception.

Implementations must degrade the same way the rest of the app does: a failed
rules or free-agent fetch warns and returns an empty/default value rather than
raising, so one unreachable endpoint costs a section of the report, not the run.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Player
from .models import FantasyTeam, LeagueRules, PoolPlayer


class LeagueViewProvider(ABC):
    """Optional capability mixin: read the league's teams and free agents."""

    @abstractmethod
    def get_league_teams(self) -> list[FantasyTeam]:
        """Every team in the league, with ``is_mine`` set on exactly one.

        Returns ``[]`` when the platform can't tell us — the trade section is
        then omitted rather than guessed at.
        """

    @abstractmethod
    def get_free_agents(self, week: int, limit: int = 150) -> list[PoolPlayer]:
        """The addable player pool, best first.

        "Best first" is the platform's own ordering (ESPN's percent-owned
        descending, Sleeper's ``search_rank``) — the point of ``limit`` is to
        take the players anyone might actually claim rather than every inactive
        third-stringer in the league's database.
        """

    def get_league_rules(self) -> LeagueRules:
        """FAAB vs. rolling priority, budget, starting slots — all best-effort."""
        return LeagueRules()

    def supports_league_view(self) -> bool:
        return True


def pool_players(pool: list[PoolPlayer]) -> list[Player]:
    """Unwrap a pool into plain ``Player`` objects for the scoring pass."""
    return [pp.player for pp in pool]
