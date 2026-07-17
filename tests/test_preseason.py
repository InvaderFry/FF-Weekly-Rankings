"""Preseason detection, the sample-data fill, and the warning banner."""

from datetime import date

from ff_startsit import season
from ff_startsit.config import Settings
from ff_startsit.models import Player
from ff_startsit.output.discord import build_discord_payload
from ff_startsit.output.html import build_dashboard_html
from ff_startsit.pipeline import build_signals, recommend
from ff_startsit.report import build_lineup, render_digest, scored
from ff_startsit.sources.sample import SampleSignal, build_sample_signals

ROSTER = [
    Player(key="101", name="Alpha QB", team="KC", position="QB"),
    Player(key="102", name="Bravo QB", team="BUF", position="QB"),
    Player(key="201", name="Charlie RB", team="SF", position="RB"),
    Player(key="202", name="Delta RB", team="DET", position="RB"),
    Player(key="203", name="Echo RB", team="CHI", position="RB"),
    Player(key="301", name="Foxtrot WR", team="MIA", position="WR"),
    Player(key="302", name="Golf WR", team="CIN", position="WR"),
    Player(key="303", name="Hotel WR", team="DAL", position="WR"),
    Player(key="401", name="India TE", team="KC", position="TE"),
    Player(key="501", name="Juliet K", team="BAL", position="K"),
    Player(key="601", name="Kilo DEF", team="NYJ", position="DEF"),
]


# --- date math -------------------------------------------------------------
def test_is_preseason_summer():
    assert season.is_preseason(date(2026, 7, 17)) is True


def test_is_preseason_false_in_season_and_playoffs():
    assert season.is_preseason(date(2026, 10, 15)) is False
    # January belongs to the *prior* season (playoffs), not next preseason.
    assert season.is_preseason(date(2027, 1, 10)) is False


def test_is_preseason_kickoff_boundary():
    # 2026: Sept 1 is a Tuesday, so the first Thursday is Sept 3.
    kickoff = season.first_kickoff(2026)
    assert kickoff == date(2026, 9, 3)
    assert season.is_preseason(kickoff - date.resolution) is True
    assert season.is_preseason(kickoff) is False


def test_date_week_matches_old_fallback():
    assert season.date_week(date(2026, 7, 17)) == 1
    assert season.date_week(date(2026, 9, 3)) == 1
    assert season.date_week(date(2026, 9, 10)) == 2
    assert season.date_week(date(2027, 2, 1)) == 18  # clamped


# --- sample signals --------------------------------------------------------
def test_sample_signals_cover_every_player_and_are_deterministic():
    signals = build_sample_signals()
    assert {s.name for s in signals} == {"ecr", "vegas"}
    for sig in signals:
        first = sig.fetch(1, ROSTER)
        second = sig.fetch(1, list(reversed(ROSTER)))
        assert set(first) == {p.key for p in ROSTER}
        assert first == second  # stable regardless of input order
        # No notes: blend turns notes into flags/alerts, which would mark
        # every row — the preseason banner labels the run instead.
        assert all(v.raw is not None and v.available and not v.note
                   for v in first.values())


def test_sample_signal_extends_past_configured_values():
    sig = SampleSignal("ecr", higher_is_better=False, by_position={"RB": [1.0, 5.0]})
    values = sig.fetch(1, [Player(key=str(i), name=f"P{i}", team="KC", position="RB")
                           for i in range(4)])
    raws = [values[str(i)].raw for i in range(4)]
    assert raws == [1.0, 5.0, 9.0, 13.0]  # keeps stepping by the last gap


def test_build_signals_swaps_in_samples_only_when_preseason_and_enabled(tmp_path):
    settings = Settings(data_dir=tmp_path)
    assert all(s.is_sample for s in build_signals(settings, preseason=True))
    assert not any(s.is_sample for s in build_signals(settings, preseason=False))
    settings_off = Settings(data_dir=tmp_path, preseason_fill=False)
    assert not any(s.is_sample for s in build_signals(settings_off, preseason=True))


def test_sample_run_fills_lineup_and_never_logs(tmp_path):
    settings = Settings(data_dir=tmp_path)
    signals = build_signals(settings, preseason=True)
    recs = {pos: recommend(settings, [p for p in ROSTER if p.position == pos],
                           week=1, signals=signals, command="report", log=True)
            for pos in {p.position for p in ROSTER}}
    lineup = build_lineup(scored(recs))
    assert all(pick is not None for _slot, pick in lineup)
    # log=True was requested, but sample runs must never feed calibration (#7).
    assert not settings.results_log_path.exists()


# --- banner threading ------------------------------------------------------
def test_preseason_banner_variants():
    july = date(2026, 7, 17)
    assert season.preseason_banner(Settings(), today=july) == season.SAMPLE_BANNER
    assert (season.preseason_banner(Settings(preseason_fill=False), today=july)
            == season.NODATA_BANNER)
    assert season.preseason_banner(Settings(), today=date(2026, 10, 1)) is None


def test_render_digest_banner():
    digest = render_digest(1, "ppr", {}, banner=season.SAMPLE_BANNER)
    assert "PRESEASON" in digest
    assert "SAMPLE data" in digest
    assert "PRESEASON" not in render_digest(1, "ppr", {})


def test_dashboard_banner():
    html = build_dashboard_html(1, "ppr", [], {}, generated_on="2026-07-17",
                                banner=season.SAMPLE_BANNER)
    assert "PRESEASON" in html
    assert "callout" in html
    assert "PRESEASON" not in build_dashboard_html(1, "ppr", [], {},
                                                   generated_on="2026-07-17")


def test_discord_payload_banner_and_commands():
    payload = build_discord_payload(1, "ppr", [], {},
                                    banner=season.SAMPLE_BANNER,
                                    commands_url="https://github.com/o/r/issues")
    embed = payload["embeds"][0]
    assert "PRESEASON" in embed["description"]
    assert embed["color"] == 0xD29922  # amber, not the all-clear green
    commands = [f for f in embed["fields"] if f["name"] == "💬 Commands"]
    assert len(commands) == 1
    assert "https://github.com/o/r/issues" in commands[0]["value"]
    assert "not here in Discord" in commands[0]["value"]


def test_discord_payload_defaults_unchanged():
    embed = build_discord_payload(1, "ppr", [], {})["embeds"][0]
    assert "PRESEASON" not in embed["description"]
    assert embed["color"] == 0x2EA043
    # Without a repo URL the pointer still ships, as a footer.
    assert "not here in Discord" in embed["footer"]["text"]
