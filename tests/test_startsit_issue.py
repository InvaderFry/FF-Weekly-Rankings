"""The start/sit issue: a later pass (Saturday, Sunday) replaces the body and
comments only which starters changed, read off a lineup marker in the digest."""

import sys

from ff_startsit import workflow
from ff_startsit.models import Player, PlayerScore
from ff_startsit.report import (LeagueBundle, Lineup, lineup_changes, lineup_markers,
                                render_multi_digest)
from ff_startsit.workflow import startsit_update_note


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


# --- start/sit Discord stays quiet when no lineup moved ------------------------
import argparse  # noqa: E402

from ff_startsit import cli, report  # noqa: E402
from ff_startsit.models import Recommendation  # noqa: E402

from .test_cli import _multi_settings  # noqa: E402


def _publish(tmp_path, monkeypatch, names, since=None):
    """One `publish --all-leagues --discord` pass with each league's lone RB
    named from ``names``; returns the Discord payloads sent."""
    rosters = {"111": [Player("1", names[0], "KC", "RB")],
               "222": [Player("2", names[1], "BUF", "RB")]}
    monkeypatch.setattr(cli, "_get_roster",
                        lambda args, settings, profile=None: rosters[profile.league_id])
    monkeypatch.setattr(cli, "_resolve_week", lambda args, settings: 7)

    def fake_rank(settings, plyrs, week, log=False, signals=None, slots=None):
        ps = PlayerScore(player=plyrs[0])
        ps.final, ps.normalized = 90.0, {"ecr": 90.0}
        return {"RB": Recommendation(week=week, scoring=settings.scoring,
                                     weights={"ecr": 1.0}, scores=[ps])}

    monkeypatch.setattr(report, "rank_each_position", fake_rank)
    monkeypatch.setattr(report, "build_journalist_view", lambda settings, plyrs, week: None)
    sent = []
    monkeypatch.setattr("ff_startsit.output.discord.send_discord",
                        lambda url, payload, **kw: sent.append(payload))
    out = tmp_path / f"report-{len(list(tmp_path.iterdir()))}.md"
    args = argparse.Namespace(report=out, dashboard=None, discord=True, url=None,
                              all_leagues=True, since=since)
    assert cli.cmd_publish(args, _multi_settings(discord_webhook_url="https://d.test/h")) == 0
    return sent, out


def test_a_later_pass_with_the_same_lineups_sends_nothing(tmp_path, monkeypatch, capsys):
    _, first = _publish(tmp_path, monkeypatch, ["Alpha", "Bravo"])
    sent, _ = _publish(tmp_path, monkeypatch, ["Alpha", "Bravo"], since=first)
    assert sent == [] and "No lineup changes" in capsys.readouterr().err


def test_a_later_pass_that_moved_a_starter_says_so_first(tmp_path, monkeypatch):
    _, first = _publish(tmp_path, monkeypatch, ["Alpha", "Bravo"])
    sent, _ = _publish(tmp_path, monkeypatch, ["Alpha", "Charlie"], since=first)
    assert len(sent) == 1
    content = sent[0]["content"]
    assert content.startswith("**Update:** lineup changes since the last post:")
    assert "- **dynasty:** start Charlie (RB); bench Bravo." in content
    assert len(sent[0]["embeds"]) == 2          # the full summary still follows


def test_the_weeks_first_post_and_manual_runs_always_send(tmp_path, monkeypatch):
    sent, _ = _publish(tmp_path, monkeypatch, ["Alpha", "Bravo"],
                       since=tmp_path / "no-issue-yet.md")
    assert len(sent) == 1 and "content" not in sent[0]
    sent, _ = _publish(tmp_path, monkeypatch, ["Alpha", "Bravo"])   # no --since
    assert len(sent) == 1


def test_last_weeks_issue_is_not_compared(tmp_path, monkeypatch):
    _, first = _publish(tmp_path, monkeypatch, ["Alpha", "Bravo"])
    first.write_text(first.read_text().replace(" week=7 ", " week=6 "))
    sent, _ = _publish(tmp_path, monkeypatch, ["Alpha", "Bravo"], since=first)
    assert len(sent) == 1 and "content" not in sent[0]
