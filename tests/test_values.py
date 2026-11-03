"""Wednesday values: Boone's trade charts beside Richard's, per league roster,
with every trade idea judged on each chart separately."""
from pathlib import Path

import pytest

from ff_startsit.boards.articles import ArticleFetcher, NOT_POSTED
from ff_startsit.boards.models import ALL_POSITIONS, BoardEntry, ExpertBoard
from ff_startsit.boards.writers import BooneTradeValues, parse_boone_chart
from ff_startsit.models import Player, PlayerScore
from ff_startsit.waivers.models import TradeIdea, WaiverBundle
from ff_startsit.waivers.values import TradeVerdict, build_sheet, chart_split

FIX = Path(__file__).parent / "fixtures" / "boards"


def page(name):
    return (FIX / name).read_text(encoding="utf-8")


class Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status


class Site:
    PAGES = {"/author/justin-boone/": "yahoo_index_boone.html",
             "fantasy-football-week-8-rankings-justin-boone": "yahoo_boone_rankings_hub.html",
             "quarterback-breakdown": "boone_chart_qb.html",
             "running-back-breakdown": "boone_chart_rb.html",
             "wide-receiver-breakdown": "boone_chart_wr.html",
             "tight-end-breakdown": "boone_chart_te.html"}

    def __init__(self, drop=()):
        self.calls, self.drop = [], set(drop)

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        for frag, name in self.PAGES.items():
            if frag in url and frag not in self.drop:
                return Resp(page(name))
        return Resp("", 404)


def roster():
    return [Player("g", "Jahmyr Gibbs", "DET", "RB"), Player("a", "Josh Allen", "BUF", "QB"),
            Player("b", "Brock Bowers", "LV", "TE"), Player("j", "Jaxon Smith-Njigba", "SEA", "WR")]


# --- Boone's charts ------------------------------------------------------------

def test_parse_quarterback_and_running_back_charts():
    qb = parse_boone_chart(page("boone_chart_qb.html"), "QB")
    assert (qb[0].name, qb[0].values) == ("Josh Allen", {"1qb": 39.0, "2qb": 97.0})
    rb = parse_boone_chart(page("boone_chart_rb.html"), "RB")
    assert (rb[0].name, rb[0].values) == ("Jahmyr Gibbs", {"half": 101.0, "ppr": 104.0})


def test_a_table_without_rank_and_player_is_refused():
    with pytest.raises(ValueError):
        parse_boone_chart("<table class=\"content-table\"><tr><td>Name</td><td>Value</td></tr></table>", "QB")


def test_found_through_the_rankings_hub_in_six_requests():
    site = Site()
    board = BooneTradeValues(ArticleFetcher(session=site), season=2026).fetch(roster(), 8, "half")
    assert {k: e.value for k, e in board.entries.items()} == {"g": 101.0, "a": 39.0, "b": 64.0, "j": 93.0}
    assert board.unavailable == {}
    assert len(site.calls) == 6       # author page, hub, four charts


def test_format_and_superflex_choose_the_column():
    src = BooneTradeValues(ArticleFetcher(session=Site()), season=2026)
    sf = src.fetch(roster(), 8, "ppr", superflex=True)
    assert sf.entries["a"].value == 97.0 and sf.entries["g"].value == 104.0


def test_standard_scoring_has_no_boone_values_but_qbs_still_do():
    board = BooneTradeValues(ArticleFetcher(session=Site()), season=2026).fetch(roster(), 8, "std")
    assert set(board.entries) == {"a"}
    assert board.unavailable["RB"] == "Boone publishes no standard-scoring values"


def test_a_hand_passed_chart_url_finds_the_other_three():
    """His author page and hub may not link this week's charts yet."""
    site = Site(drop={"fantasy-football-week-8-rankings-justin-boone"})
    url = ("https://sports.yahoo.com/fantasy/article/2026-trade-value-charts--justin-boones-"
           "fantasy-football-tight-end-breakdown-for-week-8-153628570.html")
    board = BooneTradeValues(ArticleFetcher(session=site), season=2026,
                             override_urls=[url]).fetch(roster(), 8, "ppr")
    assert set(board.entries) == {"g", "a", "b", "j"}


def test_a_missing_position_is_named():
    site = Site(drop={"tight-end-breakdown"})
    board = BooneTradeValues(ArticleFetcher(session=site), season=2026).fetch(roster(), 8, "ppr")
    assert "b" not in board.entries
    assert board.unavailable["TE"] == "Boone's Week 8 TE trade chart not found"


def test_not_found_says_how_to_fix_it():
    board = BooneTradeValues(ArticleFetcher(session=Site()), season=2026).fetch(roster(), 9, "ppr")
    reason = board.unavailable[ALL_POSITIONS]
    assert NOT_POSTED in reason and "--boone-chart" in reason


# --- disagreement and verdicts ---------------------------------------------------

@pytest.mark.parametrize("a,b,expected", [
    (12, 24, "Boone much higher (RB12 vs RB24)"),
    (3, 8, None),                     # 5 spots: under the 6-spot floor
    (30, 38, None),                   # 8 spots, but under 35% of 30
    (30, 42, "Boone much higher (RB30 vs RB42)"),
])
def test_chart_split(a, b, expected):
    got = chart_split(BoardEntry(pos_rank=a), BoardEntry(pos_rank=b), "Boone", "Richard", "RB")
    assert got == expected


def test_chart_split_needs_both_charts():
    assert chart_split(BoardEntry(pos_rank=1), None, "A", "B", "RB") is None


def _score(key, name, pos):
    return PlayerScore(player=Player(key, name, "KC", pos))


def idea(send, get):
    return TradeIdea(partner="Rival FC", you_send=tuple(send), you_get=tuple(get),
                     your_gain=1.0, their_gain=1.0)


def test_verdict_is_per_chart_and_never_mixed():
    from ff_startsit.waivers.values import _net
    t = idea([_score("s", "Sender", "RB")], [_score("r", "Receiver", "WR")])
    richard = ExpertBoard("richard_chart", "Richard", "R", entries={
        "s": BoardEntry(value=20), "r": BoardEntry(value=26)})
    boone = ExpertBoard("boone_trade_values", "Boone", "B", entries={
        "s": BoardEntry(value=60), "r": BoardEntry(value=51)})
    v = TradeVerdict(t, {"Richard": _net(t, richard), "Boone": _net(t, boone)})
    assert v.net == {"Richard": 6.0, "Boone": -9.0} and v.split
    off = ExpertBoard("x", "X", "X", entries={"s": BoardEntry(value=5)})
    assert _net(t, off) is None       # Receiver is off that chart: no partial sum


# --- the sheet -------------------------------------------------------------------

class Chart:
    def __init__(self, sid, short, values, ranks):
        self.id, self.short, self.label = sid, short, f"{short} chart"
        self.values, self.ranks = values, ranks
        self.asked = None

    def fetch(self, players, week, scoring, superflex=False):
        self.asked = sorted(p.key for p in players)
        return ExpertBoard(self.id, self.short, self.label, entries={
            k: BoardEntry(value=self.values[k], pos_rank=self.ranks[k])
            for k in self.asked if k in self.values})


def bundle():
    mine = [_score("g", "Gibbs", "RB"), _score("k", "My K", "K"), _score("q", "My Qb", "QB")]
    for s, rank in zip(mine, (3, 200, 40)):
        s.season_rank = rank
    theirs = _score("t", "Their Wr", "WR")
    b = WaiverBundle(label="workTG", scoring="ppr", week=9, trades_considered=True,
                     trades=[idea([mine[0]], [theirs])])
    from ff_startsit.models import Recommendation
    b.recs = {"RB": Recommendation(9, "ppr", {}, mine)}
    return b, [s.player for s in mine]


def test_sheet_rows_are_your_skill_players_with_both_charts_and_fp_ros():
    b, players = bundle()
    richard = Chart("richard_chart", "Richard", {"g": 56, "q": 10, "t": 40}, {"g": 1, "q": 14, "t": 9})
    boone = Chart("boone_trade_values", "Boone", {"g": 104, "q": 30, "t": 90}, {"g": 1, "q": 4, "t": 6})
    sheet = build_sheet(b, players, [richard, boone], 9)
    assert richard.asked == ["g", "q", "t"]                      # your players + the trade's
    assert [r.player.key for r in sheet.rows] == ["q", "g"]      # QB first; no kicker
    assert [x.short for x in sheet.boards] == ["Richard", "Boone", "FP ROS"]
    qb = sheet.rows[0]
    assert qb.entries["fantasypros_ros"].pos_rank == 40
    assert qb.split == "Boone much higher (QB4 vs QB14)"
    assert sheet.rows[1].split is None
    assert sheet.verdicts[0].net == {"Richard": -16.0, "Boone": -14.0}
    assert sheet.rows[1].charts_cell == "agree"


def test_an_unread_chart_neither_agrees_nor_prices_a_trade():
    from ff_startsit.output.board import verdict_text
    b, players = bundle()

    class NotUp(Chart):
        def fetch(self, *a, **k):
            board = ExpertBoard(self.id, self.short, self.label)
            board.unavailable[ALL_POSITIONS] = "not posted yet"
            return board
    sheet = build_sheet(b, players, [NotUp("richard_chart", "Richard", {}, {}),
                                     NotUp("boone_trade_values", "Boone", {}, {})], 9)
    assert {r.charts_cell for r in sheet.rows} == {"—"}
    assert verdict_text(sheet.verdicts[0]) == "no chart read yet"


def test_preseason_refusal_carries_its_banner_and_fetches_nothing():
    b = WaiverBundle(label="x", scoring="ppr", week=1, banner="not yet")
    src = Chart("richard_chart", "Richard", {}, {})
    sheet = build_sheet(b, [Player("g", "G", "KC", "RB")], [src], 1)
    assert sheet.banner == "not yet" and src.asked is None and sheet.rows == []


# --- rendering -------------------------------------------------------------------

def sheet():
    b, players = bundle()
    return build_sheet(b, players, [
        Chart("richard_chart", "Richard", {"g": 56, "q": 10, "t": 40}, {"g": 1, "q": 14, "t": 9}),
        Chart("boone_trade_values", "Boone", {"g": 104, "q": 30, "t": 90}, {"g": 1, "q": 4, "t": 6})], 9)


def test_markdown_sheet_verdicts_and_marker():
    from ff_startsit.waivers.render import render_values_digest
    md = render_values_digest(9, [sheet()], generated_on="2026-11-04")
    assert "| Player | Pos | Team | Richard | Boone | FP ROS | Charts |" in md
    assert "| My Qb | QB | KC | 10 · QB14 | 30 · QB4 | #40 | Boone much higher (QB4 vs QB14) |" in md
    assert "Send Gibbs to Rival FC for Their Wr — Richard −16 · Boone −14" in md
    assert ("read=boone_trade_values:QB,boone_trade_values:RB,boone_trade_values:TE,"
            "boone_trade_values:WR,richard_chart:QB,richard_chart:RB,richard_chart:TE,"
            "richard_chart:WR -->") in md


def test_html_values_page_is_in_the_shared_nav():
    from ff_startsit.output.html import build_values_html
    html = build_values_html(9, [sheet()], generated_on="2026-11-04")
    assert "<a href='values.html' class='active'>Trade values</a>" in html
    assert "Boone much higher" in html and "Richard −16" in html


def test_discord_values_has_every_player_and_trade():
    from ff_startsit.output.discord import build_values_payloads
    payloads = build_values_payloads(9, [sheet()])
    fields = [f for p in payloads for e in p["embeds"] for f in e["fields"]]
    assert fields[0]["name"] == "My Qb (QB, KC)"
    assert fields[0]["value"] == "Richard 10 · QB14 | Boone 30 · QB4 | FP ROS #40\n⚠️ Boone much higher (QB4 vs QB14)"
    assert fields[-1]["name"].startswith("🤝 Send Gibbs")


def test_chatops_values():
    from ff_startsit.chatops import parse_command
    assert parse_command("/values") == ["values"]
    assert parse_command("/values all week 9") == ["values", "--all-leagues", "--week", "9"]


# --- Pages: keeping the pages this run didn't build ------------------------------

class PagesSite:
    def __init__(self, statuses):
        self.statuses = statuses

    def get(self, url, headers=None, timeout=None):
        name = url.rsplit("/", 1)[1].split("?")[0]
        status = self.statuses.get(name, 404)
        if isinstance(status, Exception):
            raise status
        return Resp(f"<html>{name}</html>" if status == 200 else "", status)


def test_restore_pages_outcomes(tmp_path, monkeypatch):
    from ff_startsit.workflow import restore_pages, verify_site
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "sum"))
    site = tmp_path / "site"
    site.mkdir()
    (site / "values.html").write_text("built this run")
    outcome = restore_pages("https://x.github.io/repo", site,
                            ["index.html", "waivers.html", "values.html"],
                            session=PagesSite({"index.html": 200, "waivers.html": 200}))
    assert outcome == {"index.html": "restored", "waivers.html": "restored", "values.html": "built"}
    assert (site / "values.html").read_text() == "built this run"
    assert verify_site(site)


def test_a_failed_required_restore_blocks_the_deploy(tmp_path, monkeypatch):
    from ff_startsit.workflow import restore_pages, verify_site
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "sum"))
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("x")
    (site / "waivers.html").write_text("x")
    restore_pages("https://x", site, ["values.html"], session=PagesSite({"values.html": 503}))
    assert not verify_site(site)
    assert "values.html" in (tmp_path / "sum").read_text()


def test_optional_and_never_published_pages_do_not_block(tmp_path, monkeypatch):
    from ff_startsit.workflow import restore_pages, verify_site
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "sum"))
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("x")
    (site / "waivers.html").write_text("x")
    assert restore_pages("https://x", site, ["values.html"], session=PagesSite({}))["values.html"] == "absent"
    out = restore_pages("https://x", site, ["values.html"], optional=True,
                        session=PagesSite({"values.html": ConnectionError("down")}))
    assert out["values.html"] == "skipped" and verify_site(site)


def test_a_fresh_site_takes_its_first_deploy(tmp_path, monkeypatch):
    """Each workflow requires the page only the other builds; on a site that
    has never deployed both answer 404, and neither could ever go first."""
    from ff_startsit.workflow import restore_pages, verify_site
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "sum"))
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("built this run")
    assert restore_pages("https://x", site, ["waivers.html"], session=PagesSite({}))["waivers.html"] == "absent"
    restore_pages("https://x", site, ["values.html"], optional=True, session=PagesSite({}))
    assert verify_site(site)
    assert sorted(p.name for p in site.iterdir()) == ["index.html"]   # no marker uploaded


def test_an_absent_page_never_excuses_an_empty_site(tmp_path, monkeypatch):
    from ff_startsit.workflow import restore_pages, verify_site
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "sum"))
    site = tmp_path / "site"
    site.mkdir()
    restore_pages("https://x", site, ["index.html", "waivers.html"], session=PagesSite({}))
    assert not verify_site(site)


def test_a_retry_that_finds_a_missing_boone_chart_is_news():
    """Boone's charts post one at a time: a first run missing TE and a retry
    that found it used to record the same marker, suppressing the retry."""
    from ff_startsit.boards.models import ALL_POSITIONS, ExpertBoard
    from ff_startsit.waivers.board import board_marker, report_has_news
    from ff_startsit.waivers.values import sheets_read

    class Sheet:
        def __init__(self, boards):
            self.boards = boards

    partial = ExpertBoard("boone_values", "Boone", "Boone", unavailable={"TE": "not found"})
    full = ExpertBoard("boone_values", "Boone", "Boone")
    first = board_marker(2026, 9, sheets_read([Sheet([partial])]))
    retry = board_marker(2026, 9, sheets_read([Sheet([full])]))
    assert first != retry
    assert report_has_news(retry, first)
    assert not report_has_news(first, retry)             # fewer: keep the better post
    assert not report_has_news(retry, retry)
    gone = ExpertBoard("boone_values", "Boone", "Boone", unavailable={ALL_POSITIONS: "down"})
    assert sheets_read([Sheet([gone])]) == []
