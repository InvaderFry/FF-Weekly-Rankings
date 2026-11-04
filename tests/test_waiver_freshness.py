"""Tuesday's FantasyPros consensus is thin, and the report says so.

Most rankers publish Wednesday or later (Week 9's page read 10 experts on
Tuesday evening), so the waiver pass records what the page said about itself,
the Tuesday retry republishes once the consensus fills in, and Wednesday's
rebuild posts to Discord only when the claims themselves changed.
"""

from ff_startsit.config import Settings
from ff_startsit.models import SignalValue
from ff_startsit.sources.base import Signal
from ff_startsit.waivers.board import adds_changed, bundles_read, report_has_news
from ff_startsit.waivers.build import build_bundle
from ff_startsit.waivers.models import FantasyTeam, PoolPlayer, WaiverBundle
from ff_startsit.waivers.render import render_waiver_digest
from ff_startsit.workflow import publish_adds_changed

from .test_waiver_build import MINE, RANKS, THEIRS, _NoSchedule, _p, _Provider
# The autouse fixture applies only once imported into this module.
from .test_waiver_build import _no_journalists  # noqa: F401


class _PagedECR(Signal):
    """ECR whose pages state a week and an expert count, as FantasyPros' do."""
    name = "ecr"
    higher_is_better = False

    def __init__(self, ranks, experts, page_week=9):
        self.ranks, self.experts, self.page_week = ranks, experts, page_week

    def is_available(self):
        return True

    def fetch(self, week, players):
        return {p.key: SignalValue(raw=float(self.ranks[p.key]) if p.key in self.ranks else None,
                                   available=p.key in self.ranks) for p in players}

    def min_experts(self, week):
        return self.experts

    def week_verified(self, week):
        return self.page_week == week


def _bundle(tmp_path, ecr, pool=None, label="work"):
    teams = [FantasyTeam("1", "My Squad", tuple(MINE), is_mine=True, faab_spent=20.0),
             FantasyTeam("2", "Rival FC", tuple(THEIRS))]
    pool = pool if pool is not None else [PoolPlayer(_p("f1", "Free Wr", "WR", team="SF"))]
    return build_bundle(Settings(data_dir=tmp_path), label, _Provider(pool=pool, teams=teams),
                        MINE, 9, schedule=_NoSchedule(), signals=[ecr],
                        include_columns=False, preseason=False, rehearse=False,
                        include_trades=False)


def test_a_thin_consensus_is_said_on_the_page(tmp_path):
    b = _bundle(tmp_path, _PagedECR(RANKS, experts=10))
    assert b.ecr_experts == 10 and not b.ecr_full and b.ecr_week_ok
    assert "has only 10 experts so far" in b.freshness
    assert "⏳" in render_waiver_digest(9, [b])
    assert "fantasypros_week" in bundles_read([b]) and "fantasypros_full" not in bundles_read([b])
    assert b.adds                          # a note, not a caveat: the report still stands


def test_a_full_consensus_says_nothing(tmp_path):
    b = _bundle(tmp_path, _PagedECR(RANKS, experts=60))
    assert b.ecr_full and b.freshness is None
    assert "fantasypros_full" in bundles_read([b])


def test_a_page_still_on_last_week_is_named(tmp_path):
    b = _bundle(tmp_path, _PagedECR(RANKS, experts=90, page_week=8))
    assert "isn't on Week 9 yet" in b.freshness
    assert "fantasypros_week" not in bundles_read([b])


def test_the_tuesday_retry_republishes_once_the_consensus_fills_in(tmp_path):
    first = render_waiver_digest(9, [_bundle(tmp_path, _PagedECR(RANKS, experts=10))])
    same = render_waiver_digest(9, [_bundle(tmp_path, _PagedECR(RANKS, experts=12))])
    fuller = render_waiver_digest(9, [_bundle(tmp_path, _PagedECR(RANKS, experts=45))])
    assert not report_has_news(same, first)        # still thin: not news
    assert report_has_news(fuller, first)


# --- Wednesday: Discord only if the adds changed ------------------------------
def test_the_adds_marker_tracks_the_claims(tmp_path):
    tuesday = render_waiver_digest(9, [_bundle(tmp_path, _PagedECR(RANKS, experts=10))])
    same_claims = render_waiver_digest(9, [_bundle(tmp_path, _PagedECR(RANKS, experts=60))])
    assert "<!-- ff-adds season=" in tuesday
    assert not adds_changed(same_claims, tuesday)
    # A better free agent on Wednesday changes the claim.
    ranks = dict(RANKS, f3=2)
    pool = [PoolPlayer(_p("f1", "Free Wr", "WR", team="SF")),
            PoolPlayer(_p("f3", "Hot Wr", "WR", team="SF"))]
    wednesday = render_waiver_digest(9, [_bundle(tmp_path, _PagedECR(ranks, experts=60), pool)])
    assert adds_changed(wednesday, tuesday)
    # Once the update is on the issue, a retry compares with it, not with Tuesday.
    assert not adds_changed(wednesday, tuesday + "\n" + wednesday)
    assert adds_changed(wednesday, "")             # nothing published yet


def test_the_workflow_helper_agrees_and_handles_a_failed_rebuild(tmp_path):
    tuesday = tmp_path / "prev.md"
    tuesday.write_text(render_waiver_digest(9, [_bundle(tmp_path, _PagedECR(RANKS, experts=10))]))
    out = tmp_path / "out.txt"
    assert publish_adds_changed(tmp_path / "missing.md", tuesday, output=str(out)) is False
    assert out.read_text().strip() == "changed=false"


# --- the Wednesday refresh retries on its own record -------------------------
def test_a_failed_refresh_is_retried_even_with_no_new_charts(tmp_path):
    from ff_startsit.workflow import refresh_needed, refreshed_marker
    report = tmp_path / "values.md"
    report.write_text("# Week 9 trade values\n\n<!-- ff-board season=2026 week=9 mode=live read=richard_chart -->\n")
    previous = tmp_path / "previous.md"
    out = str(tmp_path / "out.txt")
    assert refresh_needed(report, tmp_path / "none.md", output=out)      # nothing published
    previous.write_text(report.read_text())                               # posted, refresh failed
    assert refresh_needed(report, previous, output=out)
    previous.write_text(report.read_text() + refreshed_marker(report))    # posted after a refresh
    assert not refresh_needed(report, previous, output=out)
    # The workflow's form appends the marker to the report itself.
    refreshed_marker(report, append=True)
    assert not refresh_needed(report, report, output=out)
    # Last week's marker doesn't count for this week.
    previous.write_text("<!-- ff-waivers-refreshed season=2026 week=8 -->")
    assert refresh_needed(report, previous, output=out)


def test_generated_stamp_carries_the_time():
    from datetime import datetime, timezone
    from ff_startsit.season import generated_stamp
    assert generated_stamp(datetime(2026, 11, 4, 1, 15, tzinfo=timezone.utc)) == \
        "2026-11-03 7:15 PM CST (01:15 UTC)"
    assert generated_stamp(datetime(2026, 10, 6, 14, 5, tzinfo=timezone.utc)) == \
        "2026-10-06 9:05 AM CDT (14:05 UTC)"


def test_a_retry_that_finds_boones_missing_te_chart_republishes():
    """Boone's chart is on the writers' board and can veto an add, and his four
    charts post one at a time. Recorded as one id, the run missing TE and the
    retry that found it wrote the same marker and the retry was suppressed,
    though its claims could differ. Per position, like the values sheet."""
    from ff_startsit.boards.models import ExpertBoard
    from ff_startsit.waivers.board import board_marker

    def bundle(unavailable):
        b = WaiverBundle(label="work", scoring="ppr", week=9)
        b.expert_boards = [
            ExpertBoard("richard_chart", "Richard", "Richard", kind="ros"),
            ExpertBoard("boone_trade_values", "Boone", "Boone", kind="ros",
                        unavailable=dict(unavailable))]
        return b

    partial = bundles_read([bundle({"TE": "Boone's Week 9 TE trade chart not found"})])
    full = bundles_read([bundle({})])
    assert "boone_trade_values:TE" not in partial and "boone_trade_values:TE" in full
    assert "boone_trade_values" not in full                 # never the bare id
    assert "richard_chart" in full                          # one article, one id
    first = board_marker(2026, 9, partial)
    retry = board_marker(2026, 9, full)
    assert report_has_news(retry, first)
    assert not report_has_news(first, retry)                # fewer is not news
    assert not report_has_news(retry, retry)
