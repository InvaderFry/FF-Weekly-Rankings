"""RB/WR and WR/TE flex slots are lineup slots too.

Both platforms have flex slots narrower than FLEX: ESPN lineup slots 3
("RB/WR") and 5 ("WR/TE"), Sleeper's ``WRRB_FLEX`` and ``REC_FLEX``. The
parsers knew only FLEX and SUPER_FLEX, so these slots vanished. Once a league
reporting position slots with no flex was (correctly) read as having no flex,
a nine-starter RB/WR league built eight slots, and its flex starter lost his
drop protection. Found in review of PR #92.
"""
from ff_startsit import report
from ff_startsit.availability import _slot_accepts, slot_positions
from ff_startsit.models import FLEX_SLOT_POSITIONS, Player, PlayerScore
from ff_startsit.roster.espn import parse_league_rules as parse_espn
from ff_startsit.roster.sleeper import parse_league_rules as parse_sleeper
from ff_startsit.waivers.models import LeagueRules


def _espn(counts):
    return parse_espn({"settings": {"rosterSettings": {"lineupSlotCounts": counts}}})


def test_espn_reads_rb_wr_and_wr_te_slots():
    rules = _espn({"0": 1, "2": 2, "4": 2, "6": 1, "3": 1, "5": 1, "23": 1,
                   "16": 1, "17": 1, "20": 6, "21": 1})
    assert rules.flex_slots == {"RB/WR": 1, "WR/TE": 1, "FLEX": 1}


def test_sleeper_reads_wrrb_and_rec_flex():
    rules = parse_sleeper({"roster_positions": [
        "QB", "RB", "RB", "WR", "WR", "TE", "WRRB_FLEX", "REC_FLEX", "SUPER_FLEX",
        "K", "DEF", "BN", "BN", "IR"]})
    assert rules.flex_slots == {"RB/WR": 1, "WR/TE": 1, "SUPER_FLEX": 1}


def test_a_nine_starter_rb_wr_league_builds_nine_slots():
    """The review's reproduction: QB, 2 RB, 2 WR, TE, RB/WR, K, DEF."""
    rules = _espn({"0": 1, "2": 2, "4": 2, "6": 1, "3": 1, "16": 1, "17": 1, "20": 7})
    slots = report.league_slots(rules)
    assert len(slots) == 9
    assert slots.count("RB/WR") == 1 and "FLEX" not in slots


def test_narrow_flex_slots_fill_before_broad_ones():
    rules = LeagueRules(roster_slots={"QB": 1, "RB": 1, "WR": 1, "TE": 1},
                        flex_slots={"SUPER_FLEX": 1, "FLEX": 1, "WR/TE": 1, "RB/WR": 1})
    assert report.league_slots(rules) == ["QB", "RB", "WR", "TE",
                                          "RB/WR", "WR/TE", "FLEX", "SUPER_FLEX"]


def _ps(key, pos, final):
    return PlayerScore(Player(key, key, "KC", pos), final=final)


def _by_pos(*scores):
    by = {}
    for s in scores:
        by.setdefault(s.player.position, []).append(s)
    for group in by.values():
        group.sort(key=lambda s: -s.final)
    return by


def test_an_rb_wr_slot_never_takes_a_tight_end():
    by = _by_pos(_ps("te2", "TE", 99.0), _ps("te1", "TE", 100.0),
                 _ps("rb1", "RB", 90.0), _ps("rb2", "RB", 10.0))
    lineup = report.build_lineup(by, slots=["RB", "TE", "RB/WR"])
    assert dict((s, p.player.key) for s, p in lineup)["RB/WR"] == "rb2"


def test_a_wr_te_slot_never_takes_a_running_back():
    by = _by_pos(_ps("rb1", "RB", 100.0), _ps("rb2", "RB", 99.0),
                 _ps("wr1", "WR", 90.0), _ps("te1", "TE", 50.0), _ps("te2", "TE", 5.0))
    lineup = report.build_lineup(by, slots=["RB", "WR", "TE", "WR/TE"])
    assert dict((s, p.player.key) for s, p in lineup)["WR/TE"] == "te2"


def test_a_partial_flex_slot_draws_from_the_pooled_flex_ranking():
    """Per-position scores aren't comparable across positions; the pooled
    RB/WR/TE ranking is, and any subset of it is still a valid ranking."""
    by = _by_pos(_ps("rb1", "RB", 100.0), _ps("rb2", "RB", 100.0),
                 _ps("wr1", "WR", 100.0), _ps("wr2", "WR", 40.0))
    pool = [_ps("te9", "TE", 99.0), _ps("wr2", "WR", 80.0), _ps("rb2", "RB", 70.0)]
    lineup = report.build_lineup(by, flex_pool=pool, slots=["RB", "WR", "RB/WR"])
    assert dict((s, p.player.key) for s, p in lineup)["RB/WR"] == "wr2"
    assert lineup.flex_basis == "pooled" and lineup.caveat


def test_waiver_drop_protection_covers_the_rb_wr_starter():
    from ff_startsit.waivers.build import _lineup_keys

    rules = _espn({"0": 1, "2": 2, "4": 2, "6": 1, "3": 1, "20": 7})
    mine = [_ps("qb", "QB", 90.0), _ps("rb1", "RB", 90.0), _ps("rb2", "RB", 80.0),
            _ps("rb3", "RB", 70.0), _ps("wr1", "WR", 90.0), _ps("wr2", "WR", 80.0),
            _ps("te", "TE", 80.0), _ps("rb4", "RB", 20.0)]
    protected = _lineup_keys(mine, rules)
    assert "rb3" in protected and "rb4" not in protected


def test_holes_in_partial_flex_slots_stream_the_right_positions():
    assert set(_slot_accepts("RB/WR")) == {"RB", "WR"}
    assert set(_slot_accepts("WR/TE")) == {"WR", "TE"}
    assert set(slot_positions("RB/WR")) == {"RB", "WR"}


def test_one_table_says_what_each_flex_slot_takes():
    """report and availability used to keep separate copies; now both read
    models.FLEX_SLOT_POSITIONS."""
    for slot, positions in FLEX_SLOT_POSITIONS.items():
        assert set(report.SLOT_POSITIONS[slot]) == set(positions)
        assert set(_slot_accepts(slot)) == set(positions)
