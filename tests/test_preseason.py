"""Preseason detection, the sample-data fill, and the warning banner."""

from datetime import date, timedelta

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
    # 2026 opens on a *Wednesday*, Sept 9 — not the first Thursday (Sept 3), which
    # is why the known-kickoff table exists at all.
    kickoff = season.first_kickoff(2026)
    assert kickoff == date(2026, 9, 9)
    assert season.is_preseason(kickoff - date.resolution) is True
    assert season.is_preseason(kickoff) is False
    # The six days the first-Thursday guess used to hand to the regular season.
    assert season.is_preseason(date(2026, 9, 3)) is True
    assert season.is_preseason(date(2026, 9, 8)) is True


def test_the_thursday_guess_still_covers_years_the_table_doesnt_name():
    assert 2024 not in season.KNOWN_KICKOFFS
    assert season.first_kickoff(2024) == date(2024, 9, 5)
    assert season.first_kickoff(2025) == date(2025, 9, 4)   # table and guess agree


def test_date_week_counts_from_the_real_kickoff():
    assert season.date_week(date(2026, 7, 17)) == 1
    assert season.date_week(date(2026, 9, 3)) == 1          # still preseason
    assert season.date_week(date(2026, 9, 9)) == 1          # kickoff
    assert season.date_week(date(2026, 9, 13)) == 1         # Week 1 Sunday
    assert season.date_week(date(2026, 9, 16)) == 2
    assert season.date_week(date(2027, 2, 1)) == 18  # clamped


def test_env_override_beats_the_table_for_its_own_year(monkeypatch):
    monkeypatch.setenv(season.KICKOFF_ENV, "2026-09-10")
    assert season.first_kickoff(2026) == date(2026, 9, 10)
    # It names 2026, so it says nothing about any other season.
    assert season.first_kickoff(2025) == date(2025, 9, 4)


def test_a_malformed_env_override_warns_and_falls_back(monkeypatch, capsys):
    monkeypatch.setenv(season.KICKOFF_ENV, "week one")
    assert season.first_kickoff(2026) == date(2026, 9, 9)
    assert "FF_SEASON_KICKOFF" in capsys.readouterr().err


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


def test_waiver_banner_ignores_the_sample_fill_switch():
    """The sample fill exists so a preseason start/sit *table* has something to
    show. The waiver pass refuses either way, so its banner isn't conditional."""
    august = date(2026, 8, 20)      # preseason week 3 by Sleeper's reckoning
    assert season.waiver_banner(today=august) == season.WAIVER_BANNER
    # 2026 kicks off Wednesday Sept 9, so Week 1 is live on it and Sept 8 is the
    # last preseason day.
    assert season.waiver_banner(today=date(2026, 9, 9)) is None


# --- the dress-rehearsal window --------------------------------------------
def test_the_rehearsal_window_is_the_week_before_kickoff():
    """Not the first week of preseason: that is late July, when there are no
    weekly rankings to fetch, so a run then proves the least."""
    assert season.is_rehearsal_window(date(2026, 7, 17)) is False
    assert season.is_rehearsal_window(date(2026, 9, 1)) is False    # 8 days out
    assert season.is_rehearsal_window(date(2026, 9, 2)) is True     # 7 days out
    assert season.is_rehearsal_window(date(2026, 9, 8)) is True     # the day before
    assert season.is_rehearsal_window(date(2026, 9, 9)) is False    # kickoff itself
    assert season.is_rehearsal_window(date(2026, 10, 15)) is False


def test_exactly_one_weekly_cron_lands_in_the_window():
    """waivers.yml fires weekly. A 7-day window means one rehearsal a season —
    the property that keeps the schedule and the window from drifting apart."""
    kickoff = season.first_kickoff(2026)
    # One preseason's worth of days: from March (when season_year rolls over)
    # to kickoff, so the scan can't wander into the prior season's window.
    days = [d for n in range(1, 190)
            if (d := kickoff - timedelta(days=n)) >= date(2026, 3, 1)]
    for weekday in range(7):                      # whichever day the cron fires
        hits = [d for d in days
                if d.weekday() == weekday and season.is_rehearsal_window(d)]
        assert len(hits) == 1, f"weekday {weekday} matched {hits}"


def test_a_rehearsal_replaces_the_refusal_banner():
    assert (season.waiver_banner(today=date(2026, 9, 2))
            == season.REHEARSAL_BANNER)
    # Asked for outside the window, preseason still yields to the request...
    assert (season.waiver_banner(rehearse=True, today=date(2026, 7, 17))
            == season.REHEARSAL_BANNER)
    # ...but once the season is live there is nothing to rehearse.
    assert season.waiver_banner(rehearse=True, today=date(2026, 10, 15)) is None


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
