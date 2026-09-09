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
