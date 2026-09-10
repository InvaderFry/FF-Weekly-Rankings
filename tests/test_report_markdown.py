from ff_startsit import report
from ff_startsit.config import Settings
from ff_startsit.models import Player, PlayerScore, Recommendation
from ff_startsit.output.render import LINEUP_UNSCORED_NOTE, render_markdown


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


def test_lineup_table_blanks_a_fabricated_midpoint_score():
    """A lone TE candidate normalizes to the neutral midpoint (50.0) with
    nothing real behind it -- `to_0_100` has no other value to compare it to.
    The lineup table used to print that 50.0 as though it were a real score,
    directly contradicting the position section right below it, which says no
    signal could be scored. Issue #40: 10 of 27 lineup rows read exactly
    `50.0` this way."""
    recs = {
        "TE": _rec(_ps("te1", "Lone Tight End", "TE", 50.0)),
        "RB": _rec(_ps("rb1", "Alpha", "RB", 90.0), _ps("rb2", "Bravo", "RB", 10.0)),
    }
    digest = report.render_digest(3, "ppr", recs)
    assert "| TE | Lone Tight End | KC | — |" in digest
    assert "| TE | Lone Tight End | KC | 50.0 |" not in digest
    assert LINEUP_UNSCORED_NOTE in digest


def test_lineup_table_keeps_a_real_score_for_a_multi_candidate_slot():
    """A slot with a real ranking behind it must keep printing its number --
    the fix only blanks fabricated midpoints, not every score."""
    recs = {"RB": _rec(_ps("rb1", "Alpha", "RB", 90.0), _ps("rb2", "Bravo", "RB", 10.0))}
    digest = report.render_digest(3, "ppr", recs)
    assert "| RB | Alpha | KC | 90.0 |" in digest
    assert LINEUP_UNSCORED_NOTE not in digest


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

    def fake_recommend(settings, cands, week, command="", log=True, signals=None,
                       **kwargs):
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


def test_digest_and_lineup_agree_on_flex(monkeypatch):
    """One run must not report two different FLEX picks.

    render_digest rebuilds a positional lineup when it isn't handed one, so a
    caller that computed a pooled lineup and forgot to pass it emitted the
    pooled pick to the dashboard and a different, positionally-chosen pick to
    the markdown digest -- with a "FLEX ranking was unavailable" caveat that was
    not true of that run.
    """
    recs = {
        "RB": _rec(_fp("rb1", 100.0), _fp("rb2", 50.0), _fp("rb3", 0.0)),
        "WR": _rec(_fp("wr1", 100.0), _fp("wr2", 50.0), _fp("wr3", 0.0)),
    }
    pool = _pool(["wr1", "rb1", "wr2", "rb2", "wr3", "rb3"])   # leftover WR wins
    ws = report.WeekScores(recs=recs, flex=_rec(*pool), flex_note=None)
    lineup = report.lineup_from(ws)
    assert dict(lineup)["FLEX"].player.key == "wr3"

    digest = report.render_digest(3, "ppr", ws.recs, lineup=lineup)
    assert "Third Wideout" in digest              # the pooled pick
    assert "standard-template" not in digest      # and no false caveat


def test_markdown_table_escapes_pipes_and_newlines_in_names():
    """An unescaped `|` opens a phantom column; a newline ends the table."""
    import re

    a = PlayerScore(player=Player(key="1", name="Bad | Name", team="KC", position="RB"))
    a.final, a.normalized = 90.0, {"ecr": 100.0}
    a.flags = ["injury: Q | doubtful"]
    b = PlayerScore(player=Player(key="2", name="Line\nBreak", team="SF", position="RB"))
    b.final, b.normalized = 10.0, {"ecr": 0.0}
    rec = Recommendation(week=3, scoring="ppr", weights={"ecr": 1.0}, scores=[a, b])

    md = render_markdown(rec)
    rows = [ln for ln in md.splitlines() if ln.startswith("|")]
    # Header + separator + one row per player, all with the same column count.
    assert len(rows) == 4
    widths = {len(re.findall(r"(?<!\\)\|", ln)) for ln in rows}
    assert len(widths) == 1, f"ragged table: {rows}"
    assert r"Bad \| Name" in md
    assert "Line Break" in md


def test_a_super_flex_slot_accepts_a_quarterback_and_an_ordinary_flex_does_not():
    by_pos = {
        "QB": [_ps("qb1", "QB One", "QB", 95), _ps("qb2", "QB Two", "QB", 90)],
        "RB": [_ps("rb1", "RB One", "RB", 70), _ps("rb2", "RB Two", "RB", 40)],
    }
    sflex = dict(report.build_lineup(by_pos, slots=["QB", "RB", "SUPER_FLEX"]).slots)
    assert sflex["SUPER_FLEX"].player.key == "qb2", "the best body left is the QB"

    # The plain FLEX is RB/WR/TE, so the spare quarterback is not eligible for it.
    flex = dict(report.build_lineup(by_pos, slots=["QB", "RB", "FLEX"]).slots)
    assert flex["FLEX"].player.key == "rb2"


def test_the_pooled_flex_ranking_never_fills_a_super_flex_slot():
    """`flex_pool` is FantasyPros' cross-position RB/WR/TE list, which does not
    rank quarterbacks. Drawing a superflex pick from it would answer with the best
    player on a list its best candidate isn't on."""
    by_pos = {
        "QB": [_ps("qb1", "QB One", "QB", 95), _ps("qb2", "QB Two", "QB", 90)],
        "RB": [_ps("rb1", "RB One", "RB", 70), _ps("rb2", "RB Two", "RB", 40)],
    }
    pool = [_ps("rb2", "RB Two", "RB", 88)]     # pooled scores are their own scale
    lineup = report.build_lineup(by_pos, flex_pool=pool,
                                 slots=["QB", "RB", "SUPER_FLEX"])
    assert dict(lineup.slots)["SUPER_FLEX"].player.key == "qb2"


def test_build_lineup_honors_a_leagues_own_slots():
    """`slots` exists so the waiver pass can protect what the *league* starts.

    Left on the hardcoded template, drop protection reserved one quarterback while
    `droppable` counted surplus against the league's two — so a superflex starter
    was simultaneously protected by neither guard and surplus to the other, which
    is a recommendation to cut a starter.
    """
    by_pos = {
        "QB": [_ps("qb1", "QB One", "QB", 90), _ps("qb2", "QB Two", "QB", 80)],
        "RB": [_ps("rb1", "RB One", "RB", 70)],
        "WR": [_ps("wr1", "WR One", "WR", 60)],
    }
    superflex = report.build_lineup(by_pos, slots=["QB", "QB", "RB", "WR"])
    started = {pick.player.key for _, pick in superflex if pick}
    assert "qb2" in started, "the second quarterback is a starter here"

    # Default call is unchanged — one QB, and the spare stays on the bench.
    assert "qb2" not in {pick.player.key
                         for _, pick in report.build_lineup(by_pos) if pick}


# --- the whole-roster pass opts in to holding out unavailable players -------

class _FakeECR:
    name = "ecr"
    higher_is_better = False
    is_sample = False
    served_wrong_week = False

    def __init__(self, ranks):
        self.ranks = ranks

    def is_available(self):
        return True

    def fetch(self, week, players):
        from ff_startsit.models import SignalValue
        return {p.key: (SignalValue(self.ranks[p.key]) if p.key in self.ranks
                        else SignalValue(None, available=False, note="no ECR rank"))
                for p in players}


class _FakeInjury:
    name = "injury"
    higher_is_better = True
    is_sample = False
    served_wrong_week = False

    def __init__(self, scores):
        self.scores = scores

    def is_available(self):
        return True

    def fetch(self, week, players):
        from ff_startsit.models import SignalValue
        return {p.key: SignalValue(self.scores.get(p.key, 100.0)) for p in players}

    def rules_out(self, value):
        return value.available and value.raw == 0.0


def test_rank_each_position_holds_out_a_player_who_cannot_play(tmp_path):
    """Pins the `publish`/dashboard call site, which is the one a scheduled run
    uses — the engine being capable of this is not the same as it being wired."""
    from ff_startsit.config import Settings
    from ff_startsit.models import Player
    from ff_startsit.report import rank_each_position

    settings = Settings(data_dir=tmp_path,
                        weights={"ecr": 0.60, "injury": 0.12})
    players = [
        Player("1", "Healthy Back", "KC", "RB"),
        Player("2", "IR Back", "DET", "RB"),
    ]
    signals = [_FakeECR({"1": 4.0}), _FakeInjury({"1": 100.0, "2": 0.0})]

    recs = rank_each_position(settings, players, week=1, log=False,
                              signals=signals)
    by_key = {s.player.key: s for s in recs["RB"].scores}

    assert by_key["2"].final is None
    assert any("not startable" in f for f in by_key["2"].flags)
    assert by_key["1"].final is not None
    assert recs["RB"].scores[0].player.key == "1"


def test_rank_each_position_flags_the_last_starting_spot_not_just_the_top_two(tmp_path):
    """F5(b): workTG's real Week 1 decision (Nabers 45.5 Q vs Burden 42.9 Q)
    sat at the WR2/WR3 boundary, not the top two, and the old top-two-only
    check never saw it. `report.STARTER_COUNTS["WR"]` is 2, so the pair that
    should trip here is rank 2 vs rank 3 -- not rank 1 vs rank 2, which must
    stay a clean, unflagged gap or this test proves nothing."""
    from ff_startsit.config import Settings
    from ff_startsit.models import Player
    from ff_startsit.report import rank_each_position

    settings = Settings(data_dir=tmp_path, weights={"ecr": 1.0},
                        close_call_threshold=1.0)
    players = [Player("1", "WR1", "KC", "WR"), Player("2", "WR2", "KC", "WR"),
              Player("3", "WR3", "KC", "WR"), Player("4", "WR4", "KC", "WR")]
    # Rank 1 is far clear; ranks 2 and 3 are essentially tied; rank 4 trails.
    signals = [_FakeECR({"1": 1.0, "2": 20.0, "3": 20.1, "4": 90.0})]

    rec = rank_each_position(settings, players, week=1, log=False,
                             signals=signals)["WR"]

    assert rec.close_call is True
    assert any("Last starting spot" in n for n in rec.notes)
    assert not any("Too close to call" in n for n in rec.notes)


# --- a lone candidate is not a ranking -------------------------------------

def test_unranked_is_about_what_could_be_compared():
    from ff_startsit.models import PlayerScore
    lone = _rec(_ps("1", "Only Tight End", "TE", 50.0))
    assert lone.unranked is True
    assert _rec(_ps("1", "Alpha", "RB", 90.0),
                _ps("2", "Bravo", "RB", 10.0)).unranked is False
    # An unscored second body is not a comparison either.
    unscored = PlayerScore(player=Player("2", "IR Guy", "KC", "TE"))
    assert _rec(_ps("1", "Only Tight End", "TE", 50.0), unscored).unranked is True


def test_a_lone_candidate_collapses_to_one_sentence():
    """`to_0_100` returns the midpoint for an empty range, so the shipped Week 1
    tables read `ECR 50 | INJURY 50 | VEGAS 50 | WEATHER 50` for a player whose
    real ECR may have been TE1 — four placeholders shaped like readings.

    Blanking those columns left a header, an empty table, a note explaining the
    empty table and a verdict that was never in doubt: nine of the eighteen
    sections in a three-league digest, none of them carrying a reading. The whole
    section is now the one sentence that was ever in it.
    """
    lone = _rec(_ps("1", "Only Tight End", "TE", 50.0))
    md = render_markdown(lone, title="TE")

    assert "|" not in md                      # no table at all
    assert "50" not in md                     # and no placeholder in any form
    # The pick still stands: he is the only option and has to be startable.
    assert "✅ **Start:** Only Tight End" in md
    assert "only TE" in md                    # and says why nothing was ranked


def test_a_lone_candidate_keeps_its_flags():
    """The flags are the one real reading on a lone candidate — an injury
    designation or a weather warning survives the collapse."""
    score = _ps("1", "Only Tight End", "TE", 50.0)
    score.flags.append("injury: Questionable")
    md = render_markdown(_rec(score), title="TE")
    assert "injury: Questionable" in md


def test_a_lone_scorer_beside_a_ruled_out_body_keeps_its_table():
    """``unranked`` is true here too, but the ruled-out row is exactly what a
    roster owner needs to see — so this table must not collapse."""
    from ff_startsit.models import PlayerScore
    out = PlayerScore(player=Player("2", "IR Guy", "KC", "TE"))
    out.flags.append("not startable: ruled out this week")
    md = render_markdown(_rec(_ps("1", "Only Tight End", "TE", 50.0), out), title="TE")
    assert "IR Guy" in md and "not startable" in md
    assert "| 1 | Only Tight End" in md        # the table survives
    assert "| 50 |" not in md                  # still no placeholder columns


def test_a_real_ranking_still_shows_its_signal_columns():
    md = render_markdown(_rec(_ps("1", "Alpha", "RB", 90.0),
                              _ps("2", "Bravo", "RB", 10.0)), title="RB")
    assert "| 90 |" in md and "| 10 |" in md


# --- a 0-100 column can be drawn from almost nothing ------------------------

def _blend_with(vegas_raws, gaps=None):
    """Blend a candidate set with real raw Vegas values, so the raw scale exists."""
    from ff_startsit.engine.blend import blend
    from ff_startsit.models import SignalValue
    players = [Player(str(i), f"P{i}", "KC", "RB") for i in range(len(vegas_raws))]
    values = {
        "vegas": {p.key: SignalValue(raw=v, available=True)
                  for p, v in zip(players, vegas_raws)},
        "ecr": {p.key: SignalValue(raw=float(i + 1) * 5, available=True)
                for i, p in enumerate(players)},
    }
    return blend(week=1, scoring="ppr", players=players, signal_values=values,
                 higher_is_better={"vegas": True, "ecr": False},
                 weights={"vegas": 0.4, "ecr": 0.6}, close_call_threshold=5.0,
                 close_call_raw_gaps=gaps if gaps is not None
                 else {"ecr": 3.0, "vegas": 1.5})


def test_fmt_raw_rounds_and_strips_trailing_noise():
    """`:g` defaults to 6 significant figures, which is how a Vegas gap of
    1.222222 ended up printed verbatim in a close-call note (issue #40)."""
    from ff_startsit.models import _fmt_raw

    assert _fmt_raw(2.0) == "2"
    assert _fmt_raw(1.222222) == "1.22"
    assert _fmt_raw(0.5) == "0.5"
    assert _fmt_raw(100.0) == "100"
    assert _fmt_raw(0.0) == "0"


def test_a_tiny_raw_spread_is_called_out_under_the_table():
    """`to_0_100` is min-max within the candidate set with no minimum-span floor,
    so the best candidate is 100 and the worst is 0 however little separates
    them. Week 1 put the #1 back at `VEGAS 0` in two leagues; nothing in the
    table could say whether that was a real fade or half an implied point."""
    rec = _blend_with([22.0, 21.8, 21.6, 21.5])   # spans 0.5, under the 1.5 gap
    md = render_markdown(rec, title="RB")

    # The column really does read as a blowout...
    assert "| 100 |" in md and "| 0 |" in md
    # ...so the note has to say what it was drawn from.
    assert "vegas spans 0.5" in md
    assert "too small to rank on" in md


def test_a_real_raw_spread_is_left_alone():
    """The note must stay rare enough to mean something — a genuine six-point
    spread in implied totals is exactly the edge the column should show."""
    rec = _blend_with([27.0, 24.0, 22.0, 21.0])
    md = render_markdown(rec, title="RB")
    assert "vegas spans" not in md
    assert "Read with care" not in md


def _blend_with_weather(weather_raws, gaps=None):
    """Blend a candidate set with real raw weather values, so the raw scale
    weather now carries (PR2's fix) exists to test against."""
    from ff_startsit.engine.blend import blend
    from ff_startsit.models import SignalValue
    players = [Player(str(i), f"P{i}", "KC", "WR") for i in range(len(weather_raws))]
    values = {
        "weather": {p.key: SignalValue(raw=v, available=True)
                    for p, v in zip(players, weather_raws)},
        "ecr": {p.key: SignalValue(raw=float(i + 1) * 5, available=True)
                for i, p in enumerate(players)},
    }
    return blend(week=1, scoring="ppr", players=players, signal_values=values,
                higher_is_better={"weather": True, "ecr": False},
                weights={"weather": 0.10, "ecr": 0.60}, close_call_threshold=5.0,
                close_call_raw_gaps=gaps if gaps is not None
                else {"ecr": 3.0, "weather": 12.0})


def test_weather_flat_spread_is_called_out_once_it_carries_a_raw_gap():
    """Before PR2, weather had no configured raw gap at all, so `flat_signals`
    always skipped it -- the "read with care" note built for exactly this case
    never fired for the column that needed it most: Week 1's AndyLOT WR set
    spanned 93.7-100 raw weather and rendered as a 0-100 blowout with no
    warning."""
    rec = _blend_with_weather([93.7, 95.5, 95.2])   # spans 1.8, under the 12 gap
    assert [name for name, _, _ in rec.flat_signals()] == ["weather"]


def test_weather_flat_note_does_not_fire_on_a_real_spread():
    rec = _blend_with_weather([50.0, 70.0, 95.0])   # spans 45, well past the 12 gap
    assert rec.flat_signals() == []


def test_bucketed_signals_abstain_from_the_flat_check():
    """A signal absent from the configured gaps (injury is the shipped
    example — a bucketed status with no continuous scale) carries no
    configured gap and can neither raise nor suppress the note — the same
    abstention it makes in `blend._flag_raw_dead_heat`."""
    rec = _blend_with([22.0, 21.8], gaps={"vegas": 1.5})
    assert [name for name, _, _ in rec.flat_signals()] == ["vegas"]
    no_gaps = _blend_with([22.0, 21.8], gaps={})
    assert no_gaps.flat_signals() == []


def test_one_reading_is_not_a_spread():
    """With a single usable raw value, `max - min` is 0 — which would sail under
    any gap and announce a dead heat drawn from one number. A signal that read
    only one of the candidates has measured no spread at all."""
    from ff_startsit.engine.blend import blend
    from ff_startsit.models import SignalValue
    players = [Player("1", "Has Vegas", "KC", "RB"), Player("2", "On Bye", "", "RB")]
    values = {
        "vegas": {"1": SignalValue(raw=22.0, available=True),
                  "2": SignalValue(raw=None, available=False, note="bye")},
        "ecr": {"1": SignalValue(raw=5.0, available=True),
                "2": SignalValue(raw=40.0, available=True)},
    }
    rec = blend(week=1, scoring="ppr", players=players, signal_values=values,
                higher_is_better={"vegas": True, "ecr": False},
                weights={"vegas": 0.4, "ecr": 0.6}, close_call_threshold=5.0,
                close_call_raw_gaps={"vegas": 1.5})
    assert rec.flat_signals() == []
    assert "vegas spans" not in render_markdown(rec, title="RB")


def test_starter_counts_derive_from_the_slot_list_it_is_given():
    """The count and the lineup must not be able to disagree about the league.

    Hardcoding the counts beside `LINEUP_SLOTS` is the same shape of guard that
    `waivers.build._lineup_keys` had to learn the hard way -- one half reading a
    template while the other read the league's real slots. Here a 3-WR league's
    boundary is WR3/WR4, and a count derived from the slots it is handed says so.
    """
    from ff_startsit.report import LINEUP_SLOTS, STARTER_COUNTS, starter_counts

    assert starter_counts() == STARTER_COUNTS == {"QB": 1, "RB": 2, "WR": 2,
                                                  "TE": 1, "K": 1, "DEF": 1}
    three_wr = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "K", "DEF"]
    assert starter_counts(three_wr)["WR"] == 3
    # Flex slots have no position to count against and stay out of the mapping,
    # the same way they stay out of `LeagueRules.roster_slots`.
    assert "FLEX" not in starter_counts(LINEUP_SLOTS + ["FLEX", "SUPER_FLEX"])


def test_rank_each_position_takes_the_boundary_from_the_leagues_slots(tmp_path):
    """A 3-WR league's lineup decision is WR3/WR4, and the check must follow it.

    Same four receivers either way: under the default 2-WR template the tied
    pair sits at the boundary and flags; told the league starts three, the
    boundary moves past them and the same data must go quiet.
    """
    from ff_startsit.config import Settings
    from ff_startsit.models import Player
    from ff_startsit.report import rank_each_position

    settings = Settings(data_dir=tmp_path, weights={"ecr": 1.0},
                        close_call_threshold=1.0)
    players = [Player("1", "WR1", "KC", "WR"), Player("2", "WR2", "KC", "WR"),
               Player("3", "WR3", "KC", "WR"), Player("4", "WR4", "KC", "WR")]
    ranks = {"1": 1.0, "2": 20.0, "3": 20.1, "4": 90.0}

    default = rank_each_position(settings, players, week=1, log=False,
                                 signals=[_FakeECR(dict(ranks))])["WR"]
    assert any("Last starting spot" in n for n in default.notes)

    three_wr = rank_each_position(
        settings, players, week=1, log=False, signals=[_FakeECR(dict(ranks))],
        slots=["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "K", "DEF"])["WR"]
    assert not any("Last starting spot" in n for n in three_wr.notes)


def test_analyst_markdown_warning_note_and_silence():
    from ff_startsit.engine.analyst import AnalystConflict
    from dataclasses import replace
    rec = _rec(_ps('1', 'Alpha', 'RB', 90), _ps('2', 'Bravo', 'RB', 10))
    assert 'Justin Boone' not in render_markdown(rec)
    conflict = AnalystConflict('Justin Boone', 'RB', 'Alpha', 'Bravo', 19, 6, False, True)
    rec.analyst_conflicts = [conflict]
    assert '> ⚠️ Justin Boone disagrees: he starts Bravo (his RB6) over Alpha (his RB19).' in render_markdown(rec)
    rec.analyst_conflicts = [replace(conflict, leader_rank=8, material=False)]
    md = render_markdown(rec)
    assert 'within 2 spots (Alpha RB8, Bravo RB6), edge to Bravo.' in md
    assert '> ⚠️ Justin Boone' not in md
    rec.analyst_conflicts = [replace(conflict, boundary=True)]
    assert 'would flip your last starting spot: Bravo (his RB6)' in render_markdown(rec)


def test_analyst_unposted_note_renders_quietly_and_escapes():
    rec = _rec(_ps('1', 'Alpha', 'RB', 90), _ps('2', 'Bravo', 'RB', 10))
    assert 'not posted' not in render_markdown(rec)
    rec.analyst_note = 'Justin Boone has not posted his Week 1 full-PPR RB rankings yet.'
    md = render_markdown(rec)
    assert '_Justin Boone has not posted his Week 1 full-PPR RB rankings yet._' in md
    assert '> ⚠️' not in md
