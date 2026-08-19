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
roster, every other team's roster, and the free-agent pool. Adds, drops and
trade values then all read off the same 0-100 scale, and the trade half needs no
scoring pass of its own. This is the same trap ``report.rank_pooled`` exists to
solve for the FLEX slot, and the same fix.

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

#: A 0-100 margin at or above this is treated as maximum conviction for bidding.
MAX_CONVICTION_MARGIN = 25.0
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
    value = score.raw.get("ecr")
    return bool(value is not None and value.available and value.raw is not None)


# --- roster shape ----------------------------------------------------------
def starting_slots(rules: LeagueRules) -> dict[str, int]:
    """Starting slots per position, from the league if known, else the default."""
    return dict(rules.roster_slots) if rules.roster_slots else dict(DEFAULT_SLOTS)


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

    out.sort(key=lambda d: d.score.final)
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

    An add is only an add if it beats somebody you can actually cut, so each
    target is matched to a distinct drop and the margin is measured against that
    drop — a ranking with no roster to compare against would just be a list of
    free agents, which is what every other site already gives you.
    """
    journalist_ranks = journalist_ranks or {}
    mentions = mentions or {}
    pool_by_key = {pp.player.key: pp for pp in pool}

    candidates: list[PlayerScore] = []
    for key in pool_by_key:
        score = index.get(key)
        if score is None or score.final is None or not has_ecr(score):
            continue
        candidates.append(score)
    candidates.sort(key=lambda s: s.final, reverse=True)

    available_drops = list(drops)
    targets: list[WaiverTarget] = []
    for score in candidates:
        if len(targets) >= max_adds or not available_drops:
            break
        drop = available_drops[0]
        if drop.score.final is None or score.final <= drop.score.final:
            # The best remaining free agent can't beat the worst player you can
            # cut — nothing below him will either.
            break
        available_drops.pop(0)
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
        target.bid = suggest_bid(target, rules, faab_remaining)
        target.reasons = tuple(add_reasons(target))
        targets.append(target)
    return targets


def add_reasons(target: WaiverTarget) -> list[str]:
    """Short human "why" lines — the part a ranking alone doesn't tell you."""
    reasons: list[str] = []
    if target.drop is not None:
        reasons.append(
            f"scores {target.margin:.1f} above {target.drop.player.name}, "
            f"your most droppable {target.drop.player.position}"
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
                faab_remaining: Optional[float]) -> str:
    """Bid advice matched to how the league actually acquires players.

    Deliberately silent when ``acquisition_type`` is unknown: a dollar figure
    printed for a rolling-priority league, or a claim ranking printed for a FAAB
    one, is worse than no advice — it is advice for somebody else's league.
    """
    conviction = min(1.0, max(0.0, target.margin / MAX_CONVICTION_MARGIN))
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
