"""The start/sit issue: a later pass (Saturday, Sunday) replaces the body and
comments only which starters changed, read off a lineup marker in the digest."""

import sys

from ff_startsit import workflow
from ff_startsit.models import Player, PlayerScore
from ff_startsit.report import LeagueBundle, Lineup, lineup_markers, render_multi_digest
from ff_startsit.workflow import lineup_changes, startsit_update_note


def _ps(name, pos):
    return PlayerScore(Player(name.lower().replace(" ", ""), name, "KC", pos), final=70.0)


def _bundle(label, flex="Kyren Williams", qb="D'Andre <Q> Swift"):
    slots = [("QB", _ps(qb, "QB")), ("RB", _ps("Jonathan Taylor", "RB")),
             ("RB", _ps("Kenneth Walker III", "RB")), ("FLEX", _ps(flex, "RB")),
             ("K", None)]
    return LeagueBundle(label, "ppr", {}, Lineup(slots))


def _digest(**kw):
    return render_multi_digest(9, [_bundle("workTG", **kw), _bundle("AndyLOT")])


def test_the_digest_carries_every_leagues_lineup_and_survives_odd_names():
    (season, week, data), = lineup_markers(_digest())
    assert week == 9
    assert data["workTG"][0] == ["QB", "D'Andre <Q> Swift"]
    assert data["workTG"][-1] == ["K", None]
    assert "-->" not in _digest().split("<!-- ff-lineup")[1].split("-->")[0]


def test_a_new_starter_is_named_with_his_slot_and_the_benched_one_too():
    note = startsit_update_note(_digest(flex="Breece Hall"), _digest(), "https://x/", "Update")
    assert "- **workTG:** start Breece Hall (FLEX); bench Kyren Williams." in note
    assert "AndyLOT" not in note                 # unchanged leagues stay quiet
    assert "[Open the start/sit page](https://x/)" in note
    assert len(note) < 800


def test_no_change_says_so():
    note = startsit_update_note(_digest(), _digest())
    assert "No lineup changes since the last post." in note


def test_moving_a_starter_between_slots_is_not_a_change():
    before = {"L": [["RB", "A"], ["FLEX", "B"]]}
    after = {"L": [["RB", "B"], ["FLEX", "A"]]}
    assert lineup_changes(before, after) == []


def test_a_league_that_dropped_out_or_appeared_is_named():
    lines = lineup_changes({"Gone": [["QB", "A"]]}, {"New": [["QB", "A"]]})
    assert lines == ["- **New:** new in this run.",
                     "- **Gone:** not in this run (see Data status)."]


def test_the_note_compares_against_the_latest_post_after_the_body_is_replaced():
    """Thursday's body, Saturday's note, then Sunday: the comparison is with
    Saturday's lineup (carried in its note), not Thursday's."""
    thursday = _digest()
    saturday = _digest(flex="Breece Hall")
    thread = saturday + "\n" + startsit_update_note(saturday, thursday)
    sunday = startsit_update_note(_digest(flex="Breece Hall"), thread)
    assert "No lineup changes since the last post." in sunday


def test_an_issue_from_before_the_marker_has_nothing_to_compare_with():
    assert "No earlier lineup in this issue" in startsit_update_note(_digest(), "# old digest")


def test_the_command_reads_files(tmp_path, monkeypatch, capsys):
    (tmp_path / "now.md").write_text(_digest(flex="Breece Hall"))
    (tmp_path / "before.md").write_text(_digest())
    monkeypatch.setattr(sys, "argv", ["workflow", "startsit-note", str(tmp_path / "now.md"),
                                      str(tmp_path / "before.md"), "https://x/"])
    workflow.main()
    assert "start Breece Hall (FLEX)" in capsys.readouterr().out
