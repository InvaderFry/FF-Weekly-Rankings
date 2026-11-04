"""The waivers page's "Your moves" card and its folded Data status.

``WaiverBundle.moves`` restates the sections below it; it must never say
anything those sections don't, and the page must put it (and the claims) above
the research tables that used to open each league.
"""

from ff_startsit.data_status import DataStatus, finish_status
from ff_startsit.models import Player, PlayerScore
from ff_startsit.output.html import build_waivers_html
from ff_startsit.waivers.models import (ACQ_PRIORITY, ByeGap, LeagueRules,
                                        StashIdea, TradeIdea, WaiverBundle,
                                        WaiverTarget)


def _ps(key, name, pos):
    return PlayerScore(player=Player(key=key, name=name, team="KC", position=pos), final=60.0)


def _target(key, name, pos, drop=None, bid="", reasons=()):
    return WaiverTarget(score=_ps(key, name, pos), drop=drop, bid=bid, reasons=reasons)


def _bundle():
    b = WaiverBundle(label="work", scoring="half", week=9,
                     rules=LeagueRules(acquisition_type=ACQ_PRIORITY))
    b.adds = [_target("a1", "Jonah Coleman", "RB", drop=_ps("d1", "Zach Charbonnet", "RB"),
                      bid="worth your top waiver claim",
                      reasons=("scores 17.1 above Zach Charbonnet",
                               "named this week by Jamey Eisenberg, Justin Boone",
                               "rostered in 52.5% of leagues"))]
    b.alternates = [_target(f"x{i}", f"Alt {i}", "WR") for i in range(6)]
    b.held_back = ["Evan McPherson (K): CBS and Boone rank Jason Myers higher this week",
                   "Jake Bates (K): CBS and Boone rank Jason Myers higher this week",
                   "Malik Willis (QB): no trade chart values him"]
    b.trades = [TradeIdea(partner="Rival", you_send=(_ps("m", "My Rb3", "RB"),),
                          you_get=(_ps("t", "Their Wr3", "WR"),), your_gain=5, their_gain=4)] * 2
    b.stashes = [StashIdea(score=_ps("s", "Carnell Tate", "WR"), reason="OUT")]
    b.byes = [ByeGap(week=11, position="K", available=0, needed=1)]
    return b


def test_moves_restate_each_section_in_plain_sentences():
    moves = {m.action: m.detail for m in _bundle().moves()}
    assert moves["Claim Jonah Coleman (RB), drop Zach Charbonnet."] == (
        "Worth your top waiver claim. Named this week by Jamey Eisenberg, Justin Boone.")
    assert "Alt 0 (WR), Alt 1 (WR), Alt 2 (WR), Alt 3 (WR) and 2 more." == \
        moves["Also worth adding if you free a spot:"]
    assert moves["Trade idea with Rival:"] == "send My Rb3, get Their Wr3. 1 more in Trade ideas."
    assert moves["Stash:"] == "Carnell Tate (WR)."
    assert moves["Bye-week hole:"] == "Week 11 leaves you 0 healthy Ks for 1 slot."


def test_held_back_lines_sharing_a_reason_collapse_to_one_sentence():
    held = [m.detail for m in _bundle().moves() if m.kind == "held_back"]
    assert held == [
        "Evan McPherson (K), Jake Bates (K) — CBS and Boone rank Jason Myers higher this week.",
        "Malik Willis (QB) — no trade chart values him.",
    ]


def test_moves_order_follows_what_a_reader_acts_on():
    actions = [m.action for m in _bundle().moves()]
    assert actions[0].startswith("Claim ")
    assert actions.index("Also worth adding if you free a spot:") < actions.index("Held back:")
    assert actions.index("Held back:") < actions.index("Trade idea with Rival:")
    assert actions[-1] == "Bye-week hole:"


def test_an_empty_wire_says_why_with_the_shared_reason():
    b = WaiverBundle(label="work", scoring="ppr", week=9)
    b.pool_size, b.coverage = 150, {"ecr": 120}
    moves = b.moves()
    assert [tuple(m) for m in moves] == [("claim", "No claims this week.", b.no_adds_reason())]


def test_a_preseason_refusal_has_no_card():
    b = WaiverBundle(label="work", scoring="ppr", week=1)
    b.banner = "PRESEASON: nothing to score yet."
    assert b.moves() == []
    assert "Your moves" not in build_waivers_html(1, [b], "2026-08-26")


def test_the_card_and_claims_come_before_the_research_tables():
    b = _bundle()
    from types import SimpleNamespace
    # Board on, with no rows: renders its heading and its own note.
    b.expert_boards = [SimpleNamespace(short="Boone", header="Boone WW", label="Boone's column",
                                       unavailable={}, source_id="boone")]
    html = build_waivers_html(9, [b], "2026-11-04")
    order = [html.index(s) for s in ("Your moves", "<h3>Claims</h3>",
                                     "Also consider adding", "<h3>Trade ideas</h3>",
                                     "What the writers say")]
    assert order == sorted(order)
    assert "Claim Jonah Coleman (RB), drop Zach Charbonnet." in html


def test_data_status_folds_details_but_never_a_skipped_league():
    b = WaiverBundle(label="ok", scoring="ppr", week=9)
    b.pool_size, b.coverage = 150, {"ecr": 120}
    status = DataStatus(2026, 9, ["ok", "<gone>"], skipped={"<gone>": "cookies expired"})
    finish_status(status, [b])
    html = build_waivers_html(9, [b], "2026-11-04")
    section = html[html.index("<section class='status'>"):html.index("</section>")]
    visible, folded = section.split("<details", 1)
    assert "Season 2026 · Week 9" in visible
    assert "1 of 2 included — INCOMPLETE" in visible
    assert "Skipped &lt;gone&gt;: cookies expired" in visible
    assert "ok: ecr covers 120/150 free agents" in folded
    assert "not a provider update time" in folded.split("</details>")[1]
