"""The writers can veto an add (option B from the Week 9 review), never make one.

Skill players: RB/WR need a trade chart or a waiver-column mention; QB/TE need
a trade chart. Kickers and defenses: this week's CBS consensus and Boone's
weekly list, judged against the player they'd replace. Every rule falls back to
the blend when there was nothing to consult, and a veto is listed, not hidden.
"""

from ff_startsit.boards.models import ALL_POSITIONS, BoardEntry, ExpertBoard
from ff_startsit.models import SignalValue
from ff_startsit.output.html import build_waivers_html
from ff_startsit.sources.base import Signal
from ff_startsit.waivers.endorse import Endorsements
from ff_startsit.waivers.models import DropCandidate, FantasyTeam, PoolPlayer
from ff_startsit.waivers.render import render_waiver_digest
from ff_startsit.models import PlayerScore

from .test_waiver_build import (MINE, RANKS, THEIRS, _NoSchedule, _p, _Provider,
                                _WriterSource)
# The autouse fixture applies only once imported into this module.
from .test_waiver_build import _no_journalists  # noqa: F401
from ff_startsit.config import Settings
from ff_startsit.waivers.build import build_bundle


def _score(key, name, pos):
    return PlayerScore(player=_p(key, name, pos), final=50.0, normalized={}, raw={})


def _board(sid, short, entries=(), unavailable=None):
    return ExpertBoard(sid, short, short, entries={k: BoardEntry(pos_rank=r) for k, r in entries},
                       unavailable=dict(unavailable or {}))


RICHARD = lambda *e: _board("richard_chart", "Richard", e)          # noqa: E731
BOONE_COL = lambda *e: _board("boone_waivers", "Boone", e)          # noqa: E731


# --- skill players ----------------------------------------------------------
def test_a_quarterback_needs_a_trade_chart():
    willis = _score("w", "Malik Willis", "QB")
    e = Endorsements(boards=[RICHARD(("other", 5)), BOONE_COL(("w", 1))])
    assert e.skill_veto(willis) == "not on Richard's trade chart"   # a column isn't enough
    assert Endorsements(boards=[RICHARD(("w", 20))]).skill_veto(willis) is None


def test_a_back_needs_a_chart_or_a_column():
    rb = _score("r", "Some Rb", "RB")
    assert Endorsements(boards=[RICHARD(), BOONE_COL(("r", 3))]).skill_veto(rb) is None
    assert Endorsements(boards=[RICHARD(("r", 30))]).skill_veto(rb) is None
    assert Endorsements(boards=[RICHARD(), BOONE_COL()], mentioned=frozenset({"r"})).skill_veto(rb) is None
    assert "not on a trade chart or in a waiver column" in Endorsements(
        boards=[RICHARD(), BOONE_COL()]).skill_veto(rb)


def test_nothing_read_is_the_blends_call_not_a_veto():
    qb, rb = _score("q", "Qb", "QB"), _score("r", "Rb", "RB")
    unread = _board("richard_chart", "Richard", unavailable={ALL_POSITIONS: "not posted yet"})
    e = Endorsements(boards=[unread])
    assert e.skill_veto(qb) is None and e.skill_veto(rb) is None
    assert "blend's call" in e.fallback_note(qb)


# --- kickers and defenses ---------------------------------------------------
def test_a_kicker_swap_the_experts_disagree_with_is_held_back():
    add, mine = _score("fa", "Free K", "K"), _score("me", "My K", "K")
    cbs = _board("cbs_consensus", "CBS", [("fa", 10), ("me", 3)])
    boone = _board("boone_weekly", "Boone", [("fa", 12), ("me", 4)])
    why = Endorsements(weekly=[cbs, boone]).stream_veto(add, DropCandidate(mine))
    assert why == "CBS and Boone rank My K higher this week"


def test_a_split_goes_to_the_blend():
    add, mine = _score("fa", "Free K", "K"), _score("me", "My K", "K")
    cbs = _board("cbs_consensus", "CBS", [("fa", 10), ("me", 3)])
    boone = _board("boone_weekly", "Boone", [("fa", 2), ("me", 4)])
    assert Endorsements(weekly=[cbs, boone]).stream_veto(add, DropCandidate(mine)) is None


def test_an_expert_who_lists_only_one_prefers_him():
    add, mine = _score("fa", "Free K", "K"), _score("me", "My K", "K")
    cbs = _board("cbs_consensus", "CBS", [("me", 8)])          # the free agent is off his list
    assert Endorsements(weekly=[cbs]).stream_veto(add, DropCandidate(mine))


def test_a_bye_week_stream_is_never_vetoed():
    add, mine = _score("fa", "Free K", "K"), _score("me", "My K", "K")
    cbs = _board("cbs_consensus", "CBS", [("me", 1)])
    assert Endorsements(weekly=[cbs]).stream_veto(add, DropCandidate(mine, bye_stream=True)) is None


def test_no_weekly_ranks_falls_back_with_a_note():
    add = _score("fa", "Free K", "K")
    e = Endorsements(weekly=[_board("cbs_consensus", "CBS", unavailable={"K": "page down"})])
    assert e.stream_veto(add, DropCandidate(_score("me", "My K", "K"))) is None
    assert "no CBS or Boone ranking" in e.fallback_note(add)


# --- through build_bundle ---------------------------------------------------
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


def _season(monkeypatch, extra):
    base = {"m4": 65, "t3": 68, "t4": 66, "m7": 240, "f1": 120}
    base.update(extra)
    monkeypatch.setattr("ff_startsit.waivers.build.SeasonValueProvider.fetch",
                        lambda self, players, scoring: {p.key: base.get(p.key, 150)
                                                        for p in players})


def _bundle(tmp_path, pool, ranks, **kw):
    teams = [FantasyTeam("1", "My Squad", tuple(MINE), is_mine=True, faab_spent=20.0),
             FantasyTeam("2", "Rival FC", tuple(THEIRS))]
    return build_bundle(Settings(data_dir=tmp_path), "work",
                        _Provider(pool=pool, teams=teams), MINE, 9,
                        schedule=_NoSchedule(), signals=[_ECR(ranks)],
                        include_columns=False, preseason=False, rehearse=False,
                        include_trades=False, **kw)


def test_a_quarterback_on_no_chart_is_held_back_and_listed(tmp_path, monkeypatch):
    """The blend would add the free QB over a back (one QB rostered, so PR 1's
    rule allows it); Richard's chart was read and doesn't list him."""
    _season(monkeypatch, {"f3": 90, "m4": 260})
    pool = [PoolPlayer(_p("f3", "Free Qb", "QB"))]
    ranks = dict(RANKS, f3=5)
    plain = _bundle(tmp_path, pool, ranks)
    assert "f3" in {t.score.player.key for t in plain.adds}           # the blend's pick
    vetoed = _bundle(tmp_path, pool, ranks,
                     chart_sources=[_WriterSource("richard_chart", "Richard", {"m1": BoardEntry(value=9)})])
    assert "f3" not in {t.score.player.key for t in vetoed.adds + vetoed.alternates}
    assert vetoed.held_back == ["Free Qb (QB): not on Richard's trade chart"]
    md = render_waiver_digest(9, [vetoed])
    assert "Held back: the experts don't back these" in md and "Free Qb (QB)" in md


def test_the_veto_only_removes(tmp_path, monkeypatch):
    """Against the plain report, the vetoed one has the same adds minus the
    vetoed player, each with the same drop and bid — nothing added or moved."""
    _season(monkeypatch, {"f3": 90, "m4": 260})
    pool = [PoolPlayer(_p("f3", "Free Qb", "QB")),
            PoolPlayer(_p("f1", "Free Wr", "WR", team="SF"))]
    ranks = dict(RANKS, f3=5)
    plain = _bundle(tmp_path, pool, ranks)
    vetoed = _bundle(tmp_path, pool, ranks, chart_sources=[
        _WriterSource("richard_chart", "Richard", {"f1": BoardEntry(value=5)})])
    kept = {t.score.player.key for t in vetoed.adds}
    assert kept <= {t.score.player.key for t in plain.adds + plain.alternates}
    assert "f3" not in kept


def test_the_kicker_table_and_veto(tmp_path):
    """The blend prefers the free kicker (FP K3 vs my K6); CBS ranks mine higher."""
    pool = [PoolPlayer(_p("f4", "Free K", "K"))]
    ranks = dict(RANKS, f4=3)
    plain = _bundle(tmp_path, pool, ranks)
    assert "f4" in {t.score.player.key for t in plain.adds}

    def weekly(players, week, scoring):
        return [_board("cbs_consensus", "CBS", [("f4", 14), ("m9", 2)]),
                _board("boone_weekly", "Boone", unavailable={ALL_POSITIONS: "not posted yet"})]
    b = _bundle(tmp_path, pool, ranks, weekly_boards=weekly)
    assert "f4" not in {t.score.player.key for t in b.adds}
    assert b.held_back == ["Free K (K): CBS ranks My K higher this week"]
    assert b.stream_columns == ["CBS", "Boone"]
    rows = {r.player.key: r for r in b.stream_rows["K"]}
    assert rows["m9"].mine and rows["m9"].status == "yours: keep"
    assert rows["f4"].ranks == {"CBS": 14, "Boone": None}
    assert rows["f4"].status.startswith("held back: CBS ranks")
    md = render_waiver_digest(9, [b])
    assert "Kicker and defense: this week's experts" in md
    assert "| **My K** (yours) | KC | " in md and "K14" in md
    html = build_waivers_html(9, [b], "2026-11-04")
    assert "Kicker and defense: this week&#x27;s experts" in html or \
        "Kicker and defense: this week's experts" in html


def test_without_writers_nothing_changes(tmp_path):
    pool = [PoolPlayer(_p("f4", "Free K", "K"))]
    b = _bundle(tmp_path, pool, dict(RANKS, f4=3))
    assert b.held_back == [] and b.stream_rows == {}
    assert all("blend's call" not in r for t in b.adds for r in t.reasons)


# --- the Tuesday retry gate -------------------------------------------------
def _kicker_weekly(players, week, scoring):
    return [_board("cbs_consensus", "CBS", [("f4", 14), ("m9", 2)]),
            _board("boone_weekly", "Boone", unavailable={ALL_POSITIONS: "not posted yet"})]


def test_the_marker_records_which_kdef_lists_were_read(tmp_path):
    from ff_startsit.waivers.board import bundles_read
    pool = [PoolPlayer(_p("f4", "Free K", "K"))]
    b = _bundle(tmp_path, pool, dict(RANKS, f4=3), weekly_boards=_kicker_weekly)
    read = bundles_read([b])
    assert "cbs_consensus:K" in read
    assert not any(r.startswith("boone_weekly") for r in read)   # not posted yet
    assert "cbs_consensus:DEF" not in read                       # ranked none of ours


def test_a_retry_that_finds_the_kdef_lists_republishes(tmp_path):
    """The lists post Tuesday evening: after the 20:30 UTC pass, before the
    01:30 UTC retry. The retry must count them as news, or the published picks
    never see them."""
    from ff_startsit.waivers.board import report_has_news
    pool = [PoolPlayer(_p("f4", "Free K", "K"))]
    ranks = dict(RANKS, f4=3)

    def nothing_yet(players, week, scoring):
        return [_board("cbs_consensus", "CBS", unavailable={ALL_POSITIONS: "page down"}),
                _board("boone_weekly", "Boone", unavailable={ALL_POSITIONS: "not posted yet"})]
    first = render_waiver_digest(9, [_bundle(tmp_path, pool, ranks, weekly_boards=nothing_yet)])
    retry = render_waiver_digest(9, [_bundle(tmp_path, pool, ranks, weekly_boards=_kicker_weekly)])
    assert report_has_news(retry, first)
    assert not report_has_news(retry, retry)          # the same lists again is not news
