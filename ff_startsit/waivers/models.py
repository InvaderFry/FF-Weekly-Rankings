"""Data structures for the waiver/trade pass.

Plain frozen dataclasses in the spirit of ``models.py`` — the scoring and trade
logic stay pure functions over these, and every renderer consumes the same
``WaiverBundle`` so one pass per league feeds markdown, HTML and Discord alike
(the same contract ``report.LeagueBundle`` has on the start/sit side).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..models import Player, PlayerScore

#: ``LeagueRules.acquisition_type`` values. "unknown" is a real answer, not a
#: failure: ESPN and Sleeper both hide this behind optional settings blobs, and
#: rendering a confident FAAB bid for a priority league is worse than rendering
#: no bid at all.
ACQ_FAAB = "faab"
ACQ_PRIORITY = "priority"
ACQ_UNKNOWN = "unknown"


@dataclass(frozen=True)
class FantasyTeam:
    """One team in the league — yours or a potential trade partner.

    ``players`` is the same canonical ``Player`` type the roster providers
    return, keyed identically, so every team's players can be dropped into the
    same scoring candidate set as your own.
    """

    team_id: str
    name: str
    players: tuple[Player, ...] = ()
    is_mine: bool = False
    faab_spent: Optional[float] = None
    waiver_priority: Optional[int] = None


@dataclass(frozen=True)
class LeagueRules:
    """How acquisitions work in one league, as far as we could tell.

    Everything is optional because both platforms expose it inconsistently, and
    a missing rule must degrade the report rather than break it.
    """

    acquisition_type: str = ACQ_UNKNOWN
    faab_budget: Optional[float] = None
    #: position -> number of starting slots, when the platform tells us.
    roster_slots: dict[str, int] = field(default_factory=dict)

    def faab_remaining(self, spent: Optional[float]) -> Optional[float]:
        """Budget left for a team, or None when we don't know the budget."""
        if self.faab_budget is None:
            return None
        return max(0.0, self.faab_budget - (spent or 0.0))


@dataclass(frozen=True)
class PoolPlayer:
    """A free agent plus the platform metadata that colors a bid.

    ``percent_owned`` (ESPN) and ``trending_adds`` (Sleeper) are the two "how
    contested is this claim" reads the platforms give away for free; both are
    optional and neither is required for the player to be recommended.
    """

    player: Player
    percent_owned: Optional[float] = None
    trending_adds: Optional[int] = None
    injury_status: str = ""


@dataclass
class WaiverTarget:
    """One recommended add, with the drop it is paired to."""

    score: PlayerScore
    #: 0-100 margin over the roster player this add would replace.
    margin: float
    drop: Optional[PlayerScore] = None
    pool: Optional[PoolPlayer] = None
    #: Average FantasyPros rank across the preferred journalists, when ranked.
    journalist_avg: Optional[float] = None
    mentions: tuple["ColumnMention", ...] = ()
    #: Rendered bid advice — a FAAB percentage or priority-claim language.
    bid: str = ""
    reasons: tuple[str, ...] = ()


@dataclass
class DropCandidate:
    """A roster player safe to cut, and why."""

    score: PlayerScore
    reason: str = ""


@dataclass
class TradeIdea:
    """A concrete two-sided swap with a named partner."""

    partner: str
    you_send: tuple[PlayerScore, ...]
    you_get: tuple[PlayerScore, ...]
    your_gain: float
    their_gain: float
    rationale: str = ""


@dataclass(frozen=True)
class ColumnMention:
    """A named player found in one writer's weekly waiver column."""

    author: str
    url: str
    player_key: str
    snippet: str = ""


@dataclass
class StashIdea:
    """A speculative hold: hurt, suspended, or a starter's handcuff."""

    score: PlayerScore
    reason: str


@dataclass
class ByeGap:
    """A week where a position has too few healthy starters available."""

    week: int
    position: str
    available: int
    needed: int


@dataclass
class WaiverBundle:
    """One league's fully-built waiver/trade report.

    The analogue of ``report.LeagueBundle``: every renderer takes this and
    nothing else, so the markdown digest, the dashboard page and the Discord
    embed can never disagree about what the week's advice was.
    """

    label: str
    scoring: str
    week: int
    rules: LeagueRules = field(default_factory=LeagueRules)
    adds: list[WaiverTarget] = field(default_factory=list)
    drops: list[DropCandidate] = field(default_factory=list)
    trades: list[TradeIdea] = field(default_factory=list)
    stashes: list[StashIdea] = field(default_factory=list)
    byes: list[ByeGap] = field(default_factory=list)
    sources: list[tuple[str, str]] = field(default_factory=list)  # (author, url)
    #: Surfaced by every renderer, like ``report.Lineup.caveat`` — the honest
    #: "here is what this report could not see this week" line.
    caveat: Optional[str] = None
    notes: list[str] = field(default_factory=list)
