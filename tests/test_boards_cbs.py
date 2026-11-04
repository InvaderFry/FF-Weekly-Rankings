"""CBS staff consensus + Eisenberg + Richard, from trimmed live pages.

The fixtures are real CBS weekly ranking pages fetched 2026-11-02, trimmed to
the title, the analyst header and a few rows (including rows where one analyst
left the player unranked, shown as "--").
"""
from pathlib import Path

import pytest
import requests

from ff_startsit import report
from ff_startsit.boards.cbs import (CbsFetcher, CbsParseError, parse_page,
                                    week_mismatch)
from ff_startsit.config import Settings, load_settings
from ff_startsit.models import GameContext, Player, SignalValue
from ff_startsit.output import render
from ff_startsit.output.board import close_call_comparison
from ff_startsit.sources.base import Signal

FIX = Path(__file__).parent / "fixtures" / "boards"


def page(name):
    return (FIX / name).read_text(encoding="utf-8")


# --- parsing ---------------------------------------------------------------

def test_parses_names_teams_matchups_and_each_analysts_rank():
    rb = parse_page(page("cbs_ppr_RB_weekly.html"), "RB", "ppr")
    assert rb.analysts == ["Jamey Eisenberg", "Dave Richard", "Heath Cummings"]
    gibbs = rb.rows[0]
    assert (gibbs.name, gibbs.team, gibbs.opponent, gibbs.rank) == ("Jahmyr Gibbs", "DET", "MIN", 1)
    walker = rb.rows[1]
    assert walker.name == "Kenneth Walker III"     # suffix kept, hidden initial dropped
    assert walker.analysts == {"Jamey Eisenberg": 2, "Dave Richard": 3, "Heath Cummings": 2}


def test_an_unranked_cell_is_none_not_zero():
    rb = parse_page(page("cbs_ppr_RB_weekly.html"), "RB", "ppr")
    knight = next(r for r in rb.rows if r.name == "Zonovan Knight")
    assert knight.analysts == {"Jamey Eisenberg": None, "Dave Richard": 39, "Heath Cummings": None}


def test_defenses_are_keyed_by_the_team_in_their_logo():
    dst = parse_page(page("cbs_ppr_DST_weekly.html"), "DEF", "ppr")
    assert (dst.rows[0].name, dst.rows[0].team, dst.rows[0].opponent) == ("Jaguars", "JAX", "IND")


def test_the_analyst_less_half_ppr_page_is_refused():
    """CBS's half-ppr URL serves a different table with no analyst columns."""
    with pytest.raises(CbsParseError):
        parse_page(page("cbs_half_RB_no_analysts.html"), "RB", "ppr")


def test_a_header_that_no_longer_matches_the_columns_is_refused():
    """Drop one analyst's name: the columns can no longer be attributed."""
    body = page("cbs_ppr_RB_weekly.html").replace(
        'FantasyRankingsTable-expertName">Heath Cummings', 'FantasyRankingsTable-expertX">Heath Cummings', 1)
    with pytest.raises(CbsParseError, match="analyst cells"):
        parse_page(body, "RB", "ppr")


def test_a_page_for_another_position_is_refused():
    with pytest.raises(CbsParseError, match="WR rankings"):
        parse_page(page("cbs_ppr_RB_weekly.html"), "WR", "ppr")


# --- week check --------------------------------------------------------------

def schedule_from(pages):
    """The week the fixtures were published for, rebuilt from their own matchups."""
    games = {}
    for pg in pages:
        for row in pg.rows:
            if row.team and row.opponent:
                game = GameContext(home_team=row.team, away_team=row.opponent)
                games[row.team] = games[row.opponent] = game
    return games


def test_matchups_from_the_requested_week_pass():
    rb = parse_page(page("cbs_ppr_RB_weekly.html"), "RB", "ppr")
    assert week_mismatch(rb, schedule_from([rb]), 8) is None


def test_another_weeks_matchups_are_refused():
    rb = parse_page(page("cbs_ppr_RB_weekly.html"), "RB", "ppr")
    shuffled = {team: GameContext(home_team=team, away_team="ZZZ")
                for team in schedule_from([rb])}
    assert "another week's matchups" in week_mismatch(rb, shuffled, 9)


# --- the fetcher -------------------------------------------------------------

class Resp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class Session:
    """Serves fixtures by URL; any other page is a 404."""
    PAGES = {"ppr/RB": "cbs_ppr_RB_weekly.html", "ppr/QB": "cbs_ppr_QB_weekly.html",
             "ppr/DST": "cbs_ppr_DST_weekly.html"}

    def __init__(self):
        self.urls = []

    def get(self, url, headers=None, timeout=None):
        self.urls.append(url)
        assert headers["User-Agent"].startswith("ff-startsit/")
        for frag, name in self.PAGES.items():
            if f"/{frag}/weekly/" in url:
                return Resp(page(name))
        raise requests.HTTPError("404")


class Schedule:
    def __init__(self, games):
        self.games = games

    def for_week(self, week):
        return self.games


def fixture_schedule():
    return Schedule(schedule_from([parse_page(page(n), p, "ppr") for n, p in
                                   [("cbs_ppr_RB_weekly.html", "RB"), ("cbs_ppr_QB_weekly.html", "QB"),
                                    ("cbs_ppr_DST_weekly.html", "DEF")]]))


def roster():
    return [Player("g", "Jahmyr Gibbs", "DET", "RB"), Player("w", "Kenneth Walker III", "KC", "RB"),
            Player("k", "Zonovan Knight", "ARI", "RB"), Player("a", "Josh Allen", "BUF", "QB"),
            Player("j", "Jacksonville Jaguars", "JAX", "DEF"), Player("x", "Some Kicker", "DAL", "K")]


def fetch(scoring="ppr", **kw):
    session = kw.pop("session", None) or Session()
    fetcher = CbsFetcher(schedule=kw.pop("schedule", fixture_schedule()), session=session, **kw)
    return fetcher, session, fetcher.fetch(roster(), 8, scoring)


def test_three_boards_with_each_analysts_own_ranks():
    _, _, (cbs, eisenberg, richard) = fetch()
    assert (cbs.short, eisenberg.short, richard.short) == ("CBS", "Eisenberg", "Richard")
    assert {k: e.pos_rank for k, e in cbs.entries.items()} == {"g": 1, "w": 2, "k": 47, "a": 1, "j": 1}
    assert eisenberg.entries["w"].pos_rank == 2 and richard.entries["w"].pos_rank == 3
    # Unranked by Eisenberg: no entry, so the cell renders as a dash.
    assert "k" not in eisenberg.entries and richard.entries["k"].pos_rank == 39


def test_a_missing_page_costs_that_position_only_and_never_raises():
    _, _, (cbs, eisenberg, _) = fetch()
    assert "K" in cbs.unavailable and "404" in cbs.unavailable["K"]
    assert "K" in eisenberg.unavailable
    assert "x" not in cbs.entries and "g" in cbs.entries


def test_half_ppr_reads_the_ppr_list_and_says_so():
    _, session, (cbs, eisenberg, richard) = fetch("half")
    assert all("/ppr/" in url for url in session.urls)
    assert (cbs.header, eisenberg.header, richard.header) == ("CBS Wk (PPR)", "Eisenberg Wk (PPR)",
                                                              "Richard Wk (PPR)")


def test_standard_reads_the_standard_list():
    session = Session()
    fetch("std", session=session)
    assert all("/standard/" in url for url in session.urls)


def test_a_stale_week_shows_nothing():
    stale = Schedule({t: GameContext(home_team=t, away_team="ZZZ")
                      for t in fixture_schedule().games})
    _, _, (cbs, _, _) = fetch(schedule=stale)
    assert cbs.entries == {}
    assert "another week's matchups" in cbs.unavailable["RB"]


def test_without_a_schedule_the_page_is_kept_and_the_gap_is_said():
    fetcher, _, (cbs, _, _) = fetch(schedule=Schedule({}))
    assert "g" in cbs.entries
    assert "matchups not checked" in " ".join(fetcher.status.values())


def test_pages_are_cached_on_disk(tmp_path):
    fetch(data_dir=tmp_path)
    again = Session()
    _, _, (cbs, _, _) = fetch(data_dir=tmp_path, session=again)
    assert "g" in cbs.entries
    assert not any("/RB/" in url for url in again.urls)


# --- wiring ------------------------------------------------------------------

class Ecr(Signal):
    name = "ecr"
    higher_is_better = False

    def is_available(self):
        return True

    def fetch(self, week, players):
        return {p.key: SignalValue(float(i + 1)) for i, p in enumerate(players)}


def test_report_shows_fp_then_the_cbs_columns(tmp_path):
    settings = Settings(data_dir=tmp_path, cbs_rankings=True)
    fetcher = CbsFetcher(schedule=fixture_schedule(), session=Session())
    recs = report.rank_each_position(settings, roster(), 8, signals=[Ecr()], cbs_fetcher=fetcher)
    rb = recs["RB"]
    assert [b.short for b in rb.expert_boards] == ["FP", "CBS", "Eisenberg", "Richard"]
    header = next(l for l in render.render_markdown(rb).splitlines() if l.startswith("| #"))
    assert "| FP Wk | CBS Wk | Eisenberg Wk | Richard Wk |" in header
    assert any(identity[:2] == ("board", "cbs") for identity, _ in rb.source_status)


def test_cbs_is_off_unless_asked_for(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("CBS fetched while disabled")
    monkeypatch.setattr("ff_startsit.boards.cbs.CbsFetcher.fetch", boom)
    recs = report.rank_each_position(Settings(data_dir=tmp_path), roster(), 8, signals=[Ecr()])
    assert [b.short for b in recs["RB"].expert_boards] == ["FP"]


def test_discord_comparison_carries_the_consensus_columns_only(tmp_path):
    settings = Settings(data_dir=tmp_path, cbs_rankings=True)
    fetcher = CbsFetcher(schedule=fixture_schedule(), session=Session())
    rb = report.rank_each_position(settings, roster(), 8, signals=[Ecr()], cbs_fetcher=fetcher)["RB"]
    line = close_call_comparison(rb)
    assert line == "Jahmyr Gibbs vs Kenneth Walker III — FP 1–2 · CBS 1–2"
    assert "Eisenberg" not in line and "Richard" not in line


def test_discord_alerts_include_the_comparison_for_a_close_call(tmp_path):
    from ff_startsit.output.discord import _alerts
    settings = Settings(data_dir=tmp_path, cbs_rankings=True)
    fetcher = CbsFetcher(schedule=fixture_schedule(), session=Session())
    rb = report.rank_each_position(settings, roster(), 8, signals=[Ecr()], cbs_fetcher=fetcher)["RB"]
    rb.close_call = True
    alerts = _alerts([], {"RB": rb})
    assert "[RB] Jahmyr Gibbs vs Kenneth Walker III — FP 1–2 · CBS 1–2" in alerts


def test_status_line_says_which_list_and_that_time_is_fetch_time(tmp_path):
    settings = Settings(data_dir=tmp_path, scoring="half", league_label="workTG")
    _, _, boards = fetch("half")
    _, line = report.cbs_status(settings, boards)
    assert line.startswith("workTG: CBS staff consensus, PPR list (CBS has no half-PPR list)")
    assert "fetch time, not CBS's update time" in line


# --- config ------------------------------------------------------------------

@pytest.mark.parametrize("raw,boone,cbs", [
    ("boone,cbs", "boone", True), ("cbs", "", True), (" CBS , Boone ", "boone", True),
    ("boone", "boone", False), ("", "", False), ("off", "", False)])
def test_ff_analysts_takes_a_list(monkeypatch, tmp_path, raw, boone, cbs):
    monkeypatch.setenv("FF_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FF_ANALYSTS", raw)
    settings = load_settings(env_file=tmp_path / "absent")
    assert (settings.analysts, settings.cbs_rankings) == (boone, cbs)


def test_an_unknown_entry_costs_only_itself(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FF_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FF_ANALYSTS", "boone,cbz")
    settings = load_settings(env_file=tmp_path / "absent")
    assert settings.analysts == "boone" and settings.cbs_rankings is False
    assert "cbz" in capsys.readouterr().out
