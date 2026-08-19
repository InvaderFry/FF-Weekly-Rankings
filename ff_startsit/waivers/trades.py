"""Trade ideas: who has what you need, and needs what you have.

Pure functions over already-scored players, in the spirit of ``engine/`` — no
I/O, no fetching. The whole section rides on a fact the app was already paying
for: ESPN's ``mRoster`` response carries **every** team's roster, and Sleeper's
``/league/{id}/rosters`` does the same. Because ``score.score_positions`` scores
the entire league in one candidate set per position, every player in the league
already has a 0-100 number on the same scale, and a trade is just arithmetic on
those numbers.

What this is honest about
-------------------------

The scores are **this week's** ensemble reads. There is no rest-of-season
projection, no strength of schedule, no keeper or dynasty value in this app, and
a trade is a season-long decision made on those things. So these are framed as
conversation starters — "these two rosters fit" — not as valuations. The
renderers say so, and ``rationale`` says why each pairing was surfaced.

The surplus model
-----------------

A team's *starters* at a position are its top N by score, where N is the
league's starting requirement. Everyone above N is **surplus** — real value that
cannot enter their lineup. The gap between a team's worst starter and the best
starter they'd field if they traded from surplus is the size of their **need**.
A swap is proposed only when it moves both teams' starting lineups forward,
which is the only kind of trade that actually gets accepted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from ..models import PlayerScore
from .models import FantasyTeam, LeagueRules, TradeIdea
from .score import starting_slots

#: Two sides' totals must land within this many 0-100 points for the offer to
#: read as fair rather than as a lowball nobody answers.
FAIRNESS_BAND = 12.0
#: Below this, a "gain" is inside the noise of the blend and not worth a message.
MIN_GAIN = 2.0


@dataclass(frozen=True)
class PositionShape:
    """One team's depth at one position: who starts, and who's stuck on the bench."""

    position: str
    starters: tuple[PlayerScore, ...]
    surplus: tuple[PlayerScore, ...]

    @property
    def worst_starter(self) -> Optional[PlayerScore]:
        return self.starters[-1] if self.starters else None

    @property
    def best_surplus(self) -> Optional[PlayerScore]:
        return self.surplus[0] if self.surplus else None


def team_shape(team: FantasyTeam, index: dict[str, PlayerScore],
               rules: LeagueRules) -> dict[str, PositionShape]:
    """Split every position on a roster into starters and surplus."""
    slots = starting_slots(rules)
    by_pos: dict[str, list[PlayerScore]] = {}
    for player in team.players:
        score = index.get(player.key)
        if score is None or score.final is None:
            continue
        by_pos.setdefault(player.position, []).append(score)

    shapes: dict[str, PositionShape] = {}
    for pos, scores in by_pos.items():
        scores.sort(key=lambda s: s.final, reverse=True)
        n = slots.get(pos, 1)
        shapes[pos] = PositionShape(position=pos, starters=tuple(scores[:n]),
                                    surplus=tuple(scores[n:]))
    return shapes


def upgrade_value(shape: Optional[PositionShape], incoming: PlayerScore,
                  outgoing: Sequence[PlayerScore] = ()) -> float:
    """How much a team's *starting lineup* improves by receiving ``incoming``.

    Bench points are not points. A team that receives a great WR while already
    starting two better ones has gained nothing this week, and this returns 0 for
    that — which is what stops the naive "trade the highest scores" version from
    proposing swaps neither side wants.
    """
    outgoing_keys = {s.player.key for s in outgoing}
    pool = [s for s in (shape.starters + shape.surplus if shape else ())
            if s.player.key not in outgoing_keys]
    pool.append(incoming)
    pool.sort(key=lambda s: s.final, reverse=True)

    n = len(shape.starters) if shape else 1
    before = [s for s in (shape.starters if shape else ())
              if s.player.key not in outgoing_keys]
    after = pool[:n]
    return sum(s.final for s in after) - sum(s.final for s in before)


def _pair_gain(mine: dict[str, PositionShape], theirs: dict[str, PositionShape],
               send: PlayerScore, get: PlayerScore) -> tuple[float, float]:
    """(your gain, their gain) in starting-lineup points for a 1-for-1."""
    your_gain = upgrade_value(mine.get(get.player.position), get, outgoing=[send])
    their_gain = upgrade_value(theirs.get(send.player.position), send, outgoing=[get])
    return your_gain, their_gain


def suggest_trades(teams: Sequence[FantasyTeam], index: dict[str, PlayerScore],
                   rules: LeagueRules, max_ideas: int = 5,
                   protected: Optional[set[str]] = None) -> list[TradeIdea]:
    """Propose swaps where both rosters' starting lineups improve.

    Only 1-for-1s: a 2-for-1 needs a roster-size model to know the other side can
    absorb the extra body, and proposing one they cannot accept wastes the pitch.

    ``protected`` exists for callers who want to pin specific players, but it is
    normally left empty: offers are drawn from *surplus* only, so by construction
    no starting slot is ever traded away. Passing a lineup's worth of protected
    keys blocks everything, because surplus depth is what the FLEX is made of.
    """
    protected = protected or set()
    mine_team = next((t for t in teams if t.is_mine), None)
    if mine_team is None:
        return []

    mine = team_shape(mine_team, index, rules)
    ideas: list[TradeIdea] = []

    for other in teams:
        if other.is_mine or not other.players:
            continue
        theirs = team_shape(other, index, rules)

        for my_pos, my_shape in mine.items():
            for send in my_shape.surplus:
                if send.player.key in protected or send.final is None:
                    continue
                for their_pos, their_shape in theirs.items():
                    if their_pos == my_pos:
                        continue  # swapping RB for RB rarely fixes either roster
                    for get in their_shape.surplus:
                        if get.final is None:
                            continue
                        if abs(send.final - get.final) > FAIRNESS_BAND:
                            continue
                        your_gain, their_gain = _pair_gain(mine, theirs, send, get)
                        if your_gain < MIN_GAIN or their_gain < MIN_GAIN:
                            continue
                        ideas.append(TradeIdea(
                            partner=other.name,
                            you_send=(send,),
                            you_get=(get,),
                            your_gain=your_gain,
                            their_gain=their_gain,
                            rationale=(
                                f"You start {their_pos} thinner than {my_pos}; "
                                f"they start {my_pos} thinner than {their_pos}. "
                                f"Both lineups get better this week."
                            ),
                        ))

    # Rank by your gain, then by theirs — an offer that's great for you and
    # barely moves them is the one that goes unanswered.
    ideas.sort(key=lambda i: (i.your_gain, i.their_gain), reverse=True)
    return _dedupe(ideas)[:max_ideas]


def _dedupe(ideas: Sequence[TradeIdea]) -> list[TradeIdea]:
    """Keep the best idea per (partner, player you send) so one surplus player
    doesn't fill the whole list with near-identical offers."""
    seen: set[tuple[str, str]] = set()
    out: list[TradeIdea] = []
    for idea in ideas:
        sig = (idea.partner, idea.you_send[0].player.key if idea.you_send else "")
        if sig in seen:
            continue
        seen.add(sig)
        out.append(idea)
    return out
