"""Trade suggestions: surplus vs. need, both sides gaining, pure throughout."""

from ff_startsit.config import Settings
from ff_startsit.models import Player, SignalValue
from ff_startsit.sources.base import Signal
from ff_startsit.waivers.models import FantasyTeam, LeagueRules
from ff_startsit.waivers.score import score_positions
from ff_startsit.waivers.trades import (FAIRNESS_BAND, suggest_trades,
                                        team_shape, upgrade_value)

RULES = LeagueRules(roster_slots={"QB": 1, "RB": 2, "WR": 2, "TE": 1})


def _p(key, name, pos, team="KC"):
    return Player(key=key, name=name, team=team, position=pos)


class _FakeECR(Signal):
    name = "ecr"
    higher_is_better = False

    def __init__(self, ranks):
        self.ranks = ranks

    def is_available(self):
        return True

    def fetch(self, week, players):
        return {p.key: SignalValue(raw=float(self.ranks[p.key]) if p.key in self.ranks
                                   else None, available=p.key in self.ranks)
                for p in players}


# Me: three good RBs, two weak WRs.  Them: three good WRs, two weak RBs.
MINE = [_p("m1", "My Rb1", "RB"), _p("m2", "My Rb2", "RB"), _p("m3", "My Rb3", "RB"),
        _p("m4", "My Wr1", "WR"), _p("m5", "My Wr2", "WR")]
THEIRS = [_p("t1", "Their Rb1", "RB"), _p("t2", "Their Rb2", "RB"),
          _p("t3", "Their Wr1", "WR"), _p("t4", "Their Wr2", "WR"),
          _p("t5", "Their Wr3", "WR")]
RANKS = {"m1": 2, "m2": 6, "m3": 9, "m4": 40, "m5": 55,
         "t1": 30, "t2": 48, "t3": 4, "t4": 7, "t5": 11}


def _index(ranks=RANKS, players=None):
    players = players or (MINE + THEIRS)
    _, index = score_positions(Settings(), players, 9, signals=[_FakeECR(ranks)])
    return index


def _teams(mine=MINE, theirs=THEIRS):
    return [FantasyTeam("1", "My Squad", tuple(mine), is_mine=True),
            FantasyTeam("2", "Rival FC", tuple(theirs))]


def test_complementary_rosters_produce_a_trade():
    ideas = suggest_trades(_teams(), _index(), RULES)
    assert ideas, "RB-rich for WR-rich is the textbook fit and must be found"
    idea = ideas[0]
    assert idea.partner == "Rival FC"
    assert idea.you_send[0].player.position == "RB"
    assert idea.you_get[0].player.position == "WR"


def test_both_sides_gain_or_it_is_not_proposed():
    """An offer that only helps you is the one that goes unanswered."""
    for idea in suggest_trades(_teams(), _index(), RULES):
        assert idea.your_gain > 0 and idea.their_gain > 0


def test_a_starter_is_never_offered_away():
    """Offers come from surplus only, so no starting slot can be traded off."""
    starters = {s.player.key
                for shape in team_shape(_teams()[0], _index(), RULES).values()
                for s in shape.starters}
    for idea in suggest_trades(_teams(), _index(), RULES):
        assert idea.you_send[0].player.key not in starters


def test_lopsided_values_are_filtered_by_the_fairness_band():
    """A surplus stud for a surplus scrub reads as a lowball, however much it
    improves your lineup."""
    ranks = dict(RANKS, t5=200)   # their WR3 is now worthless
    assert suggest_trades(_teams(), _index(ranks), RULES) == []


def test_bench_points_are_not_points():
    """Receiving a great player at a position you already start better ones at
    is a gain of zero — the check that stops 'trade the biggest numbers'."""
    index = _index()
    shape = team_shape(_teams()[0], index, RULES)["RB"]
    # Their RB2 is worse than both of my starters, so he never cracks my lineup.
    assert upgrade_value(shape, index["t2"]) == 0


def test_no_team_of_mine_means_no_ideas():
    teams = [FantasyTeam("2", "Rival FC", tuple(THEIRS))]
    assert suggest_trades(teams, _index(), RULES) == []


def test_a_team_we_could_not_parse_is_skipped_not_crashed():
    teams = _teams() + [FantasyTeam("3", "Ghost Team", ())]
    assert all(i.partner != "Ghost Team" for i in suggest_trades(teams, _index(), RULES))


def test_the_cap_is_respected_and_one_player_does_not_fill_the_list():
    """Without the dedupe, one surplus RB produces a near-identical offer against
    every one of a rival's spare WRs and crowds everything else out."""
    ideas = suggest_trades(_teams(), _index(), RULES, max_ideas=2)
    assert len(ideas) <= 2
    sent = [i.you_send[0].player.key for i in ideas]
    assert len(sent) == len(set(sent))


def test_same_position_swaps_are_not_proposed():
    for idea in suggest_trades(_teams(), _index(), RULES):
        assert idea.you_send[0].player.position != idea.you_get[0].player.position


def test_fairness_band_is_a_real_constraint_not_a_formality():
    for idea in suggest_trades(_teams(), _index(), RULES):
        gap = abs(idea.you_send[0].final - idea.you_get[0].final)
        assert gap <= FAIRNESS_BAND
