"""The waiver pass reads the roster as the platform actually holds it.

Four findings from the live Week 9 report, each a recommendation the owner
could not act on or a sentence that wasn't true:

* "Malik Willis (QB), drop Jadarian Price (RB)" in a one-QB league whose owner
  already carried two quarterbacks — and Price sat in an IR slot, so dropping
  him would not have opened the roster spot Willis needed.
* AndyLOT's kicker was on bye; the report printed "only 0 healthy K for 1
  slot(s)" and suggested no kicker, because a player on bye has no ECR and so
  never entered the streaming pool.
* AndyLOT had nobody droppable at all, yet read "Nothing on the wire beats
  anyone you could drop", which claims a comparison that never ran.
"""

from ff_startsit.config import Settings
from ff_startsit.models import Player, SignalValue
from ff_startsit.roster.espn import IR_SLOT_ID, parse_team_reserve
from ff_startsit.roster.sleeper import build_league_teams
from ff_startsit.sources.base import Signal
from ff_startsit.sources.schedule import ScheduleProvider
from ff_startsit.waivers.build import build_bundle
from ff_startsit.waivers.models import FantasyTeam, PoolPlayer, WaiverBundle
from ff_startsit.waivers.render import render_waiver_digest
from ff_startsit.waivers.score import must_swap_in_position

from .test_waiver_build import (MINE, RANKS, RULES, THEIRS, _NoSchedule, _p,
                                _Provider, _no_journalists)  # noqa: F401



class _ECR(Signal):
    name = "ecr"
    higher_is_better = False

    def __init__(self, ranks):
        self.ranks = ranks

    def is_available(self):
        return True

    def fetch(self, week, players):
        return {p.key: SignalValue(raw=float(self.ranks[p.key]) if p.key in self.ranks else None,
                                   available=p.key in self.ranks) for p in players}


def _bundle(tmp_path, mine, pool, ranks, reserve=frozenset(), schedule=None):
    teams = [FantasyTeam("1", "My Squad", tuple(mine), is_mine=True, faab_spent=20.0,
                         reserve=frozenset(reserve)),
             FantasyTeam("2", "Rival FC", tuple(THEIRS))]
    return build_bundle(Settings(data_dir=tmp_path), "work",
                        _Provider(pool=pool, teams=teams), mine, 9,
                        schedule=schedule or _NoSchedule(), signals=[_ECR(ranks)],
                        include_columns=False, preseason=False, rehearse=False,
                        include_trades=False)


# --- IR ---------------------------------------------------------------------
def test_an_ir_player_is_never_paired_with_an_add(tmp_path):
    """The worst-ranked body on the roster is on IR: he would have headed the
    drop list. Cutting him frees an IR slot, so the add pairs elsewhere and he
    is listed as an IR-only cut."""
    mine = MINE + [_p("m12", "Irs Rb", "RB")]
    ranks = dict(RANKS, m12=95)
    b = _bundle(tmp_path, mine, [PoolPlayer(_p("f1", "Free Wr", "WR", team="SF"))],
                ranks, reserve={"m12"})
    assert all(a.drop.player.key != "m12" for a in b.adds)
    assert "m12" not in {d.score.player.key for d in b.drops}
    assert [d.score.player.key for d in b.ir_drops] == ["m12"]
    assert b.ir_drops[0].on_reserve
    assert "frees an IR slot, not a roster spot" in b.ir_drops[0].reason
    md = render_waiver_digest(9, [b])
    assert "Irs Rb" in md and "frees an IR slot" in md


def test_without_reserve_data_the_same_player_is_an_ordinary_drop(tmp_path):
    """The control: the only thing keeping him out of the pairing is the slot."""
    mine = MINE + [_p("m12", "Irs Rb", "RB")]
    b = _bundle(tmp_path, mine, [PoolPlayer(_p("f1", "Free Wr", "WR", team="SF"))],
                dict(RANKS, m12=95))
    assert "m12" in {d.score.player.key for d in b.drops}
    assert b.ir_drops == []


def test_espn_reads_the_ir_slot():
    team = {"roster": {"entries": [
        {"lineupSlotId": IR_SLOT_ID, "playerPoolEntry": {"player": {"id": 7}}},
        {"lineupSlotId": 20, "playerPoolEntry": {"player": {"id": 8}}},
        {"lineupSlotId": 2, "playerPoolEntry": {"player": {"id": 9}}},
    ]}}
    assert parse_team_reserve(team) == frozenset({"espn-7"})


def test_sleeper_reads_the_reserve_list():
    rosters = [{"roster_id": 1, "owner_id": "u1", "players": ["1", "2"],
                "reserve": ["2"], "settings": {}}]
    teams = build_league_teams(rosters, {"u1": "Me"}, {}, "u1")
    assert teams[0].reserve == frozenset({"2"})


# --- the roster-aware QB/TE cap --------------------------------------------
def test_a_third_quarterback_can_only_replace_a_quarterback():
    one_qb = RULES
    assert must_swap_in_position("QB", "RB", one_qb, {"QB": 2})
    assert not must_swap_in_position("QB", "RB", one_qb, {"QB": 1})   # a backup is fine
    assert not must_swap_in_position("QB", "QB", one_qb, {"QB": 2})   # like-for-like
    assert must_swap_in_position("TE", "WR", one_qb, {"TE": 2})
    assert not must_swap_in_position("RB", "WR", one_qb, {"RB": 6})   # RB is uncapped
    assert not must_swap_in_position("QB", "RB", one_qb, None)        # rule off


def _season(monkeypatch, extra):
    base = {"m4": 65, "t3": 68, "t4": 66, "m7": 240, "f1": 120}
    base.update(extra)
    monkeypatch.setattr("ff_startsit.waivers.build.SeasonValueProvider.fetch",
                        lambda self, players, scoring: {p.key: base.get(p.key, 150)
                                                        for p in players})


def test_willis_for_price_becomes_a_quarterback_swap(tmp_path, monkeypatch):
    """Two QBs in a one-QB league; a startable QB on the wire. He may replace
    the backup quarterback, never a running back."""
    _season(monkeypatch, {"f3": 90, "m13": 200, "m4": 260})
    mine = MINE + [_p("m13", "My Qb2", "QB")]
    ranks = dict(RANKS, m13=25, f3=5)
    pool = [PoolPlayer(_p("f3", "Free Qb", "QB"))]
    b = _bundle(tmp_path, mine, pool, ranks)
    qb_adds = [a for a in b.adds if a.score.player.key == "f3"]
    assert qb_adds and qb_adds[0].drop.player.position == "QB"


def test_with_one_quarterback_a_backup_may_still_cost_a_running_back(tmp_path, monkeypatch):
    """The control: one QB rostered, so the free QB is a backup, and the
    cross-position pairing the old code made is still allowed."""
    _season(monkeypatch, {"f3": 90, "m4": 260})
    ranks = dict(RANKS, f3=5)
    b = _bundle(tmp_path, MINE, [PoolPlayer(_p("f3", "Free Qb", "QB"))], ranks)
    qb_adds = [a for a in b.adds if a.score.player.key == "f3"]
    assert qb_adds and qb_adds[0].drop.player.position != "QB"


# --- a starting kicker on bye ----------------------------------------------
class _KickerBye(ScheduleProvider):
    """BUF (my kicker's team) is off in week 9."""

    def for_week(self, week):
        teams = {"KC", "SF"} if week == 9 else {"KC", "SF", "BUF"}
        return {t: object() for t in teams}


def test_a_kicker_on_bye_is_streamed_for(tmp_path):
    mine = [p if p.key != "m9" else _p("m9", "My K", "K", team="BUF") for p in MINE]
    ranks = {k: v for k, v in RANKS.items() if k != "m9"}   # FP doesn't rank a bye
    ranks["f4"] = 3
    pool = [PoolPlayer(_p("f4", "Free K", "K"))]
    b = _bundle(tmp_path, mine, pool, ranks, schedule=_KickerBye())
    k_adds = [a for a in b.adds if a.score.player.key == "f4"]
    assert k_adds, "the bye-week kicker should be streamed for"
    assert k_adds[0].drop.player.key == "m9"
    assert k_adds[0].bye_stream and k_adds[0].margin is None
    assert any("on bye this week" in r for r in k_adds[0].reasons)
    render_waiver_digest(9, [b])   # a None margin must render


def test_a_second_kicker_who_plays_covers_the_bye(tmp_path):
    mine = ([p if p.key != "m9" else _p("m9", "My K", "K", team="BUF") for p in MINE]
            + [_p("m14", "My K2", "K", team="KC")])
    ranks = {k: v for k, v in RANKS.items() if k != "m9"}
    ranks.update(f4=3, m14=12)
    b = _bundle(tmp_path, mine, [PoolPlayer(_p("f4", "Free K", "K"))], ranks,
                schedule=_KickerBye())
    assert not any(a.bye_stream for a in b.adds)


# --- nobody droppable ------------------------------------------------------
def test_nobody_droppable_is_not_reported_as_a_lost_comparison():
    b = WaiverBundle(label="x", scoring="half", week=9, coverage={"ecr": 122},
                     pool_size=150, drop_pool=0)
    reason = b.no_adds_reason()
    assert reason.startswith("Nobody on your roster could be dropped")
    assert "beats anyone" not in reason


def test_the_build_records_how_many_drops_were_available(tmp_path):
    b = _bundle(tmp_path, MINE, [PoolPlayer(_p("f1", "Free Wr", "WR", team="SF"))], RANKS)
    assert b.drop_pool and b.drop_pool >= len(b.drops)
