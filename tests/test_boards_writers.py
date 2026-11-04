"""The Tuesday expert board: Boone's and Eisenberg's waiver columns and
Richard's trade chart, side by side for each league's free agents.

Fixtures are live pages fetched 2026-11-02, trimmed to the markup each parser
reads (bylines, headings, tables, cards, list items) with the prose removed.
"""
from pathlib import Path

import pytest

from ff_startsit.boards.articles import (NOT_POSTED, ArticleFetcher, not_posted,
                                         verified_headline, week_links)
from ff_startsit.boards.models import ALL_POSITIONS, BoardEntry, ExpertBoard
from ff_startsit.boards.writers import (BooneWaivers, EisenbergWaivers, RichardChart,
                                        chart_column, faab_short, parse_boone_waivers,
                                        parse_eisenberg, parse_richard, rank_by_value,
                                        tier_short)
from ff_startsit.models import Player
from ff_startsit.output.board import waiver_cell
from ff_startsit.waivers.board import (board_marker, build_board, fp_ros_board, has_news,
                                       report_has_news)

FIX = Path(__file__).parent / "fixtures" / "boards"


def page(name):
    return (FIX / name).read_text(encoding="utf-8")


# --- parsers -------------------------------------------------------------------

def test_eisenberg_priority_order_team_and_faab_per_position():
    listed = parse_eisenberg(page("eisenberg_waiver_column.html"))
    rbs = [(x.name, x.team, x.pos_rank, x.bid) for x in listed if x.position == "RB"]
    assert rbs == [("Seth McGowan", "IND", 1, "10%+ FAAB"),
                   ("Zonovan Knight", "ARI", 2, "10%+ FAAB"),
                   ("Dameon Pierce", "PHI", 3, "10%+ FAAB")]
    assert {x.position for x in listed} == {"QB", "RB", "WR", "TE"}


def test_eisenberg_off_position_label_takes_the_sections_position():
    """Travis Hunter is listed as CB among Eisenberg's receivers."""
    body = page("eisenberg_waiver_column.html").replace(
        'PlayerObjectV4-playerPosition"> WR', 'PlayerObjectV4-playerPosition"> CB', 1)
    first_wr = next(x for x in parse_eisenberg(body) if x.name == "KC Concepcion")
    assert first_wr.position == "WR"


@pytest.mark.parametrize("raw,short", [("at least 10", "10%+ FAAB"), ("5-10", "5–10% FAAB"),
                                       ("1", "1% FAAB"), ("5 - 10", "5–10% FAAB")])
def test_faab_short(raw, short):
    assert faab_short(raw) == short


def boone():
    return {x.name: x for x in parse_boone_waivers(page("boone_waiver_column.html"))}


def test_boone_featured_player_carries_his_fab_bid_and_top10_rank():
    washington = boone()["Mike Washington Jr."]
    assert (washington.position, washington.team, washington.pos_rank) == ("RB", "LV", 1)
    assert washington.bid == "$29" and washington.tier == "top-10 #1"


def test_boone_shared_heading_gives_both_players_the_bid():
    b = boone()
    assert (b["Antonio Williams"].bid, b["Treylon Burks"].bid) == ("$9", "$4")   # "$9/$4"
    assert b["Antonio Williams"].tier.startswith("top-10")


def test_boone_tiers_and_if_available_note():
    b = boone()
    assert b["Sam Darnold"].tier == "streamer" and b["Sam Darnold"].team is None
    assert b["Tank Bigsby"].tier == "IR stash"
    assert b["Kirk Cousins"].pos_rank == 3
    assert b["Kirk Cousins"].tier == "No. 3 if available (56% rostered)"
    assert b["James Conner"].tier == "featured, IR"


def test_boone_list_names_lose_their_matchup():
    assert "Reggie Virgil" in boone()     # "Reggie Virgil at SE (0% rostered)"


@pytest.mark.parametrize("label,short", [("Dart-throw flex options", "dart throw"),
                                         ("Injured-reserve stashes", "IR stash"),
                                         ("Upside stashes to roster", "upside stash"),
                                         ("Risky flex options (if healthy)", "risky flex (if healthy)")])
def test_tier_short(label, short):
    assert tier_short(label) == short


def test_richard_values_per_format_and_dashes_mean_none():
    r = {x.name: x for x in parse_richard(page("richard_trade_chart.html"))}
    assert r["Jahmyr Gibbs"].values == {"non": 53.0, "0.5": 54.5, "ppr": 56.0}
    assert r["Josh Allen"].values == {"1qb-4": 21.0, "1qb-6": 21.0, "2qb": 46.0}
    shough = r["Tyler Shough"]
    assert shough.values == {"2qb": 12.0}              # "--" in 1QB formats


def test_chart_column_by_scoring_and_superflex():
    assert chart_column("RB", "half") == "0.5" and chart_column("WR", "std") == "non"
    assert chart_column("TE", "ppr") == "ppr"
    assert chart_column("QB", "ppr") == "1qb-4" and chart_column("QB", "ppr", True) == "2qb"


def test_rank_by_value_shares_ties():
    from ff_startsit.boards.writers import Listed
    items = [Listed("a", "QB", values={"x": 9}), Listed("b", "QB", values={"x": 7}),
             Listed("c", "QB", values={"x": 7}), Listed("d", "QB", values={"x": 5})]
    assert rank_by_value(items, "x") == {0: 1, 1: 2, 2: 2, 3: 4}


@pytest.mark.parametrize("parser", [parse_boone_waivers, parse_eisenberg, parse_richard])
def test_an_unrecognised_page_is_refused_not_guessed(parser):
    with pytest.raises(ValueError):
        parser("<html><body>nothing here</body></html>")


# --- discovery and verification ------------------------------------------------

def test_week_links_need_the_week_and_skip_the_college_column():
    idx = page("yahoo_index_boone.html")
    links = week_links(idx, "https://sports.yahoo.com", 9, ("waiver-wire",), ("college",))
    assert links and all("week-9" in u for u in links)
    assert week_links(idx, "https://sports.yahoo.com", 10, ("waiver-wire",), ("college",)) == []
    assert week_links(page("cbs_index_richard.html"), "https://www.cbssports.com", 8,
                      ("trade-chart",))[0].startswith("https://www.cbssports.com/fantasy/football/news/dave-richards-week-8")


def test_verified_headline_checks_byline_week_and_kind():
    body = page("richard_trade_chart.html")
    assert verified_headline(body, "Dave Richard", 2026, 8, ("trade chart",))
    assert verified_headline(body, "Jamey Eisenberg", 2026, 8, ("trade chart",)) is None
    assert verified_headline(body, "Dave Richard", 2026, 9, ("trade chart",)) is None
    assert verified_headline(body, "Dave Richard", 2025, 8, ("trade chart",)) is None
    assert verified_headline(body, "Dave Richard", 2026, 8, ("waiver wire",)) is None


class Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status


class Site:
    """Serves fixtures for the URLs a source asks for."""
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        assert headers["User-Agent"].startswith("ff-startsit/")
        for frag, body in self.pages.items():
            if frag in url:
                return Resp(body)
        return Resp("", 404)


def richard_site():
    return Site({"/writers/dave-richard/": page("cbs_index_richard.html"),
                 "dave-richards-week-8": page("richard_trade_chart.html")})


def test_find_article_happy_path_and_page_cache(tmp_path):
    site = richard_site()
    src = RichardChart(ArticleFetcher(tmp_path, session=site), season=2026)
    listed, url, reason = src.article(8)
    assert reason is None and "dave-richards-week-8" in url and listed
    again = Site({})
    assert RichardChart(ArticleFetcher(tmp_path, session=again), season=2026).article(8)[1] == url
    assert again.calls == []           # both pages came from the disk cache


def test_no_link_for_the_week_reads_as_not_posted_yet():
    src = RichardChart(ArticleFetcher(session=richard_site()), season=2026)
    _, _, reason = src.article(12)
    assert NOT_POSTED in reason and not_posted(reason)
    assert "or too old to be listed" in reason


def test_an_unreachable_author_page_is_not_not_posted():
    src = RichardChart(ArticleFetcher(session=Site({})), season=2026)
    _, _, reason = src.article(8)
    assert "author page unavailable" in reason and not not_posted(reason)


def test_wrong_byline_is_refused():
    site = Site({"/writers/dave-richard/": page("cbs_index_richard.html"),
                 "dave-richards-week-8": page("richard_trade_chart.html").replace(
                     '"name": "Dave Richard"', '"name": "Someone Else"')})
    _, _, reason = RichardChart(ArticleFetcher(session=site), season=2026).article(8)
    assert "could not be verified" in reason


def test_a_verified_article_in_an_unknown_layout_is_refused():
    body = page("richard_trade_chart.html").split("<h2>")[0] + "</body></html>"
    site = Site({"/writers/dave-richard/": page("cbs_index_richard.html"),
                 "dave-richards-week-8": body})
    _, _, reason = RichardChart(ArticleFetcher(session=site), season=2026).article(8)
    assert "layout not recognised" in reason


# --- sources matched to a league -------------------------------------------------

def players():
    return [Player("g", "Jahmyr Gibbs", "DET", "RB"), Player("a", "Josh Allen", "BUF", "QB"),
            Player("s", "Tyler Shough", "NO", "QB")]


def test_richard_board_uses_the_leagues_format():
    src = RichardChart(ArticleFetcher(session=richard_site()), season=2026)
    half = src.fetch(players(), 8, "half")
    assert half.entries["g"].value == 54.5 and half.entries["g"].pos_rank == 1
    assert "s" not in half.entries                     # no 1QB value
    sf = src.fetch(players(), 8, "ppr", superflex=True)
    assert sf.entries["a"].value == 46.0 and sf.entries["s"].value == 12.0


def test_eisenberg_board_matches_by_name_position_and_team():
    site = Site({"/writers/jamey-eisenberg/": page("cbs_index_eisenberg.html"),
                 "week-8-waiver-wire": page("eisenberg_waiver_column.html")})
    board = EisenbergWaivers(ArticleFetcher(session=site), season=2026).fetch(
        [Player("m", "Seth McGowan", "IND", "RB"), Player("x", "Not Listed", "IND", "RB")], 8, "ppr")
    assert board.entries["m"] == BoardEntry(pos_rank=1, bid="10%+ FAAB")
    assert "x" not in board.entries and board.url


def test_an_unreadable_source_is_an_empty_board_with_its_reason():
    board = BooneWaivers(ArticleFetcher(session=Site({})), season=2026).fetch(players(), 9, "ppr")
    assert board.entries == {} and "author page unavailable" in board.unavailable[ALL_POSITIONS]


# --- the board -------------------------------------------------------------------

def _board(sid, entries, short=None):
    return ExpertBoard(sid, short or sid, sid, entries=entries)


def test_rows_are_free_agents_writers_named_best_supported_first():
    fas = [Player("a", "Alpha", "KC", "RB"), Player("b", "Bravo", "KC", "WR"),
           Player("c", "Charlie", "KC", "TE"), Player("d", "Delta", "KC", "QB")]
    boards = [_board("boone_waivers", {"a": BoardEntry(pos_rank=4), "b": BoardEntry(pos_rank=1)}),
              _board("eisenberg_waivers", {"a": BoardEntry(pos_rank=2)}),
              _board("richard_chart", {"c": BoardEntry(value=9, pos_rank=20)}),
              fp_ros_board({"a": 50, "b": 80, "c": 120, "d": 30})]

    class Target:
        def __init__(self, key, drop=None):
            self.score = type("S", (), {"player": Player(key, key, "KC", "RB")})()
            self.drop = type("D", (), {"player": Player("z", drop, "KC", "RB")})() if drop else None
    rows = build_board(fas, boards, adds=[Target("a", "My Rb3")], alternates=[Target("b")])
    assert [r.player.key for r in rows] == ["a", "b", "c"]   # d: only FP ROS -> not a row
    assert [r.listed_by for r in rows] == [2, 1, 0]
    assert rows[0].tool == "add (drop My Rb3)" and rows[1].tool == "also consider"
    assert rows[2].entries["fantasypros_ros"].pos_rank == 120


def test_waiver_cells():
    assert waiver_cell(BoardEntry(pos_rank=1, bid="$29", tier="top-10 #1"), "RB") == "RB1 · $29 · top-10 #1"
    assert waiver_cell(BoardEntry(pos_rank=2, bid="10%+ FAAB", tier="featured"), "WR") == "WR2 · 10%+ FAAB"
    assert waiver_cell(BoardEntry(value=12.0, pos_rank=31), "RB") == "12 · RB31"
    assert waiver_cell(BoardEntry(pos_rank=88)) == "#88"             # FP ROS: overall
    assert waiver_cell(None) == "—"


def test_ties_break_on_boones_top10_order():
    fas = [Player("w", "Williams", "WAS", "WR"), Player("m", "Washington", "LV", "RB")]
    boards = [_board("boone_waivers", {"w": BoardEntry(pos_rank=1, tier="top-10 #6"),
                                       "m": BoardEntry(pos_rank=1, tier="top-10 #1")})]
    assert [r.player.key for r in build_board(fas, boards)] == ["m", "w"]


# --- the retry gate --------------------------------------------------------------

def test_has_news():
    published = board_marker(2026, 9, ["boone_waivers", "richard_chart"])
    assert has_news(["boone_waivers"], "", 2026, 9)                       # nothing published
    assert not has_news(["boone_waivers", "richard_chart"], published, 2026, 9)
    assert not has_news(["boone_waivers"], published, 2026, 9)           # fewer: keep the better
    assert has_news(["boone_waivers", "eisenberg_waivers", "richard_chart"], published, 2026, 9)
    assert has_news(["boone_waivers"], published, 2026, 10)              # a new week


def test_report_has_news_without_a_marker_only_posts_once():
    assert report_has_news("# digest", "")
    assert not report_has_news("# digest", "an earlier post this week")


def test_a_digest_with_the_boards_off_still_tells_weeks_apart():
    """FF_ANALYSTS unset is the default: no writer board, so the digest used
    to carry no marker, and any earlier open issue (last week's) suppressed
    Discord, the issue and the deploy for every week after it."""
    from ff_startsit.waivers.models import WaiverBundle
    from ff_startsit.waivers.render import render_waiver_digest
    week8 = render_waiver_digest(8, [WaiverBundle(label="a", scoring="ppr", week=8)])
    week9 = render_waiver_digest(9, [WaiverBundle(label="a", scoring="ppr", week=9)])
    assert "week=9 mode=live read= -->" in week9
    assert report_has_news(week9, week8)
    assert not report_has_news(week9, week9)              # the retry is still quiet


def test_live_week_one_is_news_after_a_preseason_refusal_or_rehearsal():
    """Found in review: with no board read, the refusal, the rehearsal and the
    live Week 1 report all wrote the same marker, so the live one was
    suppressed as a repeat."""
    from ff_startsit.waivers.models import WaiverBundle
    from ff_startsit.waivers.render import render_waiver_digest

    def digest(mode):
        return render_waiver_digest(1, [WaiverBundle(label="a", scoring="ppr", week=1, mode=mode)])
    refusal, rehearsal, live = digest("preseason"), digest("rehearsal"), digest("live")
    assert "mode=preseason" in refusal and "mode=live" in live
    assert report_has_news(rehearsal, refusal)
    assert report_has_news(live, refusal)
    assert report_has_news(live, rehearsal)
    assert report_has_news(live, refusal + "\n" + rehearsal)
    assert not report_has_news(live, live)
    assert not report_has_news(rehearsal, rehearsal)
    # A marker written before modes existed reads as a live report.
    legacy = "<!-- ff-board season=2026 week=9 read=boone_waivers -->"
    assert not has_news(["boone_waivers"], legacy, 2026, 9)
    assert has_news(["boone_waivers"], legacy, 2026, 9, mode="rehearsal")


def test_one_live_league_makes_the_run_live():
    from ff_startsit.waivers.board import run_mode
    from ff_startsit.waivers.models import WaiverBundle
    assert run_mode([WaiverBundle(label="a", scoring="ppr", week=1, mode="preseason"),
                     WaiverBundle(label="b", scoring="ppr", week=1)]) == "live"
    assert run_mode([WaiverBundle(label="a", scoring="ppr", week=1, mode="preseason"),
                     WaiverBundle(label="b", scoring="ppr", week=1, mode="rehearsal")]) == "rehearsal"


def test_publish_news_writes_the_step_output(tmp_path):
    from ff_startsit.workflow import publish_news
    current = tmp_path / "waivers.md"
    current.write_text("x\n" + board_marker(2026, 9, ["boone_waivers", "eisenberg_waivers"]))
    previous = tmp_path / "previous.md"
    previous.write_text(board_marker(2026, 9, ["boone_waivers"]))
    out = tmp_path / "out"
    assert publish_news(current, previous, output=out) is True
    assert out.read_text() == "news=true\n"
    assert publish_news(current, tmp_path / "missing.md", output=out) is True


# --- rendering -------------------------------------------------------------------

def bundle_with_board(rows=3):
    from ff_startsit.waivers.board import BoardRow
    from ff_startsit.waivers.models import WaiverBundle
    boards = [_board("boone_waivers", {}, "Boone"), _board("eisenberg_waivers", {}, "Eisenberg"),
              _board("richard_chart", {}, "Richard"), fp_ros_board({})]
    boards[1].unavailable[ALL_POSITIONS] = f"Jamey Eisenberg's Week 9 waiver column {NOT_POSTED}"
    b = WaiverBundle(label="workTG", scoring="ppr", week=9, expert_boards=boards)
    b.expert_rows = [BoardRow(Player(str(i), f"D'Andre {i}", "LV", "RB"),
                              {"boone_waivers": BoardEntry(pos_rank=i, bid="$9")}, 1,
                              "add (drop My Rb3)" if i == 1 else None) for i in range(1, rows + 1)]
    return b


def test_markdown_board_and_marker():
    from ff_startsit.waivers.render import render_waiver_digest
    md = render_waiver_digest(9, [bundle_with_board()], generated_on="2026-11-03")
    assert "### What the writers say" in md
    assert "| Player | Pos | Team | Boone | Eisenberg | Richard | FP ROS | This tool |" in md
    assert "| D'Andre 1 | RB | LV | RB1 · $9 | — | — | — | add (drop My Rb3) |" in md
    assert "Eisenberg's Week 9 waiver column not posted yet" in md
    assert "<!-- ff-board season=" in md and "week=9 mode=live read=boone_waivers,richard_chart -->" in md


def test_html_board():
    from ff_startsit.output.html import build_waivers_html
    page_ = build_waivers_html(9, [bundle_with_board()], generated_on="2026-11-03")
    assert "<h3>What the writers say</h3>" in page_ and "D&#x27;Andre 1" in page_
    assert "not posted yet" in page_


def test_discord_board_is_one_message_per_league_split_into_25_field_embeds():
    from ff_startsit.output.discord import build_waiver_board_payloads
    big = bundle_with_board(30)
    for row in big.expert_rows:      # two writers each: every row is a lead row
        row.listed_by = 2
    payloads = build_waiver_board_payloads(9, [big, bundle_with_board(2)])
    assert len(payloads) == 2
    first = payloads[0]["embeds"]
    assert [len(e["fields"]) for e in first] == [25, 5]
    assert first[0]["fields"][0]["name"] == "1. D'Andre 1 (RB, LV)"
    assert first[0]["fields"][0]["value"] == ("Boone RB1 · $9 | Eisenberg — | Richard — | FP ROS —"
                                             "\nThis tool: add (drop My Rb3)")
    assert "not posted yet" in first[0]["description"]


def test_discord_board_splits_a_message_over_6000_characters():
    from ff_startsit.output.discord import build_waiver_board_payloads
    b = bundle_with_board(60)
    for row in b.expert_rows:
        row.entries["boone_waivers"] = BoardEntry(pos_rank=1, tier="x" * 200)
        row.listed_by = 2            # every row a lead row, so all 60 go out
    payloads = build_waiver_board_payloads(9, [b])
    assert len(payloads) > 1
    from ff_startsit.output.discord import _embed_chars
    for p in payloads:
        assert sum(_embed_chars(e) for e in p["embeds"]) <= 6000 and len(p["embeds"]) <= 10
    assert sum(len(e["fields"]) for p in payloads for e in p["embeds"]) == 60


def test_no_board_section_when_the_board_is_off():
    from ff_startsit.waivers.models import WaiverBundle
    from ff_startsit.waivers.render import render_waiver_digest
    md = render_waiver_digest(9, [WaiverBundle(label="x", scoring="ppr", week=9)])
    assert "What the writers say" not in md
    assert "week=9 mode=live read= -->" in md        # the retry gate still needs the week


# --- quotes come from the board's own articles (Phase 5) -----------------------

def test_boone_quotes_come_from_his_column_not_the_sidebar():
    """Yahoo's first <article> is only the header, and the page carries
    "Recommended Stories" headlines naming players. Quotes come from his own
    section headings through his drop list, nothing after."""
    body = page("boone_waiver_column.html") + (
        "<div><h3>Recommended Stories</h3><p>Zeke Sidebar storms the wire.</p></div>")
    site = Site({"/author/justin-boone/": page("yahoo_index_boone.html"),
                 "week-9-fantasy-football-waiver-wire": body})
    src = BooneWaivers(ArticleFetcher(session=site), season=2026)
    pool = [Player("k", "Kirk Cousins", "LV", "QB"), Player("c", "Zeke Sidebar", "DEN", "RB")]
    quotes = src.mentions(9, pool)
    assert [q.player_key for q in quotes] == ["k"]
    assert quotes[0].author == "Justin Boone" and "yahoo.com" in quotes[0].url
    # Whole sentence: "No. 3" is not a full stop, and no invisible joiner leaks out.
    assert quotes[0].snippet.startswith("Kirk Cousins is 56% rostered but would be the No. 3 QB add")
    assert "\u2060" not in quotes[0].snippet
    fetches = len(site.calls)
    src.fetch(pool, 9, "ppr")
    assert len(site.calls) == fetches          # the board reuses the same download


def test_quotes_are_empty_when_the_article_is_not_up():
    src = EisenbergWaivers(ArticleFetcher(session=Site({})), season=2026)
    assert src.mentions(9, [Player("w", "Mike Washington Jr.", "LV", "RB")]) == []


def test_board_rows_carry_one_quote_per_writer():
    from ff_startsit.waivers.models import ColumnMention
    fas = [Player("a", "Alpha Back", "KC", "RB")]
    boards = [_board("boone_waivers", {"a": BoardEntry(pos_rank=1)})]
    mentions = {"a": [ColumnMention("Justin Boone", "u1", "a", "first"),
                      ColumnMention("Justin Boone", "u1", "a", "second"),
                      ColumnMention("Jamey Eisenberg", "u2", "a", "third"),
                      ColumnMention("Jamey Eisenberg", "u2", "a", "")]}
    row = build_board(fas, boards, mentions=mentions)[0]
    assert [(q.author, q.snippet) for q in row.quotes] == [("Justin Boone", "first"),
                                                           ("Jamey Eisenberg", "third")]


def test_quotes_render_under_the_board_and_the_old_section_steps_aside():
    from ff_startsit.output.html import build_waivers_html
    from ff_startsit.waivers.models import ColumnMention
    from ff_startsit.waivers.render import render_waiver_digest
    b = bundle_with_board(1)
    b.expert_rows[0].quotes = [ColumnMention("Justin Boone", "https://y/a", "1", "He's the add.")]
    md = render_waiver_digest(9, [b], generated_on="2026-11-03")
    assert "**Why, in their words**" in md
    assert "- **D'Andre 1** — [Justin Boone](https://y/a): “He's the add.”" in md
    assert "What the writers said" not in md
    page_ = build_waivers_html(9, [b], generated_on="2026-11-03")
    assert "Why, in their words" in page_ and "What the writers said" not in page_


def test_prose_keeps_sentences_and_drops_headings_lists_and_cards():
    """Found live: stripping every tag ran headings and list items into one
    "sentence", so every quote read "Key injuries to consider Lamar Jackson…"."""
    from ff_startsit.boards.writers import prose
    region = ("<h3>Key injuries to consider</h3><ul><li><div><p>Lamar Jackson (thumb)</p></div></li></ul>"
              "<div class='card'>Seth McGowan RB IND Indianapolis</div>"
              "<p>Seth McGowan took over the backfield and should be added in every league this week</p>"
              "<p>He's the No. 2 RB add behind Washington in my rankings, and worth a real bid.</p>")
    text = prose(region)
    assert "Lamar Jackson" not in text and "Indianapolis" not in text
    assert text.startswith("Seth McGowan took over the backfield and should be added in every league this week.")
