"""Waiver scoring: the one candidate set, the ECR floor, and safe drops.

The headline invariant — free agents and roster players normalized *together* —
is what makes "this add beats your WR4" a true sentence rather than a
comparison of two unrelated 0-100 scales.
"""

from ff_startsit.config import Settings
from ff_startsit.models import Player, SignalValue
from ff_startsit.sources.base import Signal
from ff_startsit.waivers.models import (ACQ_FAAB, ACQ_PRIORITY, ACQ_UNKNOWN,
                                        LeagueRules, PoolPlayer, WaiverTarget)
from ff_startsit.waivers.score import (bye_gaps, dedupe_players, depth_ratio,
                                       droppable, find_stashes, has_ecr,
                                       keep_counts, pick_adds, score_positions,
                                       signal_coverage, starter_demand,
                                       suggest_bid)


def _p(key, name, pos, team="KC"):
    return Player(key=key, name=name, team=team, position=pos)


class _FakeECR(Signal):
    """ECR with a fixed rank table; anyone absent is genuinely unranked."""

    name = "ecr"
    higher_is_better = False

    def __init__(self, ranks):
        self.ranks = ranks

    def is_available(self):
        return True

    def fetch(self, week, players):
        return {p.key: SignalValue(raw=float(self.ranks[p.key]) if p.key in self.ranks
                                   else None,
                                   available=p.key in self.ranks,
                                   note="" if p.key in self.ranks else "no ECR rank")
                for p in players}


def _score(players, ranks, settings=None):
    settings = settings or Settings()
    return score_positions(settings, players, 9, signals=[_FakeECR(ranks)])


# --- the one candidate set ------------------------------------------------
def test_free_agents_and_roster_share_one_normalize_set():
    """Scored apart, the best free agent is always 100 and the comparison to
    your bench is meaningless. Scored together, the numbers rank each other."""
    roster = [_p("r1", "Stud", "WR"), _p("r2", "Bench", "WR")]
    pool = [_p("f1", "Free Man", "WR")]
    _, index = _score(roster + pool, {"r1": 5, "r2": 60, "f1": 30})

    assert index["r1"].final == 100.0     # best in the set
    assert index["r2"].final == 0.0       # worst in the set
    # The free agent lands *between* them rather than topping his own scale.
    assert 0 < index["f1"].final < 100


def test_scoring_a_waiver_pass_never_writes_the_results_log(tmp_path):
    """#7 invariant. A waiver row packs a hundred players — most on other
    people's teams — into one 'decision' whose pairwise concordance is noise,
    and the log is append-only."""
    settings = Settings(data_dir=tmp_path)
    players = [_p("a", "A", "RB"), _p("b", "B", "RB")]
    score_positions(settings, players, 9, signals=[_FakeECR({"a": 1, "b": 2})])
    assert not settings.results_log_path.exists()


def test_dedupe_keeps_the_roster_copy_of_a_player_in_both_lists():
    roster = [_p("x", "Claimed Guy", "RB")]
    pool = [_p("x", "Claimed Guy", "RB")]
    merged = dedupe_players(roster, pool)
    assert len(merged) == 1


# --- the ECR floor --------------------------------------------------------
def test_a_player_with_no_ecr_is_not_recommended():
    """Without ECR the blend runs on injury alone, which scores every healthy
    anonymous backup at the top — and would recommend adding them."""
    roster = [_p("r1", "Starter", "RB"), _p("r2", "Bench", "RB"),
              _p("r3", "Deep", "RB"), _p("r4", "Deeper", "RB")]
    pool = [PoolPlayer(_p("f1", "Unranked Nobody", "RB"))]
    ranks = {"r1": 5, "r2": 20, "r3": 40, "r4": 55}   # f1 deliberately absent
    _, index = _score(roster + [pool[0].player], ranks)

    assert not has_ecr(index["f1"])
    rules = LeagueRules(roster_slots={"RB": 2})
    drops = droppable([index[p.key] for p in roster], rules)
    assert pick_adds(index, pool, drops, rules) == []


def test_an_unranked_roster_player_is_never_a_drop_candidate():
    """On a Tuesday, 'unranked' usually means 'on bye' — dropping your RB1
    because his team didn't play is the exact bad advice this guards."""
    roster = [_p("r1", "Starter", "RB"), _p("r2", "Bench", "RB"),
              _p("r3", "On Bye", "RB", team=None)]
    _, index = _score(roster, {"r1": 5, "r2": 30})
    drops = droppable([index[p.key] for p in roster], LeagueRules(roster_slots={"RB": 1}))
    assert "r3" not in {d.score.player.key for d in drops}


# --- drops ----------------------------------------------------------------
def test_lineup_starters_are_never_droppable():
    roster = [_p("r1", "Starter", "WR"), _p("r2", "Bench", "WR")]
    _, index = _score(roster, {"r1": 5, "r2": 60})
    rules = LeagueRules(roster_slots={"WR": 1})
    scores = [index[p.key] for p in roster]
    assert {d.score.player.key for d in droppable(scores, rules)} == {"r2"}
    assert droppable(scores, rules, protected={"r2"}) == []


def test_a_position_is_never_cut_below_its_starting_requirement():
    roster = [_p("k1", "Only Kicker", "K")]
    _, index = _score(roster, {"k1": 3})
    assert droppable([index["k1"]], LeagueRules(roster_slots={"K": 1})) == []


def test_the_flex_body_is_guarded_by_protected_not_by_a_blanket_spare():
    """Reserving a spare at every flex position would hold three bodies back for
    one slot, on top of the `protected` guard that already shields the real FLEX
    pick — and nothing would ever be droppable."""
    keep = keep_counts(LeagueRules(roster_slots={"QB": 1, "RB": 2, "WR": 2, "TE": 1}))
    assert keep == {"QB": 1, "RB": 2, "WR": 2, "TE": 1}


# --- adds -----------------------------------------------------------------
def test_an_add_is_only_suggested_when_it_beats_a_droppable_player():
    roster = [_p("r1", "Stud", "WR"), _p("r2", "Solid", "WR"), _p("r3", "Bench", "WR")]
    pool = [PoolPlayer(_p("f1", "Better", "WR"), percent_owned=30.0),
            PoolPlayer(_p("f2", "Worse", "WR"), percent_owned=1.0)]
    ranks = {"r1": 3, "r2": 10, "r3": 70, "f1": 25, "f2": 90}
    _, index = _score(roster + [pp.player for pp in pool], ranks)

    rules = LeagueRules(roster_slots={"WR": 2})
    drops = droppable([index[p.key] for p in roster], rules)
    adds = pick_adds(index, pool, drops, rules)

    assert [a.score.player.key for a in adds] == ["f1"]
    assert adds[0].drop.player.key == "r3"
    assert adds[0].margin > 0


def test_each_add_is_paired_with_a_distinct_drop():
    """Two adds and one droppable player is one add: a pickup you have no room
    for isn't actionable."""
    roster = [_p("r1", "Stud", "WR"), _p("r2", "Bench", "WR")]
    pool = [PoolPlayer(_p("f1", "Good", "WR")), PoolPlayer(_p("f2", "Also Good", "WR"))]
    ranks = {"r1": 3, "r2": 90, "f1": 20, "f2": 25}
    _, index = _score(roster + [pp.player for pp in pool], ranks)
    rules = LeagueRules(roster_slots={"WR": 1})
    drops = droppable([index[p.key] for p in roster], rules)
    adds = pick_adds(index, pool, drops, rules)
    assert len(adds) == 1


def test_add_reasons_name_the_journalists_and_the_column():
    from ff_startsit.waivers.models import ColumnMention

    roster = [_p("r1", "Stud", "WR"), _p("r2", "Bench", "WR")]
    pool = [PoolPlayer(_p("f1", "Target", "WR"), percent_owned=44.0)]
    ranks = {"r1": 3, "r2": 90, "f1": 20}
    _, index = _score(roster + [pool[0].player], ranks)
    rules = LeagueRules(roster_slots={"WR": 1})
    drops = droppable([index[p.key] for p in roster], rules)
    adds = pick_adds(index, pool, drops, rules,
                     journalist_ranks={"f1": 21.5},
                     mentions={"f1": [ColumnMention("Dave Richard", "u", "f1", "go get him")]})
    blob = " ".join(adds[0].reasons)
    assert "21.5" in blob and "Dave Richard" in blob and "44%" in blob


# --- bidding --------------------------------------------------------------
def _target(percent_owned=None):
    return WaiverTarget(score=None,
                        pool=PoolPlayer(_p("f", "F", "WR"), percent_owned=percent_owned))


def test_faab_bid_scales_with_conviction_and_stays_within_the_budget():
    rules = LeagueRules(acquisition_type=ACQ_FAAB, faab_budget=100.0)
    small = suggest_bid(_target(), rules, 80.0, conviction=0.05)
    large = suggest_bid(_target(), rules, 80.0, conviction=1.0)
    assert "$" in small and "$" in large
    assert int(small.split("$")[1].split()[0]) < int(large.split("$")[1].split()[0])
    assert int(large.split("$")[1].split()[0]) <= 80


def test_the_bid_does_not_depend_on_who_the_drop_happens_to_be():
    """Conviction comes from the add's own standing, never from a subtraction
    against the drop.

    It used to be ``margin / MAX_CONVICTION_MARGIN``, and ``margin`` crossed two
    normalization frames — so the identical free agent was worth a different
    dollar figure depending on which position sat at the head of your drop list.
    ``suggest_bid`` can no longer see the drop at all, which is the fix stated as
    a signature.
    """
    rules = LeagueRules(acquisition_type=ACQ_FAAB, faab_budget=100.0)
    cheap = WaiverTarget(score=None, margin=2.0, pool=PoolPlayer(_p("f", "F", "WR")))
    rich = WaiverTarget(score=None, margin=40.0, pool=PoolPlayer(_p("f", "F", "WR")))
    assert (suggest_bid(cheap, rules, 80.0, conviction=0.6)
            == suggest_bid(rich, rules, 80.0, conviction=0.6))
    # And a cross-position pair, which carries no margin at all, still bids.
    none = WaiverTarget(score=None, margin=None, pool=PoolPlayer(_p("f", "F", "WR")))
    assert "$" in suggest_bid(none, rules, 80.0, conviction=0.6)


def test_faab_bid_is_a_share_of_remaining_not_total_budget():
    rules = LeagueRules(acquisition_type=ACQ_FAAB, faab_budget=100.0)
    assert "$3 left" in suggest_bid(_target(), rules, 3.0, conviction=1.0)


def test_no_faab_left_says_so():
    rules = LeagueRules(acquisition_type=ACQ_FAAB, faab_budget=100.0)
    assert suggest_bid(_target(), rules, 0.0, conviction=1.0) == "no FAAB left"


def test_priority_leagues_get_claim_language_not_dollars():
    rules = LeagueRules(acquisition_type=ACQ_PRIORITY)
    assert "claim" in suggest_bid(_target(), rules, None, conviction=1.0)
    assert "$" not in suggest_bid(_target(), rules, None, conviction=1.0)


def test_unknown_acquisition_type_renders_no_bid_at_all():
    """A dollar figure in a priority league, or a claim ranking in a FAAB one,
    is advice for somebody else's league."""
    assert suggest_bid(_target(), LeagueRules(acquisition_type=ACQ_UNKNOWN), 50.0,
                       conviction=1.0) == ""


# --- stashes & byes -------------------------------------------------------
def test_hurt_free_agents_are_stashes_not_adds():
    pool = [PoolPlayer(_p("f1", "Shelved", "RB"), injury_status="IR")]
    _, index = _score([pool[0].player, _p("r1", "R", "RB")], {"f1": 40, "r1": 10})
    stashes = find_stashes(index, pool, taken=set(), bye_teams=set())
    assert [s.score.player.key for s in stashes] == ["f1"]
    assert "IR" in stashes[0].reason


def test_ranked_players_on_bye_are_flagged_as_cheap_stashes():
    pool = [PoolPlayer(_p("f1", "Bye Guy", "RB", team="SF"))]
    _, index = _score([pool[0].player, _p("r1", "R", "RB")], {"f1": 40, "r1": 10})
    stashes = find_stashes(index, pool, taken=set(), bye_teams={"SF"})
    assert "bye" in stashes[0].reason


def test_a_player_already_recommended_as_an_add_is_not_also_a_stash():
    pool = [PoolPlayer(_p("f1", "Shelved", "RB"), injury_status="IR")]
    _, index = _score([pool[0].player], {"f1": 40})
    assert find_stashes(index, pool, taken={"f1"}, bye_teams=set()) == []


def test_bye_gaps_flag_a_week_you_cannot_field_a_position():
    roster = [_p("r1", "A", "RB", team="SF"), _p("r2", "B", "RB", team="SF")]
    gaps = bye_gaps(roster, LeagueRules(roster_slots={"RB": 2}), 9,
                    {9: {"SF", "KC"}, 10: {"KC"}})
    assert [(g.week, g.position, g.available) for g in gaps] == [(10, "RB", 0)]


def test_an_unreadable_week_is_skipped_not_treated_as_a_league_wide_bye():
    """An empty set would read as 'all 32 teams on bye' and fire a false alarm
    for every position, every week the schedule endpoint hiccupped."""
    roster = [_p("r1", "A", "RB", team="SF")]
    assert bye_gaps(roster, LeagueRules(roster_slots={"RB": 1}), 9, {}) == []


# --- signal coverage (what a dress rehearsal reports) ----------------------
def test_signal_coverage_counts_only_usable_readings():
    """A signal that answered "I don't know" for a player didn't cover him, and
    a rehearsal that can't tell the difference can't prove anything."""
    from ff_startsit.models import PlayerScore

    a, b = Player("1", "A", "KC", "WR"), Player("2", "B", "SF", "WR")
    index = {
        "1": PlayerScore(player=a, raw={"ecr": SignalValue(raw=3.0),
                                        "vegas": SignalValue(raw=24.5),
                                        "weather": SignalValue(raw=None,
                                                               available=False)}),
        # No ecr *reading* (present but empty), and no weather key at all.
        "2": PlayerScore(player=b, raw={"ecr": SignalValue(raw=None),
                                        "vegas": SignalValue(raw=21.0)}),
    }
    assert signal_coverage(index, [a, b]) == {"ecr": 1, "vegas": 2, "weather": 0}


def test_signal_coverage_skips_players_that_were_never_scored():
    from ff_startsit.models import PlayerScore

    a = Player("1", "A", "KC", "WR")
    ghost = Player("9", "Unscored", "SF", "WR")
    index = {"1": PlayerScore(player=a, raw={"ecr": SignalValue(raw=3.0)})}
    assert signal_coverage(index, [a, ghost]) == {"ecr": 1}


# --- one scale across positions -------------------------------------------
# `to_0_100` is min-max *within* a position, so a WR's 78 and a TE's 78 came from
# different populations and their difference is not a number. These cover the
# places that used to subtract them anyway.
_LEAGUE = LeagueRules(roster_slots={"QB": 1, "RB": 2, "WR": 2, "TE": 1, "DEF": 1},
                      team_count=12)


def test_depth_ratio_puts_every_position_on_one_scale():
    """A rank means nothing until you divide it by what the league starts there."""
    players = [_p("rb", "Rb", "RB"), _p("qb", "Qb", "QB")]
    _, index = _score(players, {"rb": 24, "qb": 12})
    # RB24 in a league starting 24 and QB12 in a league starting 12 are the same
    # player in the only sense that matters: the last one who'd start anywhere.
    assert depth_ratio(index["rb"], _LEAGUE) == 1.0
    assert depth_ratio(index["qb"], _LEAGUE) == 1.0
    assert starter_demand("RB", _LEAGUE) == 24
    assert starter_demand("QB", _LEAGUE) == 12


def test_a_margin_is_printed_only_when_the_add_and_drop_share_a_position():
    roster = [_p("r1", "Wr Stud", "WR"), _p("r2", "Wr Bench", "WR"),
              _p("r5", "Wr Spare", "WR"),
              _p("r3", "Te Stud", "TE"), _p("r4", "Te Bench", "TE")]
    pool = [PoolPlayer(_p("f1", "Free Wr", "WR"))]
    # TE40 where twelve start is deeper than WR60 where twenty-four do.
    ranks = {"r1": 5, "r2": 44, "r5": 60, "r3": 3, "r4": 40, "f1": 18}
    _, index = _score(roster + [pool[0].player], ranks)
    drops = droppable([index[p.key] for p in roster], _LEAGUE)

    # The most-droppable body is the TE, so the pairing crosses positions.
    assert drops[0].score.player.position == "TE"
    add = pick_adds(index, pool, drops, _LEAGUE)[0]
    assert add.drop.player.position != add.score.player.position
    assert add.margin is None, "a WR score minus a TE score is not a quantity"
    assert "takes the roster spot" in " ".join(add.reasons)

    # Same position: the subtraction is real, so the number comes back.
    same = pick_adds(index, pool, [d for d in drops
                                   if d.score.player.position == "WR"], _LEAGUE)[0]
    assert same.margin is not None
    assert "scores" in " ".join(same.reasons)


def test_the_best_of_a_bad_pool_no_longer_wins_by_arithmetic():
    """Min-max hands the best player in any group a 100 whoever he is. With two
    mediocre defenses on the wire, the better one scored 100 and led the add list
    ahead of a running back who would actually start somewhere.

    Note what this does *not* claim: depth ratio ranks by starter scarcity, not by
    points over replacement, so the genuine DEF1 still outranks a fringe RB. It
    fixes the group being empty, not the two positions being different games.
    """
    pool = [PoolPlayer(_p("d1", "Less Bad Def", "DEF")),
            PoolPlayer(_p("d2", "Worse Def", "DEF")),
            PoolPlayer(_p("rb", "Startable Rb", "RB"))]
    roster = [_p("r1", "Rb1", "RB"), _p("r2", "Rb2", "RB"), _p("r3", "Rb3", "RB")]
    ranks = {"d1": 20, "d2": 28, "rb": 20, "r1": 4, "r2": 9, "r3": 55}
    _, index = _score(roster + [pp.player for pp in pool], ranks)

    # The old ordering, stated so the regression is visible: the worse player of
    # the two outscores the better one because his group was shallower.
    assert index["d1"].final == 100.0 > index["rb"].final

    drops = droppable([index[p.key] for p in roster], _LEAGUE)
    adds = pick_adds(index, pool, drops, _LEAGUE)
    assert adds[0].score.player.key == "rb"
    assert depth_ratio(index["d1"], _LEAGUE) > depth_ratio(index["rb"], _LEAGUE)


def test_droppable_orders_by_depth_past_starter_demand_not_by_raw_score():
    """This list is consumed across positions — its head is what an add is paired
    with — so ordering it by `final` picked whichever position happened to have the
    widest score spread."""
    roster = [_p("w1", "Wr1", "WR"), _p("w2", "Wr2", "WR"), _p("w3", "Wr3", "WR"),
              _p("q1", "Qb1", "QB"), _p("q2", "Qb2", "QB")]
    # The spare WR is a fringe starter; the spare QB is nowhere near one. Both are
    # last in their own group, so both normalize to 0.0 and `final` cannot separate
    # them at all.
    ranks = {"w1": 2, "w2": 8, "w3": 26, "q1": 1, "q2": 40}
    _, index = _score(roster, ranks)
    drops = droppable([index[p.key] for p in roster], _LEAGUE)

    assert index["w3"].final == index["q2"].final == 0.0
    assert drops[0].score.player.key == "q2", "QB40 is deeper than WR26"


def test_roster_filler_is_not_offered_however_good_his_pool_looks():
    pool = [PoolPlayer(_p("f1", "Deep Guy", "WR"))]
    roster = [_p("r1", "Wr1", "WR"), _p("r2", "Wr2", "WR"), _p("r3", "Wr3", "WR")]
    ranks = {"f1": 90, "r1": 3, "r2": 10, "r3": 30}   # WR90 where 24 start
    _, index = _score(roster + [pool[0].player], ranks)
    drops = droppable([index[p.key] for p in roster], _LEAGUE)
    assert pick_adds(index, pool, drops, _LEAGUE) == []
