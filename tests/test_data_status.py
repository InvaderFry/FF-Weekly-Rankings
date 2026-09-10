from ff_startsit.data_status import DataStatus, finish_status
from ff_startsit.models import Player, PlayerScore, Recommendation, SignalValue
from ff_startsit.report import LeagueBundle, Lineup, render_multi_digest
from ff_startsit.output.html import build_multi_dashboard_html, build_waivers_html
from ff_startsit.waivers.render import render_waiver_digest


def test_reports_show_missing_league_and_weather_with_escaped_labels():
    score = PlayerScore(Player("p", "Player", "LAR", "WR"),
                        raw={"weather": SignalValue(None, False, "venue unknown")})
    rec = Recommendation(1, "ppr", {"weather": 1}, [score])
    bundle = LeagueBundle("healthy", "ppr", {"WR": rec}, Lineup([]))
    status = DataStatus(2026, 1, ["healthy", "<missing>"], skipped={"<missing>": "roster unavailable"})
    finish_status(status, [bundle])
    md = render_multi_digest(1, [bundle])
    html = build_multi_dashboard_html(1, [bundle], "today")
    assert "INCOMPLETE" in md and "INCOMPLETE" in html
    assert "weather missing 1/1 player readings (venue unknown)" in md
    assert "weather missing 1/1 player readings (venue unknown)" in html
    assert "&lt;missing&gt;" in html and "<missing>" not in html
    assert "+00:00" in md and "provider update time" in md


def test_waiver_status_survives_both_renderers():
    from ff_startsit.waivers.models import WaiverBundle, LeagueRules
    bundle = WaiverBundle(label="healthy", scoring="ppr", week=1, rules=LeagueRules())
    status = DataStatus(2026, 1, ["healthy", "failed"], skipped={"failed": "cookies expired"})
    finish_status(status, [bundle])
    for output in [render_waiver_digest(1, [bundle]), build_waivers_html(1, [bundle], "today")]:
        assert "Skipped failed: cookies expired" in output
        assert "1 of 2 included" in output


def test_one_status_line_per_transport_not_one_per_position():
    """A three-league digest used to open with ~25 identical ECR lines."""
    from ff_startsit.sources.ecr import ECRSignal

    signal = ECRSignal()
    for position in ("QB", "RB", "WR", "TE", "K", "DST"):
        signal._record_source(position, 3, "public current-week page")
    signal._record_source("RB", 3, "public current-week page")  # cache miss refetch

    assert len(signal.source_status) == 1
    _, line = signal.source_status[0]
    assert line.startswith("ECR QB, RB, WR, TE, K, DST: public current-week page")
    assert "requested Week 3" in line and "fetched" in line


def test_a_wrong_week_scrape_still_says_so_once():
    from ff_startsit.sources.ecr import ECRSignal

    signal = ECRSignal()
    signal._record_source("RB", 5, "public current-week page")
    signal.served_wrong_week = True
    assert any("not historical rankings" in line for _, line in signal.source_status)


def test_the_digest_shows_one_ecr_line_not_one_per_position():
    """The signal aggregated correctly; the digest still printed every prefix.

    ``ECRSignal.source_status`` is read once per position as a run scores them
    and its position list grows each time, so ``finish_status`` deduplicating
    the rendered *strings* kept ``ECR DST``, ``ECR DST, QB``,
    ``ECR DST, QB, K`` ... as separate rows: a live three-league Week 1 digest
    opened with 18 of them above the tables that decide the week. The unit test
    above passes either way, because it reads the signal once at the end —
    which is exactly why this one goes through the digest instead.
    """
    from ff_startsit.pipeline import _merge_source_status
    from ff_startsit.sources.ecr import ECRSignal

    signal = ECRSignal()
    recs = {}
    for position in ("QB", "RB", "WR"):
        signal._record_source(position, 1, "public current-week page")
        score = PlayerScore(Player(f"p{position}", f"Player {position}", "LAR", position),
                            raw={"ecr": SignalValue(5.0, True)})
        rec = Recommendation(1, "ppr", {"ecr": 1}, [score])
        # What `pipeline.recommend` does at the end of every position's pass.
        _merge_source_status(rec, signal.source_status)
        recs[position] = rec

    bundle = LeagueBundle("league", "ppr", recs, Lineup([]))
    finish_status(DataStatus(2026, 1, ["league"]), [bundle])
    digest = render_multi_digest(1, [bundle])

    served = [line for line in digest.splitlines() if "public current-week page" in line]
    assert served == ["- league: ECR QB, RB, WR: public current-week page; "
                      "requested Week 1; fetched "
                      + signal._source_runs[("public current-week page", 1)][1]]


def test_analyst_status_deduplicates_per_league_across_positions():
    from ff_startsit.data_status import DataStatus, finish_status
    from ff_startsit.models import Recommendation
    from ff_startsit.report import LeagueBundle, Lineup, render_multi_digest
    bundles = []
    for label, scoring in [('Half', 'half'), ('Full', 'ppr')]:
        recs = {}
        for pos in ['QB', 'RB', 'WR', 'TE']:
            rec = Recommendation(1, scoring, {}, [])
            rec.source_status = [(('analyst', 'Justin Boone', label),
                                  f'{label}: Justin Boone Week 1 {scoring} rankings')]
            recs[pos] = rec
        bundles.append(LeagueBundle(label, scoring, recs, Lineup([])))
    finish_status(DataStatus(2026, 1, ['Half', 'Full']), bundles)
    md = render_multi_digest(1, bundles)
    assert md.count('Half: Justin Boone Week 1 half rankings') == 1
    assert md.count('Full: Justin Boone Week 1 ppr rankings') == 1
