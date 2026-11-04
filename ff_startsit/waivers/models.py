"""Data structures for the waiver/trade pass.

Plain frozen dataclasses in the spirit of ``models.py`` — the scoring and trade
logic stay pure functions over these, and every renderer consumes the same
``WaiverBundle`` so one pass per league feeds markdown, HTML and Discord alike
(the same contract ``report.LeagueBundle`` has on the start/sit side).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple, Optional, Sequence

from ..models import Player, PlayerScore
from ..data_status import DataStatus

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
    #: Keys of the players parked in an IR/reserve slot. On both platforms that
    #: slot sits outside the roster limit, so cutting one frees an IR slot and
    #: **not** the bench spot an add needs — which is why ``build_bundle`` never
    #: pairs one with an add, and lists him as an IR-only cut instead. Empty when
    #: the platform didn't say, which degrades to the old behaviour.
    reserve: frozenset[str] = frozenset()


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
    #: Set when this add streams for a starting K/DEF on bye (see
    #: ``DropCandidate.bye_stream``); ``add_reasons`` says so.
    bye_stream: bool = False

    @property
    def drop_label(self) -> str:
        """The Drop column: the player's name, marked when he is on bye. The
        adds table hides ``reasons[0]`` because it repeats this column, so the
        bye has to be said here or it is said nowhere."""
        if self.drop is None:
            return "—"
        return f"{self.drop.player.name} (on bye)" if self.bye_stream else self.drop.player.name


@dataclass
class DropCandidate:
    """A roster player safe to cut, and why."""

    score: PlayerScore
    reason: str = ""
    #: A starting K/DEF whose team is on bye this week, with no other body at his
    #: position playing. FantasyPros doesn't rank a player on bye, so he has no
    #: ECR and the blend has nothing to compare an add against — but the slot is
    #: certain to score zero, so any ranked, playing free agent at his position is
    #: an upgrade *for this week*. ``_upgrade_over`` reads this flag.
    bye_stream: bool = False
    #: In an IR/reserve slot: listed as a cut, never paired with an add, because
    #: dropping him frees an IR slot rather than a roster spot.
    on_reserve: bool = False


@dataclass
class StreamRow:
    """One kicker or defense in the K/DEF comparison table.

    ``ranks`` maps an expert's column header (``CBS``, ``Boone``) to his
    positional rank this week, ``None`` where he didn't rank the player. Display
    data: what the table shows beside the decision ``status`` names.
    """

    player: Player
    mine: bool
    final: Optional[float] = None
    ecr: Optional[float] = None
    ranks: dict[str, Optional[float]] = field(default_factory=dict)
    status: str = ""

    def cells(self, columns: Sequence[str]) -> list[str]:
        """Player, Team, Score, FP, each expert, This tool — one definition for
        the digest and the dashboard, so the two tables cannot disagree."""
        pos = "DST" if self.player.position == "DEF" else self.player.position

        def rank(r: Optional[float]) -> str:
            return "—" if r is None else f"{pos}{r:g}"
        return [self.player.name, self.player.team or "FA",
                "—" if self.final is None else f"{self.final:.1f}",
                rank(self.ecr), *[rank(self.ranks.get(c)) for c in columns],
                self.status or "—"]


#: The K/DEF table's explanation, shared by the renderers.
STREAM_TABLE_NOTE = ("Score is this tool's blend within the position. FP, CBS and Boone "
                     "are this week's positional ranks; a swap is held back when more of "
                     "the experts who ranked either player prefer the one you have.")


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
    #: Adds that clear the same bar as ``adds`` but had no droppable body left to
    #: pair with — ``score.pick_alternates``. Rendered as a separate "Also
    #: consider adding" section with no Drop column, because there is no drop:
    #: ``WaiverTarget.drop`` and ``margin`` are both ``None`` here. Kept out of
    #: ``adds`` deliberately — the Discord renderer emits a claim instruction per
    #: ``adds`` entry ("drop X for Y"), and these carry no such instruction.
    alternates: list[WaiverTarget] = field(default_factory=list)
    drops: list[DropCandidate] = field(default_factory=list)
    trades: list[TradeIdea] = field(default_factory=list)
    #: Whether ``suggest_trades`` actually ran. An empty ``trades`` otherwise
    #: cannot be told apart from a section that was never built (``--no-trades``,
    #: ``FF_TRADE_SUGGESTIONS=0``, or a run that could not identify your team),
    #: and "no trade is worth making" is a claim about a search that happened.
    trades_considered: bool = False
    stashes: list[StashIdea] = field(default_factory=list)
    #: How many free agents were weighed as stashes before the gates
    #: (``score.stash_candidates``). The denominator ``no_stashes_reason`` needs,
    #: for the same reason ``pool_size`` is the one ``no_adds_reason`` needs.
    stash_pool: int = 0
    byes: list[ByeGap] = field(default_factory=list)
    #: The Tuesday expert board (``waivers/board.py``): every free agent a
    #: writer named, each writer's reading side by side. Display only — it
    #: changes no add, drop or bid.
    expert_rows: list = field(default_factory=list)
    #: The boards behind ``expert_rows``, in column order, carrying each
    #: writer's unavailable reason when his article couldn't be read.
    expert_boards: list = field(default_factory=list)
    sources: list[tuple[str, str]] = field(default_factory=list)  # (author, url)
    #: Surfaced by every renderer, like ``report.Lineup.caveat`` — the honest
    #: "here is what this report could not see this week" line.
    caveat: Optional[str] = None
    #: Whole-run warning, the analogue of ``report.LeagueBundle.banner``: today,
    #: the preseason refusal. Kept distinct from ``caveat`` so "the season hasn't
    #: started" and "this league's free-agent list was unreachable" stay
    #: distinguishable to every renderer.
    banner: Optional[str] = None
    #: Which kind of run produced this: ``"live"``, ``"rehearsal"`` (preseason
    #: by the calendar, live signals) or ``"preseason"`` (the refusal). Carried
    #: into the retry marker, because with no writer board read all three share
    #: a season, week and read list, and a refusal or rehearsal already posted
    #: for Week 1 would otherwise suppress the live Week 1 report.
    mode: str = "live"
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
    #: How many of your players an add could have been paired with (skill drops
    #: plus K/DEF streaming swaps). ``0`` means the comparison behind "nothing
    #: beats anyone you could drop" never ran — there was nobody to drop — and
    #: ``no_adds_reason`` must say that instead. ``None`` (not computed, e.g. a
    #: bundle built by hand) keeps the older wording.
    drop_pool: Optional[int] = None
    #: Adds the blend would have made that the writers didn't back, as
    #: "Name (POS): reason" lines (``endorse.Endorsements``). Listed rather than
    #: silently dropped, so a veto is always visible.
    held_back: list[str] = field(default_factory=list)
    #: position -> your K/DEF and the best free agents there, with this week's
    #: CBS and Boone ranks side by side. Empty when neither list was configured.
    stream_rows: dict[str, list[StreamRow]] = field(default_factory=dict)
    #: The expert column headers ``stream_rows`` carries, in order.
    stream_columns: list[str] = field(default_factory=list)
    #: The weekly K/DEF boards behind ``stream_rows``, kept so the retry marker
    #: can record which lists were read (``board.weekly_read``).
    weekly_boards: list = field(default_factory=list)
    #: FantasyPros' own statement about the weekly consensus the pass ran on:
    #: the fewest experts in any position's list, and whether every page said
    #: it was the requested week (``None``: couldn't tell). See ``freshness``.
    ecr_experts: Optional[int] = None
    ecr_week_ok: Optional[bool] = None
    #: True when ``ecr_experts`` reached ``FF_ECR_MIN_EXPERTS``; recorded in the
    #: retry marker so a Tuesday retry that finds a fuller consensus republishes.
    ecr_full: bool = False
    #: A plain note (not ``caveat``, which suppresses the empty-section
    #: explanations) saying FantasyPros' consensus is thin or on the wrong week.
    freshness: Optional[str] = None
    #: Players in an IR/reserve slot who could be cut. Never paired with an add:
    #: dropping one frees an IR slot, not the roster spot an add needs.
    ir_drops: list[DropCandidate] = field(default_factory=list)

    data_status: Optional["DataStatus"] = None
    recs: dict = field(default_factory=dict)
    #: Your roster on Richard's and Boone's trade charts (a ``values.ValueSheet``),
    #: attached by ``cmd_waivers`` when ``FF_ANALYSTS`` enables either writer.
    #: Display only, like every board: it prices the drop table and fills the
    #: "My roster" table, and changes no add, drop or bid.
    values: object = None

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
        if self.drop_pool == 0:
            # AndyLOT, Week 9: no drop table at all, and the sentence below
            # claimed a comparison against it. With nobody droppable nothing was
            # compared, so say who is holding every spot instead.
            return ("Nobody on your roster could be dropped for an add this week: "
                    "your starters, your top rest-of-season players, players "
                    "without a season rank and anyone on IR are all protected, "
                    "so no free agent was compared.")
        if ranked and ranked < self.pool_size:
            return ("Nothing on the wire beats anyone you could drop this week "
                    f"(ranked {ranked} of {self.pool_size} free agents).")
        return "Nothing on the wire beats anyone you could drop this week."

    def no_stashes_reason(self) -> Optional[str]:
        """Why the stash section is empty — a quiet wire, or a gate that held.

        The same argument as ``no_adds_reason``, one section over. ``find_stashes``
        wants an OUT/IR/PUP/SUS player who still has an NFL team *and* a
        rest-of-season rank, or a ranked player on bye — deliberately tight, since
        this app has no return-date model and a missing ROS rank is not evidence
        anyone plays again. Tight gates empty the section often, and an empty
        section rendered as nothing at all: the digest and the dashboard both drop
        the heading entirely, so "nobody is shelved this week" and "four shelved
        players were weighed and none could be vouched for" arrived as the same
        blank space. That is the distinguishable-failures-rendering-identically
        shape ``no_adds_reason`` and ``no_trades_reason`` each already fixed.

        ``None`` means render nothing, on the same two conditions as the others: a
        banner or a caveat is standing and already explains the silence. (Discord
        has never carried a stash section, so unlike ``no_adds_reason`` this is
        shared by two renderers, not three.)
        """
        if self.banner or self.caveat or self.stashes:
            return None
        if not self.stash_pool:
            return ("No free agent is shelved or on bye this week, so there was "
                    "nothing to stash.")
        return (f"{self.stash_pool} shelved or bye-week free agents were weighed, "
                "and none cleared the bar: a stash needs an NFL team and a "
                "rest-of-season rank saying he plays again.")

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
        # Alternates count as filled: a receiver in the "Also consider" section
        # did beat someone you could drop, so "none beat anyone you could drop"
        # would be false about the position he is standing in.
        filled = {t.score.player.position.upper()
                  for t in list(self.adds) + list(self.alternates)}
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

    def moves(self) -> list["Move"]:
        """The league's report in a few plain sentences, as ``Move`` lines.

        The full report runs to dozens of rows per league, with the actual
        instruction ("claim Coleman, drop Charbonnet") starting several screens
        down. This repeats what the sections below already decided, in the
        order a reader acts on it: claims, then what didn't fit, then what was
        held back, trades, stashes and bye-week holes.

        It is a **restatement, never a second decision**: every sentence is
        templated from a field the tables render, so the summary cannot claim
        anything the page beneath it doesn't show, and it stays testable
        offline. An empty list means there is nothing to restate: the preseason
        refusal (whose banner explains itself) or a report with no section
        filled. ``Move.kind`` lets a renderer leave out a line it already shows
        in full elsewhere (Discord keeps its own trade section).
        """
        out: list[Move] = []
        for t in self.adds:
            p = t.score.player
            action = (f"Claim {p.name} ({p.position}), drop {t.drop_label}."
                      if t.drop is not None else f"Claim {p.name} ({p.position}).")
            detail = []
            if t.bid:
                detail.append(_sentence(t.bid))
            named = next((r for r in t.reasons if r.startswith("named this week by ")), None)
            if named:
                detail.append(_sentence(named))
            out.append(Move("claim", action, " ".join(detail)))
        if not self.adds:
            reason = self.no_adds_reason()
            if reason:
                out.append(Move("claim", "No claims this week.", reason))
        if self.alternates:
            out.append(Move("alternate", "Also worth adding if you free a spot:",
                        _name_list([f"{t.score.player.name} ({t.score.player.position})"
                                    for t in self.alternates]) + "."))
        if self.held_back:
            # "Name (POS): why" lines (build.py); one sentence per distinct why,
            # so three kickers held back for the same reason read as one line.
            by_reason: dict[str, list[str]] = {}
            for line in self.held_back:
                who, sep, why = line.partition(": ")
                by_reason.setdefault(why if sep else "", []).append(who if sep else line)
            for why, who in by_reason.items():
                out.append(Move("held_back", "Held back:",
                                _name_list(who) + (f" — {why}." if why else ".")))
        if self.trades:
            idea = self.trades[0]
            send = ", ".join(s.player.name for s in idea.you_send)
            get = ", ".join(s.player.name for s in idea.you_get)
            more = len(self.trades) - 1
            out.append(Move("trade", f"Trade idea with {idea.partner}:",
                        f"send {send}, get {get}." +
                        (f" {more} more in Trade ideas." if more else "")))
        if self.stashes:
            out.append(Move("stash", "Stash:", _name_list(
                [f"{s.score.player.name} ({s.score.player.position})"
                 for s in self.stashes]) + "."))
        if self.byes:
            out.append(Move("bye", "Bye-week hole:", "; ".join(
                f"Week {g.week} leaves you {g.available} healthy {g.position}"
                f"{'s' if g.available != 1 else ''} "
                f"for {g.needed} slot{'s' if g.needed != 1 else ''}"
                for g in self.byes) + "."))
        return out


class Move(NamedTuple):
    """One "Your moves" line: ``kind`` (claim, alternate, held_back, trade,
    stash, bye), the bold ``action`` and the plain ``detail`` after it."""
    kind: str
    action: str
    detail: str


#: How many names a ``moves`` sentence lists before "and N more".
MOVES_MAX_NAMES = 4


def _sentence(text: str) -> str:
    """Capitalize and terminate a reason fragment ("worth your top waiver claim")."""
    text = text.strip()
    if not text:
        return text
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."


def _name_list(names: list[str]) -> str:
    """A short comma list, capped at ``MOVES_MAX_NAMES`` with the rest counted."""
    if len(names) <= MOVES_MAX_NAMES:
        return ", ".join(names)
    return f"{', '.join(names[:MOVES_MAX_NAMES])} and {len(names) - MOVES_MAX_NAMES} more"


def _join(items: list[str]) -> str:
    """Oxford-comma join for the short position lists the reasons above build."""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])} and {items[-1]}"
