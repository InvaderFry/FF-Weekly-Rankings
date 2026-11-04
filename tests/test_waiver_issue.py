"""The waiver issue: the digest's page-style layout, and the short update a
later run posts in place of a full re-post.

The digest is the issue body and the only weekly archive (the site keeps just
the current week), so nothing may be dropped from it, only folded; and two
things must stay readable by the workflows: the Data status line the issue is
titled from, and the markers the retry gates read.
"""

import re

from ff_startsit.data_status import DataStatus, finish_status
from ff_startsit.waivers.render import render_waiver_digest
from ff_startsit.workflow import issue_title

from .test_boards_writers import bundle_with_board
from .test_waiver_moves import _bundle


def _digest(rows=14, skipped=None):
    b = _bundle()
    wb = bundle_with_board(rows)
    b.expert_boards, b.expert_rows = wb.expert_boards, wb.expert_rows
    status = DataStatus(2026, 9, ["work"] + list(skipped or {}), skipped=dict(skipped or {}))
    finish_status(status, [b])
    return render_waiver_digest(9, [b], generated_on="2026-11-04")


def test_the_digest_opens_each_league_with_moves_then_decisions_then_research():
    md = _digest()
    order = [md.index(s) for s in ("**Your moves**", "**Claims**", "**Also consider adding**",
                                   "### Trade ideas", "### What the writers say")]
    assert order == sorted(order)
    assert "1. **Claim Jonah Coleman (RB), drop Zach Charbonnet.**" in md


def test_the_writers_board_folds_its_tail_but_keeps_every_row():
    md = _digest(rows=14)
    visible, folded = md.split("<details><summary>The other 4 players the writers named</summary>")
    assert "| D'Andre 10 |" in visible and "| D'Andre 11 |" not in visible
    assert all(f"| D'Andre {i} |" in folded for i in range(11, 15))
    assert "Showing 10 of 14" in visible


def test_a_short_board_has_no_fold():
    assert "<details><summary>The other" not in _digest(rows=3)


def test_data_status_folds_detail_but_keeps_title_line_and_skipped_leagues_visible():
    md = _digest(skipped={"gone": "cookies expired"})
    status = md[md.index("## Data status"):md.index("### ")]
    visible = status.split("<details>")[0]
    assert "- Season 2026 · Week 9 · Generated" in visible
    assert "1 of 2 included — INCOMPLETE" in visible
    assert "**Skipped gone: cookies expired**" in visible
    assert "Included: work" not in visible
    assert "- Included: work" in status.split("<details>")[1]
    assert "</details>" in status and "not a provider update time" in status
    # The workflow still titles the issue off the visible line.
    assert issue_title(md, "waiver wire") == "2026 Week 9 waiver wire"


def test_the_markers_stay_at_the_end_outside_any_fold():
    md = _digest()
    tail = md[md.rindex("</details>"):]
    assert re.search(r"<!-- ff-board season=\d+ week=9 mode=live read=", tail)
    assert re.search(r"<!-- ff-adds season=\d+ week=9 sig=\w+ -->", tail)


# --- the short update a later run posts ----------------------------------------
from ff_startsit.waivers.board import adds_changed, has_news, report_has_news  # noqa: E402
from ff_startsit.workflow import read_label, update_note  # noqa: E402


def _retry(md, extra="boone_weekly:K"):
    return md.replace("read=boone_waivers,richard_chart",
                      f"read={extra},boone_waivers,richard_chart")


def test_the_note_names_what_was_newly_read_and_says_claims_held():
    first = _digest()
    note = update_note(_retry(first), first, "https://x/waivers.html", "Update")
    assert note.startswith("**Update:**")
    assert "Newly read: Boone's weekly K ranks." in note
    assert "The claims are unchanged." in note and "Your moves" not in note
    assert "[Open the waivers page](https://x/waivers.html)" in note
    assert len(note) < 1000            # a note, not the report again


def test_changed_claims_bring_each_leagues_moves():
    first = _digest()
    changed = _retry(first).replace("sig=", "sig=ff")
    note = update_note(changed, first)
    assert "**The claims changed.** Your moves now:" in note
    assert "**work · HALF**" in note
    assert "1. **Claim Jonah Coleman (RB), drop Zach Charbonnet.**" in note


def test_the_note_keeps_the_retry_gates_working_after_the_body_is_replaced():
    """The workflow swaps the body for the new report and posts the note, so
    the gates read (new body + old comments + note) next time."""
    first = _digest()
    second = _retry(first)
    thread = second + "\n" + update_note(second, first)
    # Nothing newly read since: no news, and the claims match the latest marker.
    assert not report_has_news(second, thread)
    assert not adds_changed(second, thread)
    # The first run's reads survive in the note even though its body is gone.
    assert not has_news(["boone_waivers", "richard_chart"], thread, *_season_week(first))


def _season_week(md):
    m = re.search(r"ff-board season=(\d+) week=(\d+)", md)
    return int(m[1]), int(m[2])


def test_read_labels():
    assert read_label("cbs_consensus:DEF") == "CBS's weekly DEF consensus"
    assert read_label("eisenberg_waivers") == "Eisenberg's waiver column"
    assert read_label("something_new:QB") == "something_new:QB"


def test_the_update_note_command_reads_files(tmp_path, monkeypatch, capsys):
    import sys
    from ff_startsit import workflow
    first = _digest()
    (tmp_path / "now.md").write_text(_retry(first))
    (tmp_path / "before.md").write_text(first)
    monkeypatch.setattr(sys, "argv", ["workflow", "update-note", str(tmp_path / "now.md"),
                                      str(tmp_path / "before.md"), "https://x", "Wednesday update"])
    workflow.main()
    out = capsys.readouterr().out
    assert out.startswith("**Wednesday update:**") and "Newly read" in out
