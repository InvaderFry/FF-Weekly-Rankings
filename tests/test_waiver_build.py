"""build_bundle: one league in, one report out — including when data is missing.

These are the seams the scheduled run actually depends on: a league whose
free-agent list is unreachable still reports, a league whose team we can't
identify still gets adds, and the whole thing still writes no decision log.
"""

import pytest

from ff_startsit.config import Settings
from ff_startsit.models import Player, SignalValue
from ff_startsit.sources.base import Signal
from ff_startsit.sources.schedule import ScheduleProvider
from ff_startsit.waivers.base import LeagueViewProvider
from ff_startsit.waivers.build import build_bundle
from ff_startsit.waivers.models import (ACQ_FAAB, FantasyTeam, LeagueRules,
                                        PoolPlayer)

# Deliberately complementary rosters: I'm three deep at RB and thin at TE; the
# rival is the mirror. A league where nobody has surplus produces no trades —
# correct behaviour, but it exercises nothing.
RANKS = {"m1": 3, "m2": 5, "m3": 8, "m4": 20, "m5": 12, "m6": 30, "m7": 88,
         "m8": 70, "m9": 6, "m10": 4,
         "t1": 60, "t2": 45, "t3": 10, "t4": 8,
         "f1": 20, "f2": 75}
# team_count is what turns a rank into "would he start anywhere in this league",
# so it has to be a real league size. The two rosters below stand in for a normal
# twelve — spelling out ten more would exercise nothing the two already do.
RULES = LeagueRules(acquisition_type=ACQ_FAAB, faab_budget=100.0, team_count=12,
                    roster_slots={"QB": 1, "RB": 2, "WR": 2, "TE": 1,
                                  "K": 1, "DEF": 1})


def _p(key, name, pos, team="KC"):
    return Player(key=key, name=name, team=team, position=pos)


MINE = [_p("m1", "My Qb", "QB"), _p("m2", "My Rb1", "RB"), _p("m3", "My Rb2", "RB"),
        _p("m4", "My Rb3", "RB"),
        # Two of my three WRs are on the same NFL team, which is what makes the
        # bye-week check below have something to find.
        _p("m5", "My Wr1", "WR", team="SF"), _p("m6", "My Wr2", "WR", team="SF"),
        _p("m7", "My Wr3", "WR"), _p("m8", "My Te", "TE"), _p("m9", "My K", "K"),
        _p("m10", "Kansas City", "DEF")]
THEIRS = [_p("t1", "Their Rb", "RB"), _p("t2", "Their Wr", "WR"),
          _p("t3", "Their Te1", "TE"), _p("t4", "Their Te2", "TE")]
POOL = [PoolPlayer(_p("f1", "Free Wr", "WR", team="SF"), percent_owned=35.0),
        PoolPlayer(_p("f2", "Hurt Rb", "RB"), percent_owned=3.0, injury_status="IR")]


class _FakeECR(Signal):
    name = "ecr"
    higher_is_better = False

    def is_available(self):
        return True

    def fetch(self, week, players):
        return {p.key: SignalValue(raw=float(RANKS[p.key]) if p.key in RANKS else None,
                                   available=p.key in RANKS) for p in players}


class _Provider(LeagueViewProvider):
    def __init__(self, pool=None, teams=None, rules=None, boom=None):
        self._pool = POOL if pool is None else pool
        self._teams = teams
        self._rules = rules or RULES
        self._boom = boom or set()

    def get_league_teams(self):
        if "teams" in self._boom:
            raise RuntimeError("league unreadable")
        if self._teams is not None:
            return self._teams
        return [FantasyTeam("1", "My Squad", tuple(MINE), is_mine=True, faab_spent=20.0),
                FantasyTeam("2", "Rival FC", tuple(THEIRS))]

    def get_free_agents(self, week, limit=150):
        if "pool" in self._boom:
            raise RuntimeError("pool unreadable")
        return list(self._pool)

    def get_league_rules(self):
        if "rules" in self._boom:
            raise RuntimeError("settings unreadable")
        return self._rules


class _NoSchedule(ScheduleProvider):
    def for_week(self, week):
        return {}


class _Schedule(ScheduleProvider):
    """Everyone plays in week 9; SF is on bye in week 10."""

    def for_week(self, week):
        teams = {"KC", "SF"} if week == 9 else {"KC"}
        return {t: object() for t in teams}


@pytest.fixture(autouse=True)
def _no_journalists(monkeypatch):
    monkeypatch.setattr("ff_startsit.waivers.build.journalist_ranks",
                        lambda settings, players, week: {})


def _build(tmp_path, provider=None, schedule=None, **kw):
    kw.setdefault("include_columns", False)
    # In-season by default: these cases are about the scoring pass, and
    # build_bundle refuses outright before Week 1 (see the preseason tests).
    kw.setdefault("preseason", False)
    kw.setdefault("rehearse", False)
    kw.setdefault("signals", [_FakeECR()])
    return build_bundle(Settings(data_dir=tmp_path), "work",
                        provider or _Provider(), MINE, 9,
                        schedule=schedule or _NoSchedule(), **kw)


def test_a_full_league_produces_adds_drops_and_trades(tmp_path):
    b = _build(tmp_path)
    assert [a.score.player.key for a in b.adds] == ["f1"]
    assert b.adds[0].drop.player.key == "m7"     # the worst droppable WR
    assert "$" in b.adds[0].bid                  # FAAB league -> a dollar figure
    assert b.drops and b.trades


def test_the_bid_is_a_share_of_what_is_left_not_the_whole_budget(tmp_path):
    """faab_spent=20 of a 100 budget leaves 80; a bid quoted against 100 would
    be one you can't actually make."""
    assert "$80 left" in _build(tmp_path).adds[0].bid


def test_no_free_agent_list_says_so_instead_of_reporting_nothing(tmp_path):
    b = _build(tmp_path, provider=_Provider(boom={"pool"}))
    assert b.adds == []
    assert "No free-agent list" in b.caveat
    assert b.trades, "the trade half doesn't need the pool and must survive"


def test_unreadable_league_settings_leave_the_rest_standing(tmp_path, capsys):
    b = _build(tmp_path, provider=_Provider(boom={"rules"}))
    assert b.adds and b.adds[0].bid == ""   # unknown rules -> no bid, not a guess
    assert "league rules unavailable" in capsys.readouterr().err


def test_unreadable_teams_cost_the_trades_and_nothing_else(tmp_path, capsys):
    b = _build(tmp_path, provider=_Provider(boom={"teams"}))
    assert b.trades == []
    assert b.adds
    assert "league teams unavailable" in capsys.readouterr().err


def test_an_unidentifiable_team_explains_why_there_are_no_trades(tmp_path):
    teams = [FantasyTeam("1", "Someone", tuple(MINE)),
             FantasyTeam("2", "Rival FC", tuple(THEIRS))]
    b = _build(tmp_path, provider=_Provider(teams=teams))
    assert b.trades == []
    assert any("which team is yours" in n for n in b.notes)


def test_bye_holes_are_found_from_the_schedule(tmp_path):
    """Two of my three WRs are on SF, which is off in week 10 — leaving one
    healthy WR for two slots."""
    b = _build(tmp_path, schedule=_Schedule())
    gaps = {(g.week, g.position): g for g in b.byes}
    assert (10, "WR") in gaps
    assert gaps[(10, "WR")].available == 1 and gaps[(10, "WR")].needed == 2
    assert (9, "WR") not in gaps   # everyone plays this week


def test_an_unreachable_schedule_reports_no_bye_gaps(tmp_path):
    """Empty means "we don't know", never "all 32 teams are off"."""
    assert _build(tmp_path).byes == []


def test_hurt_free_agents_land_in_the_stash_section(tmp_path):
    b = _build(tmp_path)
    assert [s.score.player.key for s in b.stashes] == ["f2"]


def test_trades_can_be_switched_off(tmp_path):
    assert _build(tmp_path, include_trades=False).trades == []


def test_building_a_bundle_writes_no_decision_log(tmp_path):
    """#7 invariant, at the level that actually touches the pipeline."""
    settings = Settings(data_dir=tmp_path)
    build_bundle(settings, "work", _Provider(), MINE, 9, signals=[_FakeECR()],
                 schedule=_NoSchedule(), include_columns=False, preseason=False,
                 rehearse=False)
    assert not settings.results_log_path.exists()


def test_the_margin_caveat_is_always_stated(tmp_path):
    """Margins are within-position, and the report sorts adds across positions —
    said once rather than pretended away."""
    assert any("normalized within each position" in n for n in _build(tmp_path).notes)


def test_column_mentions_are_attached_to_their_add(tmp_path):
    class _Fetcher:
        read = [("Dave Richard", "https://cbs.test/x")]

        def fetch(self, week, players):
            from ff_startsit.waivers.models import ColumnMention
            return [ColumnMention("Dave Richard", "https://cbs.test/x", "f1",
                                  "He is the top add.")]

    b = _build(tmp_path, include_columns=True, column_fetcher=_Fetcher())
    assert b.adds[0].mentions[0].author == "Dave Richard"
    assert b.sources == [("Dave Richard", "https://cbs.test/x")]


# --- preseason -------------------------------------------------------------
class _ExplodingProvider(_Provider):
    """Any provider call before Week 1 is a request that should never happen."""

    def get_league_teams(self):
        raise AssertionError("the preseason refusal must cost no requests")

    def get_free_agents(self, week, limit=150):
        raise AssertionError("the preseason refusal must cost no requests")

    def get_league_rules(self):
        raise AssertionError("the preseason refusal must cost no requests")


def test_preseason_refuses_instead_of_suggesting_sample_moves(tmp_path):
    """Before Week 1 ``build_signals`` serves bundled sample values. Dealt to a
    real roster they name real players to add and real players to cut, so the
    waiver pass refuses rather than dressing them in a banner."""
    b = _build(tmp_path, provider=_ExplodingProvider(), preseason=True)
    assert b.adds == [] and b.drops == [] and b.trades == []
    assert b.stashes == [] and b.byes == []
    assert b.banner and "PRESEASON" in b.banner
    # MARGIN_NOTE explains scores this bundle doesn't carry.
    assert b.notes == []


def test_in_season_is_unaffected_by_the_preseason_guard(tmp_path):
    b = _build(tmp_path, preseason=False)
    assert b.banner is None and b.adds


def test_a_rehearsal_scores_live_data_instead_of_refusing(tmp_path):
    """The dress rehearsal is preseason by the calendar but real underneath —
    it proves the pipeline rather than demonstrating it."""
    b = _build(tmp_path, preseason=True, rehearse=True)
    assert b.adds and b.drops                       # the provider was read
    assert b.banner and "DRESS REHEARSAL" in b.banner


def test_a_rehearsal_reports_what_the_live_signals_reached(tmp_path):
    """Without the counts an empty rehearsal reads exactly like a broken one."""
    b = _build(tmp_path, preseason=True, rehearse=True)
    assert "Live coverage:" in b.banner
    # _FakeECR covers every player in RANKS; the pool is f1/f2, both ranked.
    assert f"ecr 2/{len(POOL)}" in b.banner


def test_the_rehearsal_never_reaches_the_sample_fill(monkeypatch, tmp_path):
    """The whole point: build_signals must be asked for live signals, since the
    sample fill is exactly what the rehearsal exists to avoid."""
    asked = {}

    def _spy(settings, **kw):
        asked.update(kw)
        return [_FakeECR()]

    monkeypatch.setattr("ff_startsit.waivers.build.build_signals", _spy)
    build_bundle(Settings(data_dir=tmp_path), "work", _Provider(), MINE, 1,
                 schedule=_NoSchedule(), include_columns=False,
                 preseason=True, rehearse=True)
    assert asked["preseason"] is False


def test_the_window_is_detected_when_the_flag_is_not_passed(monkeypatch, tmp_path):
    """The scheduled cron carries no --rehearse; build_bundle finds the
    pre-kickoff window itself."""
    monkeypatch.setattr("ff_startsit.waivers.build.is_rehearsal_window",
                        lambda today=None: True)
    b = _build(tmp_path, preseason=True, rehearse=None)
    assert b.adds and "DRESS REHEARSAL" in b.banner


def test_rehearsing_in_season_is_a_no_op_not_a_banner(tmp_path):
    b = _build(tmp_path, preseason=False, rehearse=True)
    assert b.banner is None and b.adds


def test_outside_the_window_preseason_still_refuses(tmp_path):
    b = _build(tmp_path, provider=_ExplodingProvider(),
               preseason=True, rehearse=False)
    assert b.adds == [] and "PRESEASON" in b.banner


# --- the drafted roster under the refusal ----------------------------------
def test_the_refusal_still_shows_the_team_you_drafted(tmp_path):
    """A refusal that says only "not yet" is a message with nothing in it. The
    roster was already fetched before build_bundle ran, so showing it is free."""
    b = _build(tmp_path, provider=_ExplodingProvider(),
               preseason=True, rehearse=False)
    assert [p.name for p in b.roster] == [p.name for p in MINE]
    assert b.adds == [] and "PRESEASON" in b.banner   # still a refusal


def test_before_the_draft_there_is_no_team_to_show(tmp_path):
    b = build_bundle(Settings(data_dir=tmp_path), "work", _ExplodingProvider(),
                     [], 1, include_columns=False, preseason=True, rehearse=False)
    assert b.roster == [] and b.roster_by_position() == []


def test_the_roster_rides_only_on_the_refusal(tmp_path):
    """In season and in a rehearsal the report has real adds and drops to show,
    so the roster listing would be noise."""
    assert _build(tmp_path, preseason=False).roster == []
    assert _build(tmp_path, preseason=True, rehearse=True).roster == []


def test_roster_groups_are_ordered_and_lose_nobody(tmp_path):
    b = _build(tmp_path, provider=_ExplodingProvider(),
               preseason=True, rehearse=False)
    groups = b.roster_by_position()
    assert [pos for pos, _ in groups] == ["QB", "RB", "WR", "TE", "K", "DEF"]
    assert sum(len(players) for _, players in groups) == len(MINE)
    # Alphabetical inside a position, so the same roster reads the same twice.
    rbs = dict(groups)["RB"]
    assert [p.name for p in rbs] == sorted(p.name for p in rbs)


def test_an_unexpected_position_is_listed_not_dropped(tmp_path):
    """A missing player reads as a draft that didn't come through."""
    odd = list(MINE) + [_p("x1", "Odd Slot", "P")]
    b = build_bundle(Settings(data_dir=tmp_path), "work", _ExplodingProvider(),
                     odd, 1, include_columns=False, preseason=True, rehearse=False)
    assert dict(b.roster_by_position())["P"][0].name == "Odd Slot"


def test_coverage_is_recorded_in_season_not_only_when_rehearsing(tmp_path):
    """The rehearsal banner needed these counts; so does every in-season run.

    Without them an ECR outage empties the adds list and the report answers
    "nothing worth adding" — an outage rendered as advice.
    """
    b = _build(tmp_path)
    assert b.pool_size == len(POOL)
    assert b.coverage.get("ecr") == len([p for p in POOL if p.player.key in RANKS])


def test_an_unreachable_pool_does_not_also_claim_the_wire_was_quiet(tmp_path):
    """The caveat and the no-adds reason went into one Discord description.

    A failed pool fetch leaves ``pool_size`` at zero, which fell past both outage
    guards and landed on "Nothing on the wire beats anyone you could drop this
    week" — printed directly under "No free-agent list was available for this
    league". A caveat already says why the section is empty; a second sentence
    asserting a comparison that never ran contradicts it.
    """
    b = _build(tmp_path, provider=_Provider(boom={"pool"}))
    assert b.adds == []
    assert b.pool_size == 0
    assert "No free-agent list was available" in b.caveat
    assert b.no_adds_reason() is None


def test_an_ecr_outage_is_reported_as_one_rather_than_as_a_quiet_wire(tmp_path):
    class _DeadECR(_FakeECR):
        def fetch(self, week, players):
            raise RuntimeError("FantasyPros unreachable")

    b = _build(tmp_path, signals=[_DeadECR()])
    assert b.adds == []
    assert b.coverage.get("ecr", 0) == 0
    assert "data outage" in b.no_adds_reason()
