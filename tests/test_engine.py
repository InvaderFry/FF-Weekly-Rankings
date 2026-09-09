from ff_startsit.engine.blend import blend, weighted_final
from ff_startsit.engine.normalize import NEUTRAL, to_0_100
from ff_startsit.models import Player, SignalValue


def test_to_0_100_lower_is_better():
    out = to_0_100({"a": 1.0, "b": 8.0, "c": 15.0}, higher_is_better=False)
    assert out["a"] == 100.0   # best (lowest) rank
    assert out["c"] == 0.0     # worst rank
    assert 0 < out["b"] < 100


def test_to_0_100_higher_is_better_and_skips_none():
    out = to_0_100({"a": 16.0, "b": 25.5, "c": None}, higher_is_better=True)
    assert out["b"] == 100.0
    assert out["a"] == 0.0
    assert "c" not in out


def test_to_0_100_flat_set_is_neutral():
    out = to_0_100({"a": 5.0, "b": 5.0}, higher_is_better=True)
    assert out["a"] == out["b"] == NEUTRAL


def _players():
    return [
        Player(key="1", name="Alpha", team="KC", position="RB"),
        Player(key="2", name="Bravo", team="CHI", position="RB"),
    ]


def test_blend_weights_and_ranks():
    players = _players()
    signal_values = {
        "ecr": {"1": SignalValue(1.0), "2": SignalValue(8.0)},          # Alpha better
        "vegas": {"1": SignalValue(25.5), "2": SignalValue(16.0)},      # Alpha better
    }
    rec = blend(
        week=3, scoring="ppr", players=players,
        signal_values=signal_values,
        higher_is_better={"ecr": False, "vegas": True},
        weights={"ecr": 0.75, "vegas": 0.25},
        close_call_threshold=5.0,
    )
    assert [s.player.key for s in rec.scores] == ["1", "2"]
    assert rec.scores[0].final == 100.0   # Alpha tops both signals
    assert rec.scores[1].final == 0.0
    assert rec.close_call is False


def test_blend_flags_signal_disagreement():
    players = _players()
    # ECR favors Alpha; Vegas favors Bravo -> disagreement -> close call.
    signal_values = {
        "ecr": {"1": SignalValue(1.0), "2": SignalValue(8.0)},
        "vegas": {"1": SignalValue(16.0), "2": SignalValue(25.5)},
    }
    rec = blend(
        week=3, scoring="ppr", players=players,
        signal_values=signal_values,
        higher_is_better={"ecr": False, "vegas": True},
        weights={"ecr": 0.75, "vegas": 0.25},
        close_call_threshold=1.0,
    )
    assert rec.close_call is True
    assert any("disagree" in n.lower() for n in rec.notes)


def test_blend_falls_back_to_available_signal():
    players = _players()
    signal_values = {
        "ecr": {"1": SignalValue(1.0), "2": SignalValue(8.0)},
        "vegas": {
            "1": SignalValue(None, available=False, note="bye / no team"),
            "2": SignalValue(16.0),
        },
    }
    rec = blend(
        week=3, scoring="ppr", players=players,
        signal_values=signal_values,
        higher_is_better={"ecr": False, "vegas": True},
        weights={"ecr": 0.75, "vegas": 0.25},
        close_call_threshold=5.0,
    )
    alpha = next(s for s in rec.scores if s.player.key == "1")
    # Alpha has no Vegas -> scored on ECR alone, and its flag is surfaced.
    assert "vegas" not in alpha.normalized
    assert any("bye" in f.lower() for f in alpha.flags)
    assert alpha.final is not None


def test_low_weight_disagreement_does_not_flag_close_call():
    """A 10%-weight signal flipping the top two is not a coin-flip.

    Before the weight floor, weather (0.10) flagged as loudly as ECR (0.60) --
    and a zero-weight signal did too, since the disagreement scan never saw the
    weights at all. A flag that fires on everything tells the user nothing.
    """
    players = _players()
    signal_values = {
        "ecr": {"1": SignalValue(1.0), "2": SignalValue(8.0)},        # Alpha well ahead
        "weather": {"1": SignalValue(40.0), "2": SignalValue(90.0)},  # Bravo ahead
    }
    kwargs = dict(
        week=3, scoring="ppr", players=players, signal_values=signal_values,
        higher_is_better={"ecr": False, "weather": True},
        weights={"ecr": 0.90, "weather": 0.10},
        close_call_threshold=1.0,
    )
    assert blend(**kwargs, min_disagree_weight=0.15).close_call is False
    # ...and the historical behavior is still reachable by lowering the floor.
    assert blend(**kwargs, min_disagree_weight=0.0).close_call is True


def test_zero_weight_signal_cannot_flag_close_call():
    players = _players()
    signal_values = {
        "ecr": {"1": SignalValue(1.0), "2": SignalValue(8.0)},
        "weather": {"1": SignalValue(40.0), "2": SignalValue(90.0)},
    }
    rec = blend(
        week=3, scoring="ppr", players=players, signal_values=signal_values,
        higher_is_better={"ecr": False, "weather": True},
        weights={"ecr": 1.0, "weather": 0.0},   # weather contributes nothing
        close_call_threshold=1.0, min_disagree_weight=0.15,
    )
    assert rec.close_call is False


def test_heavyweight_disagreement_still_flags():
    """The condition is narrowed, not removed -- ECR flipping still counts."""
    players = _players()
    signal_values = {
        "ecr": {"1": SignalValue(8.0), "2": SignalValue(1.0)},        # Bravo ahead
        "vegas": {"1": SignalValue(30.0), "2": SignalValue(16.0)},    # Alpha ahead
    }
    rec = blend(
        week=3, scoring="ppr", players=players, signal_values=signal_values,
        higher_is_better={"ecr": False, "vegas": True},
        weights={"ecr": 0.40, "vegas": 0.60},
        close_call_threshold=1.0, min_disagree_weight=0.15,
    )
    assert rec.close_call is True
    assert any("disagree" in n.lower() for n in rec.notes)


def test_exact_tie_is_not_a_disagreement_at_zero_threshold():
    """A signal that scores the top two identically does not favor either.

    With FF_CLOSE_CALL_THRESHOLD=0 a bare `b - a < threshold` guard lets ties
    through, reporting "signals disagree" about a signal that is indifferent.
    """
    players = _players()
    signal_values = {
        "ecr": {"1": SignalValue(1.0), "2": SignalValue(8.0)},        # Alpha ahead
        "vegas": {"1": SignalValue(20.0), "2": SignalValue(20.0)},    # dead level
    }
    rec = blend(
        week=3, scoring="ppr", players=players, signal_values=signal_values,
        higher_is_better={"ecr": False, "vegas": True},
        weights={"ecr": 0.5, "vegas": 0.5},
        close_call_threshold=0.0, min_disagree_weight=0.0,
    )
    assert not any("disagree" in n.lower() for n in rec.notes)


# --- the raw-scale dead heat ----------------------------------------------
# `to_0_100` is min-max within the candidate set, so with two candidates any
# nonzero difference becomes 0-vs-100. `close_call_threshold` lives in that same
# normalized space, so it can never see the gap it exists to catch. These cover
# the third flag condition, which reads the raw values instead.
RAW_GAPS = {"ecr": 3.0, "vegas": 1.5}
WEIGHTS = {"ecr": 0.60, "vegas": 0.25, "injury": 0.10, "weather": 0.05}


def _two(ecr, vegas, **kw):
    kw.setdefault("close_call_threshold", 3.0)
    kw.setdefault("min_disagree_weight", 0.15)
    kw.setdefault("close_call_raw_gaps", RAW_GAPS)
    kw.setdefault("weights", WEIGHTS)
    return blend(
        week=5, scoring="ppr", players=_players(),
        signal_values={
            "ecr": {"1": SignalValue(ecr[0]), "2": SignalValue(ecr[1])},
            "vegas": {"1": SignalValue(vegas[0]), "2": SignalValue(vegas[1])},
        },
        higher_is_better={"ecr": False, "vegas": True},
        **kw,
    )


def test_a_two_player_dead_heat_is_flagged_despite_a_100_point_blend_gap():
    """The product's core promise, failing in the case that needs it most.

    ECR 12.0 against 12.1 and a tenth of an implied point is a coin flip by any
    reading of the inputs. Min-maxed over two candidates it renders as 100 vs 0 —
    maximum confidence — and the normalized threshold has nothing to catch.
    """
    rec = _two((12.0, 12.1), (24.1, 24.0))
    assert [s.final for s in rec.scores] == [100.0, 0.0]   # the blend still says this
    assert rec.close_call is True
    assert "nothing separates" in " ".join(rec.notes)


def test_a_real_gap_between_two_players_is_not_flagged():
    """The flag has to stay rare enough to mean something."""
    rec = _two((4.0, 30.0), (26.0, 19.0))
    assert rec.close_call is False


def test_one_signal_with_a_real_separation_vetoes_the_dead_heat():
    """ECR ties them, Vegas puts them five implied points apart. That is an edge,
    and flagging it would be the false alarm — hence unanimity, not any-of."""
    rec = _two((12.0, 12.1), (27.0, 22.0))
    assert rec.close_call is False


def test_a_lightly_weighted_signal_gets_no_vote_either_way():
    """Symmetry with the disagreement floor above: a signal too light to have
    changed the pick is also too light to certify a separation."""
    light = dict(WEIGHTS, vegas=0.02, ecr=0.83)
    # Vegas separates them, but at 2% it cannot veto ECR's dead heat.
    assert _two((12.0, 12.1), (27.0, 22.0), weights=light).close_call is True


def test_raw_dead_heat_note_formats_raw_values_to_two_decimals():
    """`:g` defaults to 6 significant figures, so a Vegas implied-total gap of
    1.222222 used to print verbatim in a close-call note (issue #40) instead of
    rounding like every other raw value this app shows a reader."""
    rec = _two((12.0, 12.0), (24.111111, 22.888889))
    note = " ".join(rec.notes)
    assert "vegas 24.11 vs 22.89" in note
    assert "24.111111" not in note and "22.888889" not in note


def test_no_raw_gaps_configured_leaves_behavior_exactly_as_before():
    """The mapping defaults to None, so every existing caller is untouched."""
    assert _two((12.0, 12.1), (24.1, 24.0), close_call_raw_gaps=None).close_call is False


def test_near_identical_weather_forecasts_produce_zero_final_differential():
    """PR2's fix, end to end: two forecasts that only differ by wind-measurement
    noise must not move `final` between two otherwise-identical candidates.
    Before quantization this is exactly Week 1's shape -- Caleb Williams 93.7
    vs Ja'Marr Chase 95.5, both clear football weather, rendered 0 vs 28.6.
    """
    from ff_startsit.sources.weather import score_conditions

    a_score = score_conditions(9.0, 0.0)
    b_score = score_conditions(10.4, 0.0)
    assert a_score == b_score            # same 5-point bucket, per test_weather.py

    players = _players()
    signal_values = {
        "ecr": {"1": SignalValue(10.0), "2": SignalValue(10.0)},   # tied elsewhere
        "weather": {"1": SignalValue(a_score), "2": SignalValue(b_score)},
    }
    rec = blend(
        week=1, scoring="ppr", players=players, signal_values=signal_values,
        higher_is_better={"ecr": False, "weather": True},
        weights={"ecr": 0.60, "weather": 0.10},
        close_call_threshold=5.0,
    )
    assert rec.scores[0].final == rec.scores[1].final   # zero differential


def test_weather_raw_gap_cannot_flag_or_veto_at_default_weights():
    """PR2's presentational fix (weather gains a raw gap) must not widen the
    close-call flag: weather's 0.10 default weight sits under the 0.15
    `min_disagree_weight` floor, so `_flag_raw_dead_heat` filters it out as a
    voter before it ever consults the new gap -- it can neither flag a
    disagreement nor veto (or manufacture) a dead heat. This is the test most
    likely to rot, per the remediation plan: it pins the *absence* of an
    effect.
    """
    from ff_startsit.config import Settings

    settings = Settings()   # real shipped defaults, not hand-picked test values
    players = _players()
    signal_values = {
        "ecr": {"1": SignalValue(12.0), "2": SignalValue(12.1)},   # a dead heat
        "weather": {"1": SignalValue(95.0), "2": SignalValue(80.0)},  # a real gap
    }
    rec = blend(
        week=5, scoring="ppr", players=players, signal_values=signal_values,
        higher_is_better={"ecr": False, "weather": True},
        weights=settings.weights,
        close_call_threshold=3.0,
        min_disagree_weight=settings.min_disagree_weight,
        close_call_raw_gaps=settings.close_call_raw_gaps,
    )
    assert rec.close_call is True                          # ecr alone is a dead heat...
    assert not any("weather" in n.lower() for n in rec.notes)   # ...but weather never votes


def test_replay_guard_pins_historical_normalized_values_through_weighted_final():
    """Constraint 1: `normalize.to_0_100` and `weighted_final` must never
    change, because the #7 calibrator re-blends logged `normalized` values
    from `results_log.jsonl` through `weighted_final` -- changing either makes
    every historical row unreplayable. This pins a logged decision's
    `normalized` blob (as `log_recommendation` would have written it) and its
    `final`, so a future edit to either function that shifts this value fails
    loudly rather than silently invalidating the calibration corpus.
    """
    normalized = {"ecr": 92.31, "vegas": 61.54, "injury": 100.0, "weather": 28.6}
    weights = {"ecr": 0.60, "vegas": 0.18, "injury": 0.12, "weather": 0.10}
    assert weighted_final(normalized, weights) == 81.32


def test_a_signal_with_no_configured_gap_cannot_veto():
    """injury is a bucketed status with no meaningful raw scale, so it is
    deliberately absent from the mapping and abstains."""
    rec = blend(
        week=5, scoring="ppr", players=_players(),
        signal_values={
            "ecr": {"1": SignalValue(12.0), "2": SignalValue(12.1)},
            "injury": {"1": SignalValue(100.0), "2": SignalValue(20.0)},
        },
        higher_is_better={"ecr": False, "injury": True},
        weights={"ecr": 0.60, "injury": 0.40},
        close_call_threshold=3.0, min_disagree_weight=0.15,
        close_call_raw_gaps=RAW_GAPS,
    )
    assert rec.close_call is True


def test_an_unavailable_raw_value_abstains_rather_than_blocking():
    rec = blend(
        week=5, scoring="ppr", players=_players(),
        signal_values={
            "ecr": {"1": SignalValue(12.0), "2": SignalValue(12.1)},
            "vegas": {"1": SignalValue(None, available=False),
                      "2": SignalValue(None, available=False)},
        },
        higher_is_better={"ecr": False, "vegas": True},
        weights=WEIGHTS,
        close_call_threshold=3.0, min_disagree_weight=0.15,
        close_call_raw_gaps=RAW_GAPS,
    )
    assert rec.close_call is True


# --- ruled-out players (unavailable_keys) ----------------------------------

def _three():
    return [
        Player(key="1", name="Alpha", team="KC", position="RB"),
        Player(key="2", name="Bravo", team="CHI", position="RB"),
        Player(key="3", name="Charlie", team="DET", position="RB"),
    ]


def _ruled_out_setup():
    """Charlie holds the best Vegas read but cannot play.

    Mirrors the live Week 1 shape that motivated this: a player with no ECR and
    an IR designation blended on Vegas alone and outranked healthy, ranked backs.
    """
    return _three(), {
        "ecr": {"1": SignalValue(1.0), "2": SignalValue(8.0),
                "3": SignalValue(None, available=False, note="no ECR rank")},
        "vegas": {"1": SignalValue(20.0), "2": SignalValue(21.0),
                  "3": SignalValue(30.0)},
    }


def _blend_three(unavailable_keys=()):
    players, signal_values = _ruled_out_setup()
    return blend(
        week=1, scoring="ppr", players=players, signal_values=signal_values,
        higher_is_better={"ecr": False, "vegas": True},
        weights={"ecr": 0.60, "vegas": 0.18},
        close_call_threshold=5.0,
        unavailable_keys=unavailable_keys,
    )


def test_ruled_out_player_is_listed_but_never_ranked():
    rec = _blend_three(unavailable_keys={"3"})
    by_key = {s.player.key: s for s in rec.scores}
    # Still present — this is your roster, he should not silently vanish...
    assert set(by_key) == {"1", "2", "3"}
    # ...but unscored, and therefore last and unstartable.
    assert by_key["3"].final is None
    assert rec.scores[-1].player.key == "3"
    assert any("not startable" in f for f in by_key["3"].flags)


def test_ruled_out_player_does_not_rescale_the_others():
    """The point of holding him out, not merely of unscoring him.

    ``to_0_100`` is min-max within the candidate set, so leaving Charlie in it
    anchors Vegas' maximum on a player who will not take a snap and drags every
    survivor's normalized value down.
    """
    kept = _blend_three()                              # Charlie scored
    held = _blend_three(unavailable_keys={"3"})        # Charlie held out

    kept_vegas = {s.player.key: s.normalized["vegas"] for s in kept.scores}
    held_vegas = {s.player.key: s.normalized["vegas"]
                  for s in held.scores if s.final is not None}

    # With Charlie in the set he owns the top of the Vegas scale.
    assert kept_vegas["3"] == 100.0
    assert kept_vegas["2"] < 100.0
    # With him out, the best *available* Vegas read is Bravo's.
    assert held_vegas["2"] == 100.0
    assert held_vegas["1"] == 0.0


def test_ruled_out_is_opt_in_so_the_waiver_pass_still_scores_him():
    """``find_stashes`` recommends exactly the players who cannot play, so the
    waiver pass must keep scoring them. Default off is what protects it."""
    rec = _blend_three()
    assert all(s.final is not None for s in rec.scores)
    assert not any("not startable" in f for s in rec.scores for f in s.flags)


def test_every_candidate_ruled_out_still_produces_a_ranking():
    """"All my RBs are out" is a real week; an empty table answers it worse."""
    rec = _blend_three(unavailable_keys={"1", "2", "3"})
    assert {s.player.key for s in rec.scores} == {"1", "2", "3"}
    assert all(s.final is not None for s in rec.scores)
    # And nothing is mislabelled as held out when nothing could be held out.
    assert not any("not startable" in f for s in rec.scores for f in s.flags)


# --- disagree_exempt -------------------------------------------------------

#: The shipped defaults. ``_share`` divides by the *total*, so these tests only
#: mean anything against a weight set that sums to 1.0 the way production's does:
#: with just ecr+injury, injury's share is 0.12/0.72 = 0.167 and clears the floor
#: it is supposed to fail.
_DEFAULT_WEIGHTS = {"ecr": 0.60, "vegas": 0.18, "injury": 0.12, "weather": 0.10}


def _disagreement(injury_weight, disagree_exempt=()):
    """ECR puts Alpha well ahead; ``injury`` favours Bravo (Questionable leader)."""
    players = _players()
    signal_values = {
        "ecr": {"1": SignalValue(1.0), "2": SignalValue(8.0)},
        "injury": {"1": SignalValue(75.0), "2": SignalValue(100.0)},
    }
    weights = dict(_DEFAULT_WEIGHTS, injury=injury_weight)
    return blend(
        week=1, scoring="ppr", players=players, signal_values=signal_values,
        higher_is_better={"ecr": False, "injury": True},
        weights=weights, close_call_threshold=5.0,
        min_disagree_weight=0.15, disagree_exempt=disagree_exempt,
    )


def test_injury_below_the_weight_floor_cannot_flag_without_the_exemption():
    """The bug: at 0.12 against a 0.15 floor, a Questionable leader passed
    silently over a healthy runner-up."""
    rec = _disagreement(0.12)
    assert rec.close_call is False


def test_exempt_injury_disagreement_is_a_note_not_a_close_call():
    """F5(a): the exemption used to make an injury-only disagreement flag
    close_call outright — any Questionable tag on the leader tripped it,
    which over-fired in practice (5 of 9 multi-candidate groups in one real
    run). It still gets *detected* below the real weight floor, and still
    gets logged (the note lands in `results_log.jsonl` either way, for later
    analysis) — it just no longer sets `close_call` on its own. The
    designation itself is not hidden by this: it's already on the player's
    own `flags`, shown in every table regardless of `close_call`."""
    rec = _disagreement(0.12, disagree_exempt={"injury"})
    assert rec.close_call is False
    assert any("injury favors Bravo" in n for n in rec.notes)


def test_a_genuinely_weighted_disagreement_still_flags_even_if_exempt():
    """The demotion only affects a disagreement that clears the floor *via*
    the exemption. One that clears it on real weight alone is untouched,
    exempt or not."""
    rec = _disagreement(0.40, disagree_exempt={"injury"})
    assert rec.close_call is True
    assert any("injury favors Bravo" in n for n in rec.notes)


def test_exemption_does_not_resurrect_a_zero_weight_signal():
    """CLAUDE.md's explicit warning: a learned-weights file that zeroes a signal
    must silence it, exemption or not."""
    rec = _disagreement(0.0, disagree_exempt={"injury"})
    assert rec.close_call is False


def test_exemption_is_per_signal_and_does_not_widen_the_floor():
    """Exempting injury must not also let weather (0.10) through."""
    players = _players()
    signal_values = {
        "ecr": {"1": SignalValue(1.0), "2": SignalValue(8.0)},
        "weather": {"1": SignalValue(40.0), "2": SignalValue(90.0)},
    }
    rec = blend(
        week=1, scoring="ppr", players=players, signal_values=signal_values,
        higher_is_better={"ecr": False, "weather": True},
        weights=_DEFAULT_WEIGHTS,
        close_call_threshold=5.0, min_disagree_weight=0.15,
        disagree_exempt={"injury"},
    )
    assert rec.close_call is False


# --- F5(b): the starter-boundary flag ---------------------------------------

def _four():
    return [Player(key=str(i), name=f"P{i}", team="KC", position="WR")
            for i in range(1, 5)]


def _blend_four(ranks, starter_count):
    players = _four()
    signal_values = {"ecr": {str(i): SignalValue(ranks[i - 1]) for i in range(1, 5)}}
    return blend(
        week=1, scoring="ppr", players=players, signal_values=signal_values,
        higher_is_better={"ecr": False}, weights={"ecr": 1.0},
        close_call_threshold=1.0, starter_count=starter_count,
    )


def test_starter_boundary_flags_rank_two_vs_three_not_the_top_two():
    """A league starting 2 WR sets its lineup on the WR2/WR3 decision, not
    WR1/WR2 -- the top-two check alone never saw workTG's real Week 1 flag."""
    rec = _blend_four([1.0, 20.0, 20.1, 90.0], starter_count=2)
    assert rec.close_call is True
    assert any("Last starting spot" in n for n in rec.notes)
    assert not any("Too close to call" in n for n in rec.notes)


def test_starter_boundary_is_silent_when_that_pair_is_not_close():
    rec = _blend_four([1.0, 10.0, 60.0, 90.0], starter_count=2)
    assert rec.close_call is False


def test_starter_count_one_does_not_duplicate_the_top_two_note():
    """N=1 is exactly the top-two pair already checked -- it must not fire a
    second, redundant note for the identical comparison."""
    rec = _blend_four([1.0, 1.05, 60.0, 90.0], starter_count=1)
    assert rec.close_call is True
    notes = [n for n in rec.notes if "close to call" in n.lower()
             or "coin flip" in n.lower()]
    assert len(notes) == 1


def test_starter_count_none_leaves_behavior_unchanged():
    """The default: every existing caller that never passes `starter_count`
    must see none of this."""
    rec = _blend_four([1.0, 20.0, 20.1, 90.0], starter_count=None)
    assert rec.close_call is False


def test_starter_boundary_needs_a_body_below_the_line():
    """Fewer scored candidates than `starter_count + 1` means there's no one
    to compare the last starter against -- must not crash or flag."""
    rec = _blend_four([1.0, 20.0, 20.1, 90.0], starter_count=4)
    assert rec.close_call is False
