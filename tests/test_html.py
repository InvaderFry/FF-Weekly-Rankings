from ff_startsit.models import Player, PlayerScore, Recommendation
from ff_startsit.output.html import build_dashboard_html


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
