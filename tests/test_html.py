from ff_startsit.models import Player, PlayerScore, Recommendation
from ff_startsit.output.html import build_dashboard_html, build_multi_dashboard_html
from ff_startsit.output.render import LINEUP_UNSCORED_NOTE
from ff_startsit.report import LeagueBundle


def _ps(key, name, pos, final, team="KC", flags=None):
    ps = PlayerScore(player=Player(key=key, name=name, team=team, position=pos))
    ps.final = final
    ps.normalized = {"ecr": final}
    ps.flags = flags or []
    return ps


def _rec(*scores, close_call=False, notes=None):
    return Recommendation(week=3, scoring="ppr", weights={"ecr": 1.0},
                          scores=list(scores), close_call=close_call, notes=notes or [])


def test_build_dashboard_html_is_complete_document():
    rb = _rec(_ps("1", "Alpha", "RB", 90.0), _ps("2", "Bravo", "RB", 10.0))
    lineup = [("RB", rb.scores[0]), ("K", None)]
    html = build_dashboard_html(3, "ppr", lineup, {"RB": rb}, generated_on="2026-06-24")

    assert html.startswith("<!doctype html>")
    assert "</html>" in html.strip()
    assert "Week 3 start/sit" in html
    assert "Suggested lineup" in html
    assert "Alpha" in html and "Bravo" in html
    # An empty slot is rendered, not crashed on.
    assert "(no option)" in html


def test_lineup_table_blanks_a_fabricated_midpoint_score():
    """Same defect as the markdown digest: a lone candidate's `final` is a
    fabricated 50.0 midpoint, and the dashboard used to print it as a real
    score."""
    lone = _rec(_ps("1", "Lone Tight End", "TE", 50.0))
    html = build_dashboard_html(3, "ppr", [("TE", lone.scores[0])], {"TE": lone},
                                generated_on="2026-06-24")
    assert ">—<" in html
    assert "50.0" not in html
    assert LINEUP_UNSCORED_NOTE in html


def test_lineup_table_keeps_a_real_score_for_a_multi_candidate_slot():
    rb = _rec(_ps("1", "Alpha", "RB", 90.0), _ps("2", "Bravo", "RB", 10.0))
    html = build_dashboard_html(3, "ppr", [("RB", rb.scores[0])], {"RB": rb},
                                generated_on="2026-06-24")
    assert ">90.0<" in html
    assert LINEUP_UNSCORED_NOTE not in html


def test_build_dashboard_html_flags_and_close_call():
    injured = _ps("3", "Hurt Guy", "WR", 5.0, flags=["injury: Out"])
    wr = _rec(_ps("1", "Alpha", "WR", 51.0), injured,
              close_call=True, notes=["Too close to call."])
    html = build_dashboard_html(3, "ppr", [("WR", wr.scores[0])], {"WR": wr},
                                generated_on="2026-06-24")

    assert "Close call" in html
    assert "Too close to call." in html
    # The injured player's flag row is highlighted.
    assert "injury: Out" in html
    assert "flagged" in html


def test_build_dashboard_html_escapes_player_names():
    rec = _rec(_ps("1", "A <script> Guy", "RB", 50.0))
    html = build_dashboard_html(3, "ppr", [("RB", rec.scores[0])], {"RB": rec},
                                generated_on="2026-06-24")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def _journalist_view():
    from ff_startsit.sources.journalists import (Expert, JournalistRow,
                                                 JournalistView)
    return JournalistView(
        experts=[Expert("101", "Justin Boone"), Expert("102", "Jamey Eisenberg")],
        by_position={"RB": [
            JournalistRow(Player("1", "Alpha", "KC", "RB"), 3.0,
                          {"101": 2.0, "102": 4.0}),
            JournalistRow(Player("2", "Bravo", "CHI", "RB"), 8.0,
                          {"101": 8.0, "102": None}),
        ]})


def test_build_dashboard_html_journalists_section():
    rb = _rec(_ps("1", "Alpha", "RB", 90.0))
    html = build_dashboard_html(3, "ppr", [("RB", rb.scores[0])], {"RB": rb},
                                generated_on="2026-06-24",
                                journalists=_journalist_view())
    assert "Preferred journalists" in html
    assert "Justin Boone" in html and "Jamey Eisenberg" in html
    assert "Avg rank" in html
    assert "3.0" in html      # Alpha's average
    assert "—" in html        # Bravo's missing Eisenberg rank
    assert html.index("Rankings by position") < html.index("Preferred journalists")


def test_build_dashboard_html_no_journalists_no_section():
    rb = _rec(_ps("1", "Alpha", "RB", 90.0))
    html = build_dashboard_html(3, "ppr", [("RB", rb.scores[0])], {"RB": rb},
                                generated_on="2026-06-24")
    assert "Preferred journalists" not in html


def test_build_dashboard_html_label_in_heading_and_title():
    rb = _rec(_ps("1", "Alpha", "RB", 90.0))
    html = build_dashboard_html(3, "ppr", [("RB", rb.scores[0])], {"RB": rb},
                                generated_on="2026-06-24", label="dynasty")
    assert "· dynasty" in html
    assert "<title>Week 3 start/sit · dynasty</title>" in html


def test_build_dashboard_html_no_label_unchanged():
    rb = _rec(_ps("1", "Alpha", "RB", 90.0))
    html = build_dashboard_html(3, "ppr", [("RB", rb.scores[0])], {"RB": rb},
                                generated_on="2026-06-24")
    assert "<title>Week 3 start/sit</title>" in html
    assert "start/sit — PPR</h1>" in html
    assert "Data status" in html


def test_build_multi_dashboard_html_one_section_per_league():
    work = _rec(_ps("1", "AlphaWork", "RB", 90.0))
    dyno = _rec(_ps("2", "BravoDyno", "RB", 80.0))
    bundles = [
        LeagueBundle("work", "ppr", {"RB": work}, [("RB", work.scores[0])]),
        LeagueBundle("dynasty", "half", {"RB": dyno}, [("RB", dyno.scores[0])]),
    ]
    html = build_multi_dashboard_html(3, bundles, generated_on="2026-06-24")

    assert html.startswith("<!doctype html>")
    assert html.count("<details class='league'") == 2
    assert "work — PPR" in html and "dynasty — HALF" in html
    assert "AlphaWork" in html and "BravoDyno" in html
    assert "2 league(s)" in html


def test_analyst_html_warning_note_silence_and_escaping():
    from dataclasses import replace
    from ff_startsit.engine.analyst import AnalystConflict
    from ff_startsit.output.html import _position_section
    from ff_startsit.models import Player, PlayerScore, Recommendation
    rec = Recommendation(1, 'ppr', {}, [
        PlayerScore(Player('a', 'Alpha', 'KC', 'RB'), final=90),
        PlayerScore(Player('b', 'Bravo', 'KC', 'RB'), final=10)])
    assert 'analyst' not in _position_section('RB', rec)
    conflict = AnalystConflict('Justin Boone', 'RB', 'Alpha', 'Bravo <script>', 19, 6, False, True)
    rec.analyst_conflicts = [conflict]
    html = _position_section('RB', rec)
    assert "class='callout analyst'" in html and 'Justin Boone disagrees' in html
    assert 'Bravo &lt;script&gt;' in html and '<script>' not in html
    rec.analyst_conflicts = [replace(conflict, preferred='Bravo', leader_rank=8, material=False)]
    html = _position_section('RB', rec)
    assert "class='analyst-note'" in html and 'within 2 spots' in html
    assert "class='callout analyst'" not in html
    assert html.index('analyst-note') > html.index('</table>')


def test_analyst_unposted_note_renders_below_the_table_and_escapes():
    from ff_startsit.output.html import _position_section
    from ff_startsit.models import Player, PlayerScore, Recommendation
    rec = Recommendation(1, 'ppr', {}, [
        PlayerScore(Player('a', 'Alpha', 'KC', 'RB'), final=90),
        PlayerScore(Player('b', 'Bravo', 'KC', 'RB'), final=10)])
    assert 'not posted' not in _position_section('RB', rec)
    rec.analyst_note = 'Boone <b> has not posted his Week 1 full-PPR RB rankings yet.'
    html = _position_section('RB', rec)
    assert "class='analyst-note'" in html and 'has not posted' in html
    assert '&lt;b&gt;' in html and '<b>' not in html
    assert "class='callout analyst'" not in html
    assert html.index('analyst-note') > html.index('</table>')
