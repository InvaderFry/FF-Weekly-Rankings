"""Scoring the waiver pass: who to add, who to drop, what to bid.

The one idea this module is built around
--------------------------------------

``engine/normalize.py:to_0_100`` is min-max **within the candidate set**. So a
free agent scored against other free agents comes back with a number that says
nothing about your roster — the best free agent always scores 100, whether he is
a league-winner or the least bad of a bad pool. The only way "add him, he beats
your WR4" can be a true sentence is if the free agent and your WR4 were
normalized *together*.

So every position is scored **once**, over one candidate set containing your
roster, every other team's roster, and the free-agent pool. This is the same trap
``report.rank_pooled`` exists to solve for the FLEX slot.

It is only half the fix, though, and the other half is what ``depth_ratio`` below
is for. Scoring a position in one pass makes a free agent comparable to *your WR4*.
It does nothing to make a WR's 78 comparable to a TE's 78 — those came from
separate min-max populations, so subtracting one from the other yields a number
that looks like points and is not. Ordering all adds by ``final``, ordering all
drops by ``final``, and pricing a FAAB bid off the difference all did exactly that:
the best of fifteen defenses scores 100 by arithmetic and outranked every real
running back on the wire.

``depth_ratio`` is the scale-free replacement — a player's ECR rank at his position
over what the league actually starts there. Below 1.0 is a starter, above is bench
depth, and it means the same thing for a QB as for a TE. Ordering, bid conviction
and trade fairness all read it. The 0-100 ``final`` is still shown, and a margin is
still printed when an add and its drop share a position, because there the
subtraction is real.

The second idea: **a missing ECR is not a bad ECR.** FantasyPros ranks 40-75
players per position; most of a waiver pool is below that line, and a player on
bye drops off it entirely. A candidate with no ECR blends on injury alone and
comes back looking healthy and therefore great, which would recommend adding
anonymous backups over real starters and dropping your bye-week RB1. So adds and
drops both *require* an ECR reading, and everything else is reported as
unranked rather than ranked last.

Nothing here is ever logged: every ``recommend`` call passes ``log=False``.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

from ..config import Settings
from ..models import Player, PlayerScore, Recommendation
from ..pipeline import recommend
from ..sources.injury import HEALTHY_SCORE
from .models import (ACQ_FAAB, ACQ_PRIORITY, ByeGap, ColumnMention,
                     DropCandidate, FantasyTeam, LeagueRules, PoolPlayer,
                     StashIdea, WaiverTarget)

#: Default starting slots when the platform won't tell us, mirroring
#: ``report.LINEUP_SLOTS`` so the waiver report and the lineup builder agree on
#: what "a starter" means.
DEFAULT_SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "K": 1, "DEF": 1}
# The lineup also has a FLEX. It is deliberately not modeled as an extra body
# per position here — ``droppable``'s ``protected`` argument carries
# ``report.build_lineup``'s actual FLEX pick, which is the same guard done once
# and done accurately.

#: Teams assumed when the platform won't say, for turning a rank into a ratio.
#: The wrong count skews every position identically, so the *ordering* survives it.
DEFAULT_TEAM_COUNT = 12

#: Below this many teams the list is a partial parse, not a small league, and the
#: default is used instead. The error is not symmetric: too *few* teams shrinks
#: every position's starter demand, which reads every free agent as roster filler
#: and empties the report — an outage rendered as "nothing worth adding", which is
#: the one failure mode a waiver report must never have.
MIN_LEAGUE_TEAMS = 4

#: The deepest a free agent can rank, as a multiple of his position's starter
#: demand, and still be worth a roster spot at all. Twice the starter field — the
#: RB48 in a league that starts 24 — is where a name stops being a bye-week fill
#: and becomes filler. This is only the outer bound; whether an add is actually an
#: upgrade is ``_worth_adding``'s question, and it is the stricter of the two.
MAX_ADD_DEPTH_RATIO = 2.0

#: A depth ratio at or below this — a player who would start somewhere in the
#: league — is maximum bid conviction. Conviction falls to zero at
#: ``MAX_ADD_DEPTH_RATIO``.
FULL_CONVICTION_DEPTH_RATIO = 1.0
#: Never advise blowing more than this share of the remaining budget on one bid.
MAX_BID_SHARE = 0.40
MIN_BID_SHARE = 0.02

#: Injury statuses worth stashing rather than starting.
_STASH_STATUSES = {"IR", "OUT", "PUP", "SUS", "NA", "DNR", "COV"}

#: How many weeks ahead the bye-week check looks.
BYE_HORIZON = 3


# --- scoring ---------------------------------------------------------------
def dedupe_players(*groups: Iterable[Player]) -> list[Player]:
    """Merge player lists, keeping the first occurrence of each key.

    Free agents and rostered players share a key space (``espn-{id}`` on both
    sides, the Sleeper id on both sides), so the same person can legitimately
    arrive from two directions — a stale pool listing someone just claimed, say.
    First-wins keeps the roster's copy, which is the more trustworthy one.
    """
    seen: set[str] = set()
    out: list[Player] = []
    for group in groups:
        for p in group:
            if p.key in seen:
                continue
            seen.add(p.key)
            out.append(p)
    return out


def group_by_position(players: Sequence[Player]) -> dict[str, list[Player]]:
    groups: dict[str, list[Player]] = {}
    for p in players:
        groups.setdefault(p.position, []).append(p)
    return groups


def score_positions(settings: Settings, players: Sequence[Player], week: int,
                    signals: Optional[Sequence] = None,
                    ) -> tuple[dict[str, Recommendation], dict[str, PlayerScore]]:
    """One blend pass per position over the whole candidate set.

    Returns the per-position recommendations plus a flat ``key -> PlayerScore``
    index, which is what the add/drop/trade logic actually reads.

    ``log=False`` is not a default being accepted — it is the invariant. A
    waiver row would put a hundred players inside one "decision", most of them
    on other people's teams, and the calibrator scores pairwise concordance
    *within* a decision. It would also count toward the ``calibrate --write``
    floors with evidence the user never acted on.
    """
    recs: dict[str, Recommendation] = {}
    index: dict[str, PlayerScore] = {}
    for pos, cands in group_by_position(players).items():
        rec = recommend(settings, cands, week, signals=signals,
                        command="waivers", log=False)
        recs[pos] = rec
        for score in rec.scores:
            index[score.player.key] = score
    return recs, index


def has_ecr(score: PlayerScore) -> bool:
    """Whether the blend's heaviest signal actually covered this player."""
    return _covered(score, "ecr")


def _covered(score: PlayerScore, signal: str) -> bool:
    value = score.raw.get(signal)
    return bool(value is not None and value.available and value.raw is not None)


def signal_coverage(index: dict[str, PlayerScore],
                    players: Sequence[Player]) -> dict[str, int]:
    """How many of ``players`` each signal returned a usable value for.

    The dress rehearsal exists to prove the pipeline reaches real data, and an
    empty report is otherwise ambiguous between "the wire is quiet" and "nothing
    connected". Counting per signal is the difference. Every signal seen on any
    player is reported, including the ones that covered nobody — a zero is the
    most informative number here.
    """
    counts: dict[str, int] = {}
    for player in players:
        score = index.get(player.key)
        if score is None:
            continue
        for name in score.raw:
            counts.setdefault(name, 0)
            if _covered(score, name):
                counts[name] += 1
    return counts


# --- roster shape ----------------------------------------------------------
def starting_slots(rules: LeagueRules) -> dict[str, int]:
    """Starting slots per position, from the league if known, else the default."""
    return dict(rules.roster_slots) if rules.roster_slots else dict(DEFAULT_SLOTS)


def starter_demand(position: str, rules: LeagueRules) -> int:
    """How many of ``position`` the whole league starts in a week."""
    return (rules.team_count or DEFAULT_TEAM_COUNT) * max(
        1, starting_slots(rules).get(position, 1))


def depth_ratio(score: PlayerScore, rules: LeagueRules) -> Optional[float]:
    """A player's ECR rank over what his league actually starts at his position.

    The one quantity in this module that means the same thing at every position.
    ECR's raw value *is* the positional rank (``sources/ecr.py``:
    ``higher_is_better = False``), so this costs no extra fetch — it just divides
    that rank by ``team_count x starting slots``. RB30 in a 12-team league starting
    two is 30/24 = 1.25; QB13 in the same league starting one is 13/12 = 1.08. The
    second is the more useful player to roster, and no comparison of their 0-100
    scores could have told you that, because those came from different populations.

    ``None`` when ECR did not cover the player — which ``has_ecr`` already prevents
    on both the add and the drop path, since an unranked player is usually on bye
    rather than bad.
    """
    value = score.raw.get("ecr")
    if value is None or not value.available or value.raw is None or value.raw <= 0:
        return None
    return float(value.raw) / starter_demand(score.player.position, rules)


def keep_counts(rules: LeagueRules) -> dict[str, int]:
    """How many players at each position must survive any drop suggestion.

    Exactly the starting slots — the FLEX body is guarded separately, by
    ``protected``, which carries ``report.build_lineup``'s actual FLEX pick.
    Reserving a spare at *every* flex position instead would hold three bodies
    back for one slot, and stack a second guard on top of the first: with a
    2-WR league and three WRs, nothing was ever droppable.
    """
    return starting_slots(rules)


def droppable(my_scores: Sequence[PlayerScore], rules: LeagueRules,
              protected: Optional[set[str]] = None) -> list[DropCandidate]:
    """Roster players safe to cut, worst first.

    Three guards, each of which was a way to suggest a bad drop:
    a player the lineup builder starts is never droppable; a position is never
    cut below its starting requirement; and a player with no ECR is left alone
    entirely, because "unranked" on a Tuesday usually means "on bye", not "bad".

    "Worst" is measured by ``depth_ratio``, not by ``final``. Across positions only
    the ratio means anything, and this list is consumed across positions — the head
    of it is what an add gets paired with. (Within a position the two can still
    disagree, since ``final`` carries injury, Vegas and weather and the ratio does
    not; ordering by rank is the deliberate choice here, and ``_worth_adding`` asks
    the blend before any of these is actually cut.)
    Sorting by ``final`` made the most-droppable player whichever position happened
    to have the widest score spread.
    """
    protected = protected or set()
    by_pos: dict[str, list[PlayerScore]] = {}
    for s in my_scores:
        by_pos.setdefault(s.player.position, []).append(s)

    keep = keep_counts(rules)
    out: list[DropCandidate] = []
    for pos, scores in by_pos.items():
        ranked = sorted(
            (s for s in scores if s.final is not None),
            key=lambda s: s.final, reverse=True,
        )
        surplus = ranked[keep.get(pos, 1):]
        for s in surplus:
            if s.player.key in protected:
                continue
            if not has_ecr(s):
                continue
            status = _injury_note(s)
            reason = (f"{pos} depth behind {len(ranked) - len(surplus)} you'd start"
                      if not status else f"{status}; {pos} depth")
            out.append(DropCandidate(score=s, reason=reason))

    out.sort(key=lambda d: (depth_ratio(d.score, rules) or 0.0), reverse=True)
    return out


def _injury_note(score: PlayerScore) -> str:
    """Render the injury signal's read as a short label, if it says anything."""
    value = score.raw.get("injury")
    if value is None or not value.available or value.raw is None:
        return ""
    if value.raw >= HEALTHY_SCORE:
        return ""
    return (value.note or "injury concern").strip()


# --- adds ------------------------------------------------------------------
def pick_adds(index: dict[str, PlayerScore], pool: Sequence[PoolPlayer],
              drops: Sequence[DropCandidate], rules: LeagueRules,
              faab_remaining: Optional[float] = None,
              journalist_ranks: Optional[dict[str, float]] = None,
              mentions: Optional[dict[str, list[ColumnMention]]] = None,
              max_adds: int = 8) -> list[WaiverTarget]:
    """Pair the best free agents with the worst droppable roster players.

    An add is only an add if there is a body to cut for him, so each target is
    matched to a distinct drop — a ranking with no roster to compare against would
    just be a list of free agents, which is what every other site already gives you.

    Ordering and the worth-it test both read ``depth_ratio``, not ``final``.
    Dropping a WR to add an RB is an ordinary roster move, but ``rb.final -
    wr.final`` is not a quantity: those two came from separate min-max populations.
    So the pairing stays, the *arithmetic* goes — ``margin`` is filled in only when
    the add and his drop share a position, and whether an add is worth making is
    decided by where he ranks against his own position's starter demand.
    """
    journalist_ranks = journalist_ranks or {}
    mentions = mentions or {}
    pool_by_key = {pp.player.key: pp for pp in pool}

    candidates: list[tuple[float, PlayerScore]] = []
    for key in pool_by_key:
        score = index.get(key)
        if score is None or score.final is None or not has_ecr(score):
            continue
        ratio = depth_ratio(score, rules)
        if ratio is None or ratio > MAX_ADD_DEPTH_RATIO:
            continue  # too deep at his position to be worth a roster spot
        candidates.append((ratio, score))
    # Shallowest first; ``final`` only breaks ties, where both are at one position
    # and it is a real comparison again.
    candidates.sort(key=lambda c: (c[0], -c[1].final))

    available_drops = list(drops)
    targets: list[WaiverTarget] = []
    for ratio, score in candidates:
        if len(targets) >= max_adds or not available_drops:
            break
        drop = available_drops[0]
        if not _worth_adding(score, drop.score, rules):
            continue
        available_drops.pop(0)
        margin = None
        if drop.score.player.position == score.player.position:
            margin = score.final - drop.score.final
        pp = pool_by_key.get(score.player.key)
        target = WaiverTarget(
            score=score,
            margin=margin,
            drop=drop.score,
            pool=pp,
            journalist_avg=journalist_ranks.get(score.player.key),
            mentions=tuple(mentions.get(score.player.key, ())),
        )
        target.bid = suggest_bid(target, rules, faab_remaining,
                                 conviction=_conviction(ratio))
        target.reasons = tuple(add_reasons(target, rules))
        targets.append(target)
    return targets


def _worth_adding(add: PlayerScore, drop: PlayerScore, rules: LeagueRules) -> bool:
    """Whether ``add`` is an upgrade on ``drop``, judged on a shared scale.

    Same position: the blended ``final``. ``score_positions`` runs **one** pass per
    position over your roster, every other roster and the pool together, so two
    same-position finals came out of one normalization frame and their comparison
    is real — this is the case ``add.final > drop.final`` was always right about.
    Reading ECR alone here instead threw away injury, Vegas and weather, which is
    the entire ensemble; it recommended a free agent the blend scored *below* the
    man he replaced, and ``pick_adds`` then printed that as "scores -20.0 above".

    Different positions: the depth ratios, since that is the only reading the two
    share. That is where ``add.final > drop.final`` genuinely had to go — those
    finals came from separate min-max populations, and the deepest position on the
    roster won by default.
    """
    if add.player.position == drop.player.position:
        if add.final is None or drop.final is None:
            return False
        return add.final > drop.final
    add_ratio = depth_ratio(add, rules)
    drop_ratio = depth_ratio(drop, rules)
    if add_ratio is None or drop_ratio is None:
        return False
    return add_ratio < drop_ratio


def _conviction(ratio: float) -> float:
    """0-1 read on how strongly a free agent's own standing argues for the claim.

    Full conviction for anyone who would start somewhere in the league, falling to
    nothing at twice the starter field. Scale-free by construction, so the same
    player is worth the same bid whoever ends up at the head of the drop list —
    which the old ``margin / MAX_CONVICTION_MARGIN`` could not promise.
    """
    span = MAX_ADD_DEPTH_RATIO - FULL_CONVICTION_DEPTH_RATIO
    return min(1.0, max(0.0, (MAX_ADD_DEPTH_RATIO - ratio) / span))


def add_reasons(target: WaiverTarget, rules: LeagueRules) -> list[str]:
    """Short human "why" lines — the part a ranking alone doesn't tell you."""
    reasons: list[str] = []
    if target.drop is not None:
        if target.margin is not None:
            reasons.append(
                f"scores {target.margin:.1f} above {target.drop.player.name}, "
                f"your most droppable {target.drop.player.position}"
            )
        else:
            # No margin across positions: the two scores were normalized in
            # different candidate sets, so their difference isn't a number. The
            # move is still real — say what it is instead of pricing it.
            reasons.append(
                f"takes the roster spot from {target.drop.player.name} "
                f"({target.drop.player.position}), your most droppable player"
            )
    ecr = target.score.raw.get("ecr")
    if ecr is not None and ecr.available and ecr.raw is not None:
        pos = target.score.player.position
        reasons.append(
            f"ranks {pos}{ecr.raw:g} where the league starts "
            f"{starter_demand(pos, rules)}"
        )
    if target.journalist_avg is not None:
        reasons.append(f"preferred journalists average him {target.journalist_avg:.1f}")
    if target.mentions:
        authors = ", ".join(sorted({m.author for m in target.mentions}))
        reasons.append(f"named this week by {authors}")
    pp = target.pool
    if pp is not None and pp.percent_owned is not None:
        reasons.append(f"rostered in {pp.percent_owned:g}% of leagues")
    if pp is not None and pp.trending_adds:
        reasons.append(f"{pp.trending_adds:,} adds in the last 24h")
    return reasons


def suggest_bid(target: WaiverTarget, rules: LeagueRules,
                faab_remaining: Optional[float], conviction: float = 0.0) -> str:
    """Bid advice matched to how the league actually acquires players.

    Deliberately silent when ``acquisition_type`` is unknown: a dollar figure
    printed for a rolling-priority league, or a claim ranking printed for a FAAB
    one, is worse than no advice — it is advice for somebody else's league.

    ``conviction`` is supplied by the caller from the add's own depth ratio rather
    than derived here from ``target.margin``. It used to be the latter, which made
    three quarters of a real dollar figure out of a subtraction across two
    normalization frames: the same player was worth a different bid depending on
    which position happened to sit at the head of your drop list.
    """
    demand = _demand(target.pool)

    if rules.acquisition_type == ACQ_FAAB:
        if not faab_remaining or faab_remaining <= 0:
            return "no FAAB left"
        share = MIN_BID_SHARE + (MAX_BID_SHARE - MIN_BID_SHARE) * (
            0.75 * conviction + 0.25 * demand)
        dollars = max(1, round(faab_remaining * share))
        return (f"bid ~${dollars} "
                f"({dollars / faab_remaining * 100:.0f}% of your ${faab_remaining:,.0f} left)")

    if rules.acquisition_type == ACQ_PRIORITY:
        weighted = 0.75 * conviction + 0.25 * demand
        if weighted >= 0.66:
            return "worth your top waiver claim"
        if weighted >= 0.33:
            return "worth a mid-priority claim"
        return "only if he clears waivers"

    return ""


def _demand(pool: Optional[PoolPlayer]) -> float:
    """0-1 read on how contested a claim is, from whatever the platform gave us."""
    if pool is None:
        return 0.0
    if pool.percent_owned is not None:
        return min(1.0, pool.percent_owned / 60.0)
    if pool.trending_adds:
        return min(1.0, pool.trending_adds / 20000.0)
    return 0.0


# --- stashes & byes --------------------------------------------------------
def find_stashes(index: dict[str, PlayerScore], pool: Sequence[PoolPlayer],
                 taken: set[str], bye_teams: set[str],
                 max_stashes: int = 5) -> list[StashIdea]:
    """Free agents worth a bench spot rather than a starting one.

    Two derivable kinds, and no others — there's no depth chart in this app, so
    "handcuff" is not something it can honestly claim to detect:

    * **Hurt but ranked** — a real player carrying an OUT/IR/PUP/SUS tag, who is
      only in the pool because he's shelved.
    * **On bye** — ranked, healthy, and invisible this week purely because his
      team isn't playing. He is the cheapest good player on the wire today.
    """
    def _rank(pp: PoolPlayer) -> float:
        # Unscored players must sort last, not first: a bare `or 0` key put every
        # player the blend couldn't reach at the top of the stash list.
        score = index.get(pp.player.key)
        return -(score.final) if score is not None and score.final is not None else 1.0

    out: list[StashIdea] = []
    for pp in sorted(pool, key=_rank):
        if len(out) >= max_stashes:
            break
        key = pp.player.key
        if key in taken:
            continue
        score = index.get(key)
        if score is None or score.final is None:
            continue
        status = (pp.injury_status or "").upper()
        if status in _STASH_STATUSES:
            out.append(StashIdea(score=score, reason=f"{status} — stash while he's cheap"))
        elif pp.player.team and pp.player.team in bye_teams and has_ecr(score):
            out.append(StashIdea(score=score,
                                 reason="on bye this week — ranked, and nobody else is looking"))
    return out


def bye_gaps(my_players: Sequence[Player], rules: LeagueRules, week: int,
             schedule_by_week: dict[int, set[str]]) -> list[ByeGap]:
    """Weeks where a position can't field its starters because of byes.

    ``schedule_by_week`` maps week -> the set of teams playing that week. A week
    the schedule provider couldn't reach is simply absent from the mapping
    rather than present-and-empty, because an empty set would read as "all 32
    teams on bye" and fire a false alarm for every position.
    """
    keep = starting_slots(rules)
    gaps: list[ByeGap] = []
    for ahead in range(0, BYE_HORIZON + 1):
        target = week + ahead
        playing = schedule_by_week.get(target)
        if not playing:
            continue
        by_pos: dict[str, int] = {}
        for p in my_players:
            if p.team and p.team in playing:
                by_pos[p.position] = by_pos.get(p.position, 0) + 1
        for pos, needed in keep.items():
            available = by_pos.get(pos, 0)
            if available < needed:
                gaps.append(ByeGap(week=target, position=pos,
                                   available=available, needed=needed))
    return gaps


def team_players(teams: Sequence[FantasyTeam], mine: bool) -> list[Player]:
    """All players on your team (``mine=True``) or on everyone else's."""
    out: list[Player] = []
    for t in teams:
        if t.is_mine is mine:
            out.extend(t.players)
    return out
