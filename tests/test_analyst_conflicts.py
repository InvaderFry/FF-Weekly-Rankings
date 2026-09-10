from copy import deepcopy

import pytest

from ff_startsit.engine.analyst import detect_conflicts
from ff_startsit.models import Player, PlayerScore, Recommendation


def rec():
    return Recommendation(1, 'ppr', {'ecr': 1}, [
        PlayerScore(Player(str(i), f'Player {i}', 'KC', 'WR'), final=100 - i)
        for i in range(4)], notes=['existing note'], close_call=True)


def test_top_pair_and_boundary_and_no_mutation():
    recommendation = rec()
    before = deepcopy(recommendation)
    conflicts = detect_conflicts(recommendation, {'0': 10, '1': 5, '2': 1, '3': 2}, 'Justin Boone', 5, 2)
    assert len(conflicts) == 2
    assert (conflicts[0].gap, conflicts[0].material, conflicts[0].boundary) == (5, True, False)
    assert (conflicts[1].gap, conflicts[1].material, conflicts[1].boundary) == (4, False, True)
    assert recommendation == before


@pytest.mark.parametrize('ranks', [{}, {'0': 10}, {'1': 1}, {'0': 1, '1': 2}, {'0': 1, '1': 1}])
def test_agreement_and_missing_player_are_silent(ranks):
    assert detect_conflicts(rec(), ranks, 'Justin Boone', 5) == []


@pytest.mark.parametrize('count', [None, 0, 1, 4, 5])
def test_boundary_guard(count):
    assert detect_conflicts(rec(), {'1': 20, '2': 1}, 'Justin Boone', 5, count) == []


def test_custom_three_starter_boundary():
    conflict, = detect_conflicts(rec(), {'2': 20, '3': 1}, 'Justin Boone', 5, 3)
    assert conflict.boundary and conflict.preferred == 'Player 3'


def test_unscored_players_are_excluded():
    recommendation = rec()
    for s in recommendation.scores[1:]:
        s.final = None
    assert detect_conflicts(recommendation, {'0': 10, '1': 1}, 'Justin Boone', 5) == []


def test_small_inversion_is_a_note():
    conflict, = detect_conflicts(rec(), {'0': 8, '1': 6}, 'Justin Boone', 5)
    assert conflict.gap == 2 and not conflict.material
