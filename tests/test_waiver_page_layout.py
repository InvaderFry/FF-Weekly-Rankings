"""The waivers page's league tabs and the folded writers' board.

Both are display only: every league's section and every board row stay in the
page, so these pin what is visible by default and that nothing went missing.
"""

import re

from ff_startsit.output.html import (BOARD_MIN_ROWS, board_visible_keys,
                                     build_waivers_html, league_slugs)
from ff_startsit.waivers.models import ColumnMention, WaiverBundle

from .test_boards_writers import bundle_with_board


def _row_classes(html, name):
    m = re.search(r"<tr( class='([^']*)')?><td>" + re.escape(name) + "</td>", html)
    assert m, name
    return (m.group(2) or "").split()


# --- league tabs ---------------------------------------------------------
def test_slugs_are_hash_safe_unique_and_never_all():
    assert league_slugs(["AndyLOT", "Work TG!", "AndyLOT", "all", ""]) == [
        "AndyLOT", "Work-TG", "AndyLOT-2", "all-2", "league-5"]


def test_one_tab_per_league_plus_all_and_every_league_is_in_the_page():
    bundles = [WaiverBundle(label=n, scoring=s, week=9)
               for n, s in (("workTG", "half"), ("AaronRun", "ppr"), ("AndyLOT", "half"))]
    html = build_waivers_html(9, bundles, "2026-11-04")
    radios = re.findall(r"<input type='radio' name='league' class='tab' id='([^']+)' "
                        r"data-slug='([^']+)'( checked)?>", html)
    assert [(i, s) for i, s, _ in radios] == [
        ("lg-0", "workTG"), ("lg-1", "AaronRun"), ("lg-2", "AndyLOT"), ("lg-all", "all")]
    assert [bool(c) for *_, c in radios] == [True, False, False, False]
    for i, name in enumerate(("workTG", "AaronRun", "AndyLOT")):
        assert f"<section class='league-panel' id='league-{i}' data-slug='{name}'>" in html
        assert f"#lg-{i}:checked ~ #league-{i}" in html
    assert "#lg-all:checked ~ section.league-panel" in html
    assert "<label for='lg-1'>AaronRun <small>PPR</small></label>" in html


def test_a_single_league_gets_no_tabs():
    html = build_waivers_html(9, [WaiverBundle(label="solo", scoring="ppr", week=9)], "x")
    assert "class='tab'" not in html and "<section class='league-panel'" not in html


def test_league_labels_are_escaped_in_tabs():
    bundles = [WaiverBundle(label="<b>x</b>", scoring="ppr", week=9),
               WaiverBundle(label="y", scoring="ppr", week=9)]
    html = build_waivers_html(9, bundles, "x")
    assert "<b>x</b>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html


# --- the folded writers' board --------------------------------------------
def test_board_shows_where_writers_agree_and_what_the_tool_did():
    b = bundle_with_board(rows=40)
    for row in b.expert_rows[:12]:
        row.listed_by = 2
    b.expert_rows[30].tool = "also consider"
    visible = board_visible_keys(b.expert_rows)
    assert visible == {r.player.key for r in b.expert_rows[:12]} | {b.expert_rows[30].player.key}

    html = build_waivers_html(9, [b], "x")
    assert "minor" not in _row_classes(html, "D&#x27;Andre 12")
    assert "minor" in _row_classes(html, "D&#x27;Andre 13")
    assert "minor" not in _row_classes(html, "D&#x27;Andre 31")
    # Every row is still in the page, one click away.
    assert all(f"D&#x27;Andre {i}</td>" in html for i in range(1, 41))
    assert "Showing 13 of 40" in html and "Show all 40 players" in html


def test_board_shows_at_least_the_minimum_when_few_writers_agree():
    b = bundle_with_board(rows=25)
    assert len(board_visible_keys(b.expert_rows)) == BOARD_MIN_ROWS


def test_a_short_board_has_no_toggle():
    html = build_waivers_html(9, [bundle_with_board(rows=3)], "x")
    assert "class='more'" not in html and "Show all" not in html
    assert " minor" not in html and "class='minor'" not in html


def test_quotes_for_folded_rows_fold_with_them():
    b = bundle_with_board(rows=20)
    b.expert_rows[0].quotes = [ColumnMention("Justin Boone", "https://y/a", "1", "Top add.")]
    b.expert_rows[15].quotes = [ColumnMention("Justin Boone", "https://y/b", "16", "Deep add.")]
    html = build_waivers_html(9, [b], "x")
    assert "<h4>Why, in their words</h4>" in html
    assert "<blockquote class='quote'><strong>D&#x27;Andre 1</strong>" in html
    assert "<blockquote class='quote minor'><strong>D&#x27;Andre 16</strong>" in html
