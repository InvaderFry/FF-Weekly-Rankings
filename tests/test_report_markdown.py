from ff_startsit import report
from ff_startsit.config import Settings
from ff_startsit.models import Player, PlayerScore, Recommendation
from ff_startsit.output.render import render_markdown


def _rec(*scores, close_call=False, notes=None):
    return Recommendation(week=3, scoring="ppr", weights={"ecr": 0.75, "vegas": 0.25},
                          scores=list(scores), close_call=close_call, notes=notes or [])


def _ps(key, name, pos, final, team="KC"):
    ps = PlayerScore(player=Player(key=key, name=name, team=team, position=pos))
    ps.final = final
    ps.normalized = {"ecr": final}
    return ps


def test_render_markdown_table_and_start_line():
    rec = _rec(_ps("1", "Alpha", "RB", 90.0), _ps("2", "Bravo", "RB", 10.0))
    md = render_markdown(rec, title="RB")
    assert "### RB" in md
    assert "| # | Player | Pos | Team | Score | ECR | Flags |" in md
    assert "| 1 | Alpha | RB | KC | 90.0 |" in md
    assert "✅ **Start:** Alpha" in md


def test_render_markdown_close_call_blockquote():
    rec = _rec(_ps("1", "Alpha", "WR", 51.0), _ps("2", "Bravo", "WR", 49.0),
               close_call=True, notes=["Too close to call."])
    md = render_markdown(rec)
    assert "> ⚠️ **Close call**" in md
    assert "> - Too close to call." in md


def test_build_lineup_fills_slots_without_reuse():
    by_pos = {
        "QB": [_ps("qb1", "QB One", "QB", 80)],
        "RB": [_ps("rb1", "RB One", "RB", 90), _ps("rb2", "RB Two", "RB", 70),
               _ps("rb3", "RB Three", "RB", 60)],
        "WR": [_ps("wr1", "WR One", "WR", 85), _ps("wr2", "WR Two", "WR", 65)],
        "TE": [_ps("te1", "TE One", "TE", 50)],
    }
    lineup = report.build_lineup(by_pos)
    slots = {slot: (pick.player.key if pick else None) for slot, pick in lineup}
    # FLEX should take the best leftover skill player (RB Three at 60 beats nothing else left).
    assert slots["QB"] == "qb1"
    assert lineup[1][1].player.key == "rb1" and lineup[2][1].player.key == "rb2"
    # No key is used twice.
    used = [pick.player.key for _, pick in lineup if pick]
    assert len(used) == len(set(used))


def test_render_digest_from_precomputed_recs():
    recs = {
        "QB": _rec(_ps("q", "Quincy", "QB", 88.0, team="BUF")),
        "RB": _rec(_ps("1", "Alpha", "RB", 90.0), _ps("2", "Bravo", "RB", 10.0)),
    }
    digest = report.render_digest(3, "ppr", recs)
    assert "# 🏈 Week 3 start/sit — PPR" in digest
    assert "## Suggested lineup" in digest
    assert "### QB" in digest and "### RB" in digest
    assert "Quincy" in digest and "Alpha" in digest


def test_build_digest_monkeypatched(monkeypatch):
    players = [
        Player("1", "Alpha", "KC", "RB"),
        Player("2", "Bravo", "CHI", "RB"),
        Player("3", "Quincy", "BUF", "QB"),
    ]

    def fake_recommend(settings, cands, week, command="", log=True, signals=None):
        scores = [_ps(p.key, p.name, p.position, 100 - i * 10, team=p.team)
                  for i, p in enumerate(cands)]
        return _rec(*scores)

    monkeypatch.setattr(report, "recommend", fake_recommend)
    digest = report.build_digest(Settings(), players, week=3)

    assert "# 🏈 Week 3 start/sit" in digest
    assert "## Suggested lineup" in digest
    assert "## Rankings by position" in digest
    assert "### QB" in digest and "### RB" in digest
    assert "Alpha" in digest and "Quincy" in digest
