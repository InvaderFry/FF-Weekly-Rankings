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


def _journalist_view():
    from ff_startsit.sources.journalists import (Expert, JournalistRow,
                                                 JournalistView)
    boone, eisen = Expert("101", "Justin Boone"), Expert("102", "Jamey Eisenberg")
    alpha = Player("1", "Alpha", "KC", "RB")
    bravo = Player("2", "Bravo", "CHI", "RB")
    return JournalistView(
        experts=[boone, eisen],
        by_position={"RB": [
            JournalistRow(alpha, 3.0, {"101": 2.0, "102": 4.0}),
            JournalistRow(bravo, 8.0, {"101": 8.0, "102": None}),
        ]})


def test_render_digest_with_journalists_section():
    recs = {"RB": _rec(_ps("1", "Alpha", "RB", 90.0))}
    digest = report.render_digest(3, "ppr", recs, journalists=_journalist_view())
    assert "## Preferred journalists" in digest
    assert "Justin Boone, Jamey Eisenberg" in digest
    assert "| # | Player | Team | Avg rank | Justin Boone | Jamey Eisenberg |" in digest
    assert "| 1 | Alpha | KC | 3.0 | 2 | 4 |" in digest
    assert "| 2 | Bravo | CHI | 8.0 | 8 | — |" in digest  # missing rank -> em dash
    # Section renders after the position rankings.
    assert digest.index("## Rankings by position") < digest.index("## Preferred journalists")


def test_render_digest_without_journalists_omits_section():
    recs = {"RB": _rec(_ps("1", "Alpha", "RB", 90.0))}
    digest = report.render_digest(3, "ppr", recs)
    assert "Preferred journalists" not in digest


def test_render_digest_label_in_heading():
    recs = {"RB": _rec(_ps("1", "Alpha", "RB", 90.0))}
    digest = report.render_digest(3, "ppr", recs, label="dynasty")
    assert "# 🏈 Week 3 start/sit — PPR · dynasty" in digest


def test_render_multi_digest_section_per_league():
    work = {"RB": _rec(_ps("1", "AlphaWork", "RB", 90.0))}
    dyno = {"RB": _rec(_ps("2", "BravoDyno", "RB", 80.0))}
    bundles = [
        report.LeagueBundle("work", "ppr", work, report.build_lineup(report.scored(work))),
        report.LeagueBundle("dynasty", "half", dyno, report.build_lineup(report.scored(dyno))),
    ]
    digest = report.render_multi_digest(3, bundles)
    assert "# 🏈 Week 3 start/sit" in digest
    assert "2 league(s)" in digest
    assert "## work — PPR" in digest and "## dynasty — HALF" in digest
    assert "AlphaWork" in digest and "BravoDyno" in digest


def test_flex_tie_break_is_deterministic():
    """FLEX_POSITIONS used to be a set, so ties broke differently per process.

    Ties are the norm here, not the exception: per-position normalization puts
    every position's leader at 100.
    """
    from ff_startsit.models import Player, PlayerScore
    from ff_startsit.report import build_lineup

    def _score(key, name, pos):
        s = PlayerScore(player=Player(key=key, name=name, team="KC", position=pos))
        s.final = 100.0                      # deliberate three-way tie
        return s

    # Enough depth that every flex-eligible position still has a leftover once
    # RB/RB/WR/WR/TE are filled -- otherwise there is no tie to break.
    by_pos = {
        "RB": [_score(f"r{i}", f"Rb {i}", "RB") for i in range(1, 4)],
        "WR": [_score(f"w{i}", f"Wr {i}", "WR") for i in range(1, 4)],
        "TE": [_score(f"t{i}", f"Te {i}", "TE") for i in range(1, 3)],
    }
    picks = dict(build_lineup(by_pos))
    # Leftovers are r3, w3 and t2, all tied at 100.0; the fixed RB -> WR -> TE
    # precedence resolves it the same way on every run.
    assert picks["FLEX"].player.key == "r3"


# --- pooled FLEX ---------------------------------------------------------
# 3 RBs and 3 WRs: RB/RB and WR/WR consume two of each, so one of each is left
# over for FLEX. That leftover pair is the whole problem -- positionally they
# are both "third best at my position", which says nothing about which is the
# better real option.
_FLEX_PLAYERS = {
    "rb1": ("Patrick Runner", "KC", "RB"),
    "rb2": ("Chicago Back", "CHI", "RB"),
    "rb3": ("Backup Back", "NYG", "RB"),
    "wr1": ("Elite Wideout", "CIN", "WR"),
    "wr2": ("Solid Wideout", "MIA", "WR"),
    "wr3": ("Third Wideout", "NE", "WR"),
}


def _fp(key, final):
    name, team, pos = _FLEX_PLAYERS[key]
    return _ps(key, name, pos, final, team=team)


def _flex_by_pos():
    return {
        "RB": [_fp("rb1", 100.0), _fp("rb2", 50.0), _fp("rb3", 0.0)],
        "WR": [_fp("wr1", 100.0), _fp("wr2", 50.0), _fp("wr3", 0.0)],
    }


def _pool(order):
    """A scored pooled ranking in the given order, best first."""
    return [_fp(key, 100.0 - i * 10) for i, key in enumerate(order)]


def test_flex_follows_the_pooled_ranking_not_positional_scores():
    """The regression test for the cross-position comparison bug.

    rb3 and wr3 both normalize to 0.0 within their own position groups, so the
    positional path cannot tell them apart and falls back to RB-before-WR. The
    pooled ranking can, and FLEX must follow it -- in either direction.
    """
    by_pos = _flex_by_pos()
    naive = dict(report.build_lineup(by_pos))
    assert naive["FLEX"].player.key == "rb3"     # decided by tie-break, not value

    # Pool says the leftover WR is the better option: FLEX changes.
    lineup = report.build_lineup(by_pos, flex_pool=_pool(["wr1", "rb1", "wr2", "rb2", "wr3", "rb3"]))
    assert lineup.flex_basis == "pooled"
    assert dict(lineup)["FLEX"].player.key == "wr3"

    # Pool says the leftover RB is better: FLEX follows that too.
    lineup_rb = report.build_lineup(by_pos, flex_pool=_pool(["wr1", "rb1", "wr2", "rb2", "rb3", "wr3"]))
    assert dict(lineup_rb)["FLEX"].player.key == "rb3"


def test_pooled_flex_never_reuses_a_started_player():
    by_pos = _flex_by_pos()
    # Pool ranks already-started players first; FLEX has to skip past them.
    picks = dict(report.build_lineup(
        by_pos, flex_pool=_pool(["rb1", "wr1", "rb2", "wr2", "wr3", "rb3"])))
    assert picks["FLEX"].player.key == "wr3"
    used = [p.player.key for p in picks.values() if p]
    assert len(used) == len(set(used))


def test_positional_fallback_carries_a_visible_caveat():
    lineup = report.build_lineup(_flex_by_pos())          # no pool supplied
    assert lineup.flex_basis == "positional"
    assert "standard-template" in lineup.caveat
    # ...and it reaches the reader, not just the object.
    assert "standard-template" in report.render_digest(3, "ppr", {}, lineup=lineup)


def test_pooled_lineup_notes_the_score_is_not_comparable():
    """Selection is fixed, but the displayed FLEX score is still another frame."""
    lineup = report.build_lineup(
        _flex_by_pos(), flex_pool=_pool(["wr1", "rb1", "wr2", "rb2", "wr3", "rb3"]))
    assert "not comparable" in lineup.caveat
    assert "not comparable" in report.render_digest(3, "ppr", {}, lineup=lineup)


def test_lineup_is_sequence_shaped_for_existing_renderers():
    """Renderers and older tests iterate/index the lineup directly."""
    lineup = report.build_lineup(_flex_by_pos())
    assert len(lineup) == len(report.LINEUP_SLOTS)
    assert lineup[0][0] == "QB"
    assert [slot for slot, _ in lineup] == report.LINEUP_SLOTS
