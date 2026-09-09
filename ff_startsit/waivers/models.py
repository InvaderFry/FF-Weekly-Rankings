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

#: Order the drafted roster is listed in. A third copy of ``report.POSITION_ORDER``
#: on purpose, the same way ``output/html.py`` keeps one: this is the lowest layer
#: in the package, and importing ``report`` from here would cycle.
ROSTER_ORDER = ["QB", "RB", "WR", "TE", "K", "DEF"]


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


#: Positions a league starts exactly one of and streams week to week. Lives here
#: rather than in ``score`` because ``WaiverBundle.no_adds_at_positions`` reasons
#: about them too, and two copies of the set would let a renderer and the scorer
#: disagree about which empty rows are worth explaining. ``score`` re-exports it
#: and holds the note on why these positions are an exception to ``depth_ratio``.
STREAM_POSITIONS = frozenset({"K", "DEF"})


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
    #: Teams in the league. With ``roster_slots`` this gives starter demand per
    #: position, which is what makes a rank comparable between positions.
    team_count: Optional[int] = None
    #: flex slot name -> count, e.g. ``{"FLEX": 2}`` or ``{"SUPER_FLEX": 1}``.
    #: Kept out of ``roster_slots`` because a flex slot is not a position: nobody
    #: plays "FLEX", and it has no starter demand of its own to divide a rank by.
    #: It still decides who *starts*, which is what drop protection turns on — a
    #: superflex league's second quarterback is a weekly starter, and with no slot
    #: recording that he was surplus to ``keep_counts`` and unprotected by the
    #: lineup builder at once.
    flex_slots: dict[str, int] = field(default_factory=dict)

    @property
    def superflex(self) -> bool:
        """True when quarterbacks carry starter value beyond a single slot.

        One definition, read by ``trades.suggest_trades`` (which refuses these
        leagues) and by ``WaiverBundle.no_trades_reason`` (which explains the
        refusal). Two copies of this condition would drift into a section that
        is empty for a reason the report states incorrectly.
        """
        return bool(self.flex_slots.get("SUPER_FLEX")
                    or self.roster_slots.get("QB", 1) > 1)

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
    #: Points over the roster player this add would replace — set *only* when the
    #: two share a position. Scores are min-maxed within a position's own
    #: candidate set, so a WR's 50 and an RB's 50 are not the same 50 and the
    #: difference between them is not a number. ``None`` for a cross-position
    #: swap, which is still a legitimate roster move, just not a subtractable one.
    margin: Optional[float] = None
    drop: Optional[PlayerScore] = None
    pool: Optional[PoolPlayer] = None
    #: The ratio ``pick_adds`` actually ordered and gated this add on
    #: (``score.depth_ratio``) — positional rank over what the league starts
    #: there. The one number comparable across positions, unlike ``score.final``,
    #: which is min-maxed *within* a position and meaningless read down a mixed
    #: table. ``None`` for a target built outside ``pick_adds`` (e.g. directly in
    #: a test); renderers must tolerate that the same way they do a missing
    #: season rank.
    depth_ratio: Optional[float] = None
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
    #: How much positional depth the swap converts: the sender's depth ratio over
    #: the receiver's, so above 1.0 means you receive the shallower player. The one
    #: figure here comparable *between* ideas — ``your_gain`` is a sum of one
    #: position's 0-100 scores, so a WR-for-TE idea and an RB-for-QB idea cannot be
    #: ranked against each other by it.
    depth_gain: float = 1.0


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
    #: Whether ``suggest_trades`` actually ran. An empty ``trades`` otherwise
    #: cannot be told apart from a section that was never built (``--no-trades``,
    #: ``FF_TRADE_SUGGESTIONS=0``, or a run that could not identify your team),
    #: and "no trade is worth making" is a claim about a search that happened.
    trades_considered: bool = False
    stashes: list[StashIdea] = field(default_factory=list)
    byes: list[ByeGap] = field(default_factory=list)
    sources: list[tuple[str, str]] = field(default_factory=list)  # (author, url)
    #: Surfaced by every renderer, like ``report.Lineup.caveat`` — the honest
    #: "here is what this report could not see this week" line.
    caveat: Optional[str] = None
    #: Whole-run warning, the analogue of ``report.LeagueBundle.banner``: today,
    #: the preseason refusal. Kept distinct from ``caveat`` so "the season hasn't
    #: started" and "this league's free-agent list was unreachable" stay
    #: distinguishable to every renderer.
    banner: Optional[str] = None
    #: Your drafted roster, shown under a banner that has nothing else to say.
    #: Only populated on the preseason refusal, where the report would otherwise
    #: be a warning and nothing else — and where the roster is the one real thing
    #: the run already holds, since ``cli._get_roster`` fetched it before
    #: ``build_bundle`` was ever called. Empty before the draft, which is exactly
    #: when there is no team to show.
    roster: list[Player] = field(default_factory=list)
    #: Run-level footnotes, shared across every league in the run and rendered
    #: once in a common footer. Reach the digest and the dashboard but **not**
    #: the Discord embed, which renders ``banner`` and ``caveat`` only — so
    #: anything a reader must not miss belongs in one of those instead.
    #:
    #: Only put a note here when its text is the same for every league. The
    #: footer is deduplicated by exact string, which is what makes a *league*
    #: fact wrong to place in it: three leagues each reporting their own
    #: coverage produced three near-identical unattributed sentences at the
    #: bottom of the page, and a reader could not tell whose was whose.
    notes: list[str] = field(default_factory=list)
    #: Footnotes about *this* league, rendered inside its own section. Anything
    #: carrying a league-specific number or an outcome one league can hit while
    #: its siblings don't belongs here rather than in ``notes``.
    league_notes: list[str] = field(default_factory=list)
    #: signal name -> how many pool players it covered, from
    #: ``score.signal_coverage``. Populated every in-season run, not only the
    #: rehearsal, because an empty adds list is otherwise indistinguishable from a
    #: broken one and all three renderers assert a comparison in that case.
    coverage: dict[str, int] = field(default_factory=dict)
    #: Free agents actually fetched, the denominator ``coverage`` is counted against.
    pool_size: int = 0
    #: position -> free agents that were real add candidates, from
    #: ``score.viable_adds_by_position``. The denominator behind
    #: ``no_adds_at_positions``: it is what lets an empty RB row say whether forty
    #: ranked backs lost the comparison or none were ranked at all.
    considered_adds: dict[str, int] = field(default_factory=dict)

    def no_adds_reason(self) -> Optional[str]:
        """Why the adds section is empty — an outage, a thin read, or a quiet wire.

        One definition for all three renderers, for the same reason
        ``roster_by_position`` below is one: the digest, the dashboard and the
        Discord embed must not disagree. They already did — each carried its own
        wording of "nothing beats anyone you could drop", and ``score.has_ecr``
        gates adds *and* drops, so an ECR outage produced exactly that sentence in
        all three. It is a confident claim about a comparison that never ran.

        ``None`` means render nothing at all, because something else already says
        why the section is empty: the preseason banner, or a caveat naming what
        this report could not see. A pool fetch that failed leaves ``pool_size``
        at zero, which used to fall past both guards below and land on the quiet-
        wire sentence — so the Discord embed printed "No free-agent list was
        available for this league" and "Nothing on the wire beats anyone you could
        drop this week" in the same description.
        """
        if self.banner or self.caveat:
            return None
        ranked = self.coverage.get("ecr", 0)
        if self.pool_size and not ranked:
            # Covers ``coverage == {}`` too — a pool was fetched but no player
            # reached the scoring index, the same outage seen one step earlier.
            return ("Rankings were unavailable for every free agent, so nothing "
                    "could be compared. This is a data outage, not a quiet wire.")
        if ranked and ranked < self.pool_size:
            return ("Nothing on the wire beats anyone you could drop this week "
                    f"(ranked {ranked} of {self.pool_size} free agents).")
        return "Nothing on the wire beats anyone you could drop this week."

    def no_adds_at_positions(self) -> Optional[str]:
        """Which starting positions produced no add, and whether anyone was weighed.

        ``no_adds_reason`` only speaks when the table is *entirely* empty, so a
        report listing a kicker and a defense and nothing else said nothing about
        the positions that decide a week. Week 1 did exactly that in all three
        leagues: every table opened with a streamer, no RB or WR appeared, and a
        reader could not tell a bare wire from a bar set too high.

        Two different silences, kept apart for the same reason ``no_adds_reason``
        separates an outage from a quiet wire:

        * nobody at that position was even a candidate — the wire holds no ranked
          player there (``score.has_ecr`` gates it, and most of a pool is below
          FantasyPros' line), so nothing was compared;
        * candidates were weighed and none beat anyone you could drop.

        ``None`` when there is nothing to add to what the report already says: a
        banner or caveat is standing, the adds table is empty (``no_adds_reason``
        owns that), or every starting position produced an add. Streaming
        positions are excluded — a league starts one of each, and their absence
        from a table is not news.
        """
        if self.banner or self.caveat or not self.adds:
            return None
        slots = {pos.upper(): n for pos, n in (self.rules.roster_slots or {}).items()
                 if n and pos.upper() not in STREAM_POSITIONS}
        if not slots:
            return None
        filled = {t.score.player.position.upper() for t in self.adds}
        weighed: list[str] = []
        unranked: list[str] = []
        for pos in ROSTER_ORDER:
            if pos not in slots or pos in filled:
                continue
            n = self.considered_adds.get(pos, 0)
            (weighed if n else unranked).append(f"{pos} ({n})" if n else pos)
        if not weighed and not unranked:
            return None
        parts = []
        if weighed:
            parts.append(
                f"No add at {_join(weighed)} — free agents there were ranked and "
                "compared, but none beat anyone you could drop."
            )
        if unranked:
            parts.append(
                f"No ranked free agent was available at {_join(unranked)}, so "
                "nothing there was compared."
            )
        return " ".join(parts)

    def no_trades_reason(self) -> Optional[str]:
        """Why the trade section is empty — a refusal, an outage, or a quiet league.

        The same argument as ``no_adds_reason`` above, applied to the section
        that had no equivalent: all three renderers dropped the trade block
        silently when ``trades`` was empty, so a league this tool *declines* to
        price looked exactly like a league where nothing was worth doing. Week 1
        produced three empty sections and no word about any of them.

        ``None`` means say nothing, because something else already accounts for
        it: a banner, a caveat (which is what a rest-of-season ranking outage
        sets, and ROS ranks are what a trade is priced on), a ``league_notes``
        line naming an unidentified team, or the user having turned the section
        off.
        """
        if self.banner or self.caveat or not self.trades_considered:
            return None
        if self.rules.superflex:
            return ("Trade ideas are not offered in superflex/two-QB leagues — "
                    "quarterbacks are worth more there than the rest-of-season "
                    "ranks used to price a swap can show.")
        if (self.rules.team_count or 0) < 2:
            return ("No other team's roster could be read, so there was nobody "
                    "to trade with. This is a data outage, not a quiet league.")
        return ("No one-for-one swap makes both starting lineups better at a "
                "fair price this week.")

    def roster_by_position(self) -> list[tuple[str, list[Player]]]:
        """The drafted roster grouped for display, in lineup order.

        One definition for all three renderers, so the digest, the dashboard and
        the Discord embed cannot disagree about what the team is. A position the
        platform names something unexpected is listed after the known ones rather
        than dropped — a missing player reads as a draft that didn't come through.
        """
        groups: dict[str, list[Player]] = {}
        for player in self.roster:
            groups.setdefault(player.position, []).append(player)
        ordered = [(pos, groups.pop(pos)) for pos in ROSTER_ORDER if pos in groups]
        ordered += sorted(groups.items())
        for _, players in ordered:
            players.sort(key=lambda p: p.name)
        return ordered


def _join(items: list[str]) -> str:
    """Oxford-comma join for the short position lists the reasons above build."""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])} and {items[-1]}"
