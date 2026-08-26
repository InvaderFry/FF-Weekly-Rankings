"""Rendering the waiver bundle: markdown, HTML and Discord all from one object."""

import json
import re

from ff_startsit.models import Player, PlayerScore
from ff_startsit.output.discord import build_waiver_payload
from ff_startsit.output.html import build_waivers_html
from ff_startsit.waivers.models import (ACQ_FAAB, ByeGap, ColumnMention,
                                        DropCandidate, LeagueRules, StashIdea,
                                        TradeIdea, WaiverBundle, WaiverTarget)
from ff_startsit.waivers.render import render_waiver_digest

# Discord's own limits, restated here so the assertions don't move when the
# implementation's private constants do (same trick as test_discord.py).
MAX_EMBEDS = 10
MAX_CHARS = 6000


def _ps(key, name, pos, final):
    return PlayerScore(player=Player(key=key, name=name, team="KC", position=pos),
                       final=final)


def _bundle(label="work", adds=1, trades=1, week=9):
    b = WaiverBundle(label=label, scoring="ppr", week=week,
                     rules=LeagueRules(acquisition_type=ACQ_FAAB, faab_budget=100.0))
    for i in range(adds):
        b.adds.append(WaiverTarget(
            score=_ps(f"f{i}", f"Free Agent {i}", "WR", 70.0 - i),
            margin=20.0 - i, drop=_ps(f"d{i}", f"Bench Guy {i}", "WR", 50.0),
            bid="bid ~$14 (18% of your $78 left)",
            journalist_avg=21.5,
            mentions=(ColumnMention("Dave Richard", "https://cbs.test/x", f"f{i}",
                                    "He is the top add this week."),),
            reasons=("scores 20.0 above Bench Guy 0, your most droppable WR",
                     "preferred journalists average him 21.5")))
    b.drops.append(DropCandidate(score=_ps("d0", "Bench Guy 0", "WR", 50.0),
                                 reason="WR depth behind 3 you'd start"))
    for i in range(trades):
        b.trades.append(TradeIdea(partner=f"Rival {i}",
                                  you_send=(_ps("m1", "My Rb3", "RB", 60.0),),
                                  you_get=(_ps("t1", "Their Wr3", "WR", 62.0),),
                                  your_gain=9.4, their_gain=6.1,
                                  rationale="Both lineups get better this week."))
    b.stashes.append(StashIdea(score=_ps("s1", "Shelved Guy", "RB", 40.0),
                               reason="IR — stash while he's cheap"))
    b.byes.append(ByeGap(week=11, position="RB", available=1, needed=2))
    b.sources = [("Dave Richard", "https://cbs.test/x")]
    b.notes = ["Scores are normalized within each position's candidate set."]
    return b


# --- markdown -------------------------------------------------------------
def test_markdown_covers_every_section():
    md = render_waiver_digest(9, [_bundle()])
    for expected in ("Week 9 waiver wire", "Free Agent 0", "Bench Guy 0",
                     "Trade ideas", "Rival 0", "Stash watch", "Bye-week holes",
                     "What the writers said", "Sources"):
        assert expected in md


def test_markdown_escapes_pipes_so_a_name_cannot_break_the_table():
    b = _bundle()
    b.adds[0].score.player = Player("f0", "Odd | Name", "KC", "WR")
    md = render_waiver_digest(9, [b])
    row = [line for line in md.splitlines() if "Odd" in line][0]
    assert r"Odd \| Name" in row        # escaped, so it stays inside its cell
    # 6 columns => 7 unescaped delimiters, whatever the name contains.
    assert len(re.findall(r"(?<!\\)\|", row)) == 7


def test_markdown_says_so_when_nothing_is_worth_adding():
    b = _bundle(adds=0, trades=0)
    b.adds.clear()
    assert "Nothing on the wire" in render_waiver_digest(9, [b])


def test_a_caveat_is_surfaced_not_buried():
    b = _bundle()
    b.caveat = "No free-agent list was available for this league."
    assert "⚠️ No free-agent list" in render_waiver_digest(9, [b])


def test_multi_league_digest_lists_each_league_once():
    md = render_waiver_digest(9, [_bundle("work"), _bundle("dynasty")])
    assert md.count("## work") == 1 and md.count("## dynasty") == 1


def test_shared_notes_are_stated_once_across_leagues():
    md = render_waiver_digest(9, [_bundle("work"), _bundle("dynasty")])
    assert md.count("normalized within each position") == 1


# --- HTML -----------------------------------------------------------------
def test_html_is_a_complete_document_with_the_sections():
    html = build_waivers_html(9, [_bundle()], "2026-08-19")
    assert html.startswith("<!doctype html>") and html.rstrip().endswith("</html>")
    for expected in ("Free Agent 0", "Trade ideas", "Stash watch",
                     "What the writers said", "Rival 0"):
        assert expected in html


def test_html_escapes_player_names():
    b = _bundle()
    b.adds[0].score.player = Player("f0", "<script>alert(1)</script>", "KC", "WR")
    html = build_waivers_html(9, [b], "2026-08-19")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_html_links_the_two_pages_and_marks_the_current_one():
    """Both scheduled workflows publish the whole site, so both links resolve."""
    html = build_waivers_html(9, [_bundle()], "2026-08-19")
    assert "href='index.html'" in html
    assert "href='waivers.html' class='active'" in html


def test_empty_html_page_still_renders():
    assert "No configured league" in build_waivers_html(9, [], "2026-08-19")


# --- Discord --------------------------------------------------------------
def _embed_chars(embed):
    total = len(embed.get("title", "")) + len(embed.get("description", ""))
    for f in embed.get("fields", []):
        total += len(f.get("name", "")) + len(f.get("value", ""))
    return total


def test_discord_payload_has_the_adds_and_the_trades():
    payload = build_waiver_payload(9, [_bundle()], dashboard_url="https://x.test/waivers.html")
    embed = payload["embeds"][0]
    assert "Free Agent 0" in embed["description"]
    assert any("Trade ideas" in f["name"] for f in embed["fields"])
    assert any("Safe to cut" in f["name"] for f in embed["fields"])


def test_discord_waiver_colour_differs_from_the_startsit_post():
    """Two scheduled messages land in the same channel each week; the colour is
    what separates them before a word is read."""
    from ff_startsit.output.discord import _EMBED_COLOR

    assert build_waiver_payload(9, [_bundle()])["embeds"][0]["color"] != _EMBED_COLOR


def test_many_leagues_stay_inside_discord_limits():
    bundles = [_bundle(f"league-{i}", adds=8, trades=5) for i in range(25)]
    payload = build_waiver_payload(9, bundles, dashboard_url="https://x.test/w.html",
                                   commands_url="https://x.test/issues")
    embeds = payload["embeds"]
    assert 0 < len(embeds) <= MAX_EMBEDS
    assert sum(_embed_chars(e) for e in embeds) <= MAX_CHARS
    # The reader is told the list was cut rather than left to notice.
    assert any("Not shown" in f["name"] for f in embeds[-1]["fields"])
    json.dumps(payload)  # must stay serializable


def test_no_bundles_produces_an_empty_payload_not_a_crash():
    assert build_waiver_payload(9, []) == {"embeds": []}


def test_a_caveat_flips_the_embed_to_the_warning_colour():
    from ff_startsit.output.discord import _BANNER_COLOR

    b = _bundle()
    b.caveat = "No free-agent list was available."
    assert build_waiver_payload(9, [b])["embeds"][0]["color"] == _BANNER_COLOR


# --- the preseason banner, in all three renderers --------------------------
PRESEASON = ("⚠️ PRESEASON — the NFL season hasn't started, so there are no "
             "weekly rankings to score a waiver wire against.")


def _preseason_bundle():
    """What ``build_bundle`` returns before Week 1: the banner and nothing else."""
    b = WaiverBundle(label="work", scoring="ppr", week=1)
    b.banner = PRESEASON
    return b


def test_markdown_leads_with_the_banner():
    md = render_waiver_digest(1, [_preseason_bundle()])
    assert PRESEASON in md
    # The usual empty-wire line would claim a comparison that never ran.
    assert "Nothing on the wire" not in md


def test_html_renders_the_banner_as_a_callout():
    html = build_waivers_html(1, [_preseason_bundle()], generated_on="2026-08-26")
    assert "callout" in html and "PRESEASON" in html


def test_discord_leads_with_the_banner_and_claims_no_comparison():
    embed = build_waiver_payload(1, [_preseason_bundle()])["embeds"][0]
    assert PRESEASON in embed["description"]
    # The usual empty-adds line would claim a comparison that never ran.
    assert "No add beats anyone" not in embed["description"]


def test_the_banner_flips_the_embed_to_the_warning_colour():
    from ff_startsit.output.discord import _BANNER_COLOR

    embed = build_waiver_payload(1, [_preseason_bundle()])["embeds"][0]
    assert embed["color"] == _BANNER_COLOR


def test_the_rehearsal_coverage_reaches_discord_not_just_markdown():
    """Coverage rides in the banner rather than `notes` for exactly this reason:
    notes reach the digest and the dashboard, but never the embed — and the
    Discord message is the thing being rehearsed."""
    b = WaiverBundle(label="work", scoring="ppr", week=1)
    b.banner = "🧪 DRESS REHEARSAL — early look. Live coverage: ecr 61/143."
    b.notes = ["a note that Discord never renders"]

    assert "ecr 61/143" in render_waiver_digest(1, [b])
    assert "ecr 61/143" in build_waivers_html(1, [b], generated_on="2026-08-27")
    embed = build_waiver_payload(1, [b])["embeds"][0]
    assert "ecr 61/143" in embed["description"]
    assert "a note that Discord never renders" not in json.dumps(embed)


# --- the drafted roster under the preseason banner -------------------------
def _drafted_bundle():
    b = WaiverBundle(label="work", scoring="ppr", week=1)
    b.banner = PRESEASON
    b.roster = [Player("1", "Josh Allen", "BUF", "QB"),
                Player("2", "Bijan Robinson", "ATL", "RB"),
                Player("3", "CeeDee Lamb", "DAL", "WR"),
                Player("4", "Free Agent Wr", None, "WR")]
    return b


def test_the_roster_reaches_all_three_renderers():
    b = _drafted_bundle()
    md = render_waiver_digest(1, [b])
    html = build_waivers_html(1, [b], generated_on="2026-08-26")
    embed = build_waiver_payload(1, [b])["embeds"][0]

    for rendered in (md, html, json.dumps(embed)):
        assert "Your team (drafted)" in rendered
        assert "Josh Allen" in rendered and "Bijan Robinson" in rendered
    # A player with no NFL team (bye/FA) still renders, without an empty paren.
    assert "Free Agent Wr (None)" not in md


def test_the_roster_carries_no_scores_it_did_not_compute():
    """The run that shows a roster is the one that refused to score anything."""
    md = render_waiver_digest(1, [_drafted_bundle()])
    roster_block = md.split("Your team (drafted)")[1]
    assert "Score" not in roster_block and "100.0" not in roster_block


def test_the_banner_still_comes_first():
    md = render_waiver_digest(1, [_drafted_bundle()])
    assert md.index(PRESEASON) < md.index("Your team (drafted)")


def test_an_undrafted_league_shows_no_roster_section():
    b = WaiverBundle(label="work", scoring="ppr", week=1)
    b.banner = PRESEASON
    assert "Your team" not in render_waiver_digest(1, [b])
    assert "Your team" not in build_waivers_html(1, [b], generated_on="2026-08-26")
    assert "Your team" not in json.dumps(build_waiver_payload(1, [b])["embeds"][0])


def test_the_html_page_makes_the_same_empty_wire_claim_as_the_others():
    """All three renderers suppress the empty-wire line under a banner; the page
    used to keep it and claim a comparison that never ran."""
    b = WaiverBundle(label="work", scoring="ppr", week=1)
    b.banner = PRESEASON
    html = build_waivers_html(1, [b], generated_on="2026-08-26")
    assert "Nothing on the wire" not in html
    # ...but an ordinary quiet week still says so.
    quiet = WaiverBundle(label="work", scoring="ppr", week=9)
    assert "Nothing on the wire" in build_waivers_html(9, [quiet],
                                                       generated_on="2026-10-01")
