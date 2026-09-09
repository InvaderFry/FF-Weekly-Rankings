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
    assert any("Conditional drop candidates" in f["name"] for f in embed["fields"])


def test_discord_waiver_colour_differs_from_the_startsit_post():
    """Two scheduled messages land in the same channel each week; the colour is
    what separates them before a word is read."""
    from ff_startsit.output.discord import _EMBED_COLOR

    assert build_waiver_payload(9, [_bundle()])["embeds"][0]["color"] != _EMBED_COLOR


def test_many_leagues_stay_inside_discord_limits():
    bundles = [_bundle(f"league-{i}", adds=8, trades=5) for i in range(25)]
    # Every league also carrying the per-position "no add here" sentence, which
    # lands in the embed *description* and so competes for the same budget.
    for b in bundles:
        b.rules = LeagueRules(acquisition_type=ACQ_FAAB, faab_budget=100.0,
                              roster_slots={"QB": 1, "RB": 2, "WR": 2, "TE": 1},
                              team_count=12)
        b.considered_adds = {"QB": 7, "RB": 31, "TE": 12}
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


def test_a_cross_position_add_renders_without_a_margin():
    """No margin exists when the add and his drop play different positions — the
    two scores came from separate candidate sets. Every renderer has to say the
    drop's name without inventing a number beside it."""
    b = _bundle(adds=1, trades=0)
    b.adds[0].margin = None
    b.adds[0].drop = _ps("d0", "Bench Te", "TE", 50.0)
    b.adds[0].reasons = ("takes the roster spot from Bench Te (TE), "
                         "your most droppable player",)

    md = render_waiver_digest(9, [b])
    assert "Bench Te" in md
    assert "(+" not in md and "None" not in md

    html = build_waivers_html(9, [b], "2026-08-19")
    assert "Bench Te" in html
    assert "+None" not in html and "class='note'>+" not in html

    payload = build_waiver_payload(9, [b])
    assert "None" not in json.dumps(payload)


# --- empty vs. broken ------------------------------------------------------
# `score.has_ecr` gates adds *and* drops, so an ECR outage empties the adds list.
# All three renderers used to answer that with their own wording of "nothing beats
# anyone you could drop" — a confident claim about a comparison that never ran.
def _empty(**kw):
    b = WaiverBundle(label="work", scoring="ppr", week=9, **kw)
    return b


def _render_all(b):
    return (render_waiver_digest(9, [b]),
            build_waivers_html(9, [b], "2026-10-01"),
            json.dumps(build_waiver_payload(9, [b])))


def test_an_ecr_outage_says_outage_in_every_renderer():
    for out in _render_all(_empty(pool_size=12, coverage={"ecr": 0, "injury": 12})):
        assert "data outage" in out
        assert "beats anyone you could drop" not in out


def test_a_pool_that_never_reached_the_index_reads_as_the_same_outage():
    """`signal_coverage` returns {} when no pooled player is in the scoring index —
    the same failure seen one step earlier, not a quiet wire."""
    for out in _render_all(_empty(pool_size=12, coverage={})):
        assert "data outage" in out


def test_a_thin_read_says_how_thin_without_crying_outage():
    for out in _render_all(_empty(pool_size=12, coverage={"ecr": 3})):
        assert "beats anyone you could drop" in out
        assert "3 of 12" in out
        assert "outage" not in out


def test_a_genuinely_quiet_wire_reads_exactly_as_it_always_did():
    for out in _render_all(_empty(pool_size=12, coverage={"ecr": 12})):
        assert "Nothing on the wire beats anyone you could drop this week" in out
        assert "outage" not in out and "of 12" not in out


def test_a_standing_banner_still_suppresses_the_line_everywhere():
    """A preseason refusal must not discuss a comparison that never ran — the rule
    all three already followed, now in one place."""
    b = _empty(pool_size=12, coverage={"ecr": 0}, banner="PRESEASON — no live data.")
    for out in _render_all(b):
        assert "beats anyone you could drop" not in out
        assert "data outage" not in out
        assert "PRESEASON" in out


def test_the_drop_table_shows_the_key_it_is_sorted_by():
    """A table ordered by an invisible column reads as unsorted.

    Drops are ranked worst-rest-of-season first, which is also the criterion
    each row was selected on. The weekly blend score used to sit here instead,
    and a list running 61.7, 58.6, 72.0 looks like a bug — worse, those finals
    came from separate per-position normalizations, so reading down the column
    compared a quarterback to a running back and got a number for it.
    """
    def _drop(key, pos, final, ros, reason):
        score = PlayerScore(player=Player(key=key, name=f"Guy {key}", team="KC",
                                          position=pos), final=final)
        score.season_rank = ros
        return DropCandidate(score, reason)

    bundle = WaiverBundle(label="L", scoring="half", week=3, drops=[
        _drop("a", "RB", 61.7, 240, "RB depth"),
        _drop("b", "QB", 73.8, 180, "QB depth"),
    ])
    out = render_waiver_digest(3, [bundle])
    assert "| ROS rank |" in out
    assert "| Score |" not in out
    assert "240" in out and "180" in out
    assert "61.7" not in out and "73.8" not in out


def test_a_drop_without_a_season_rank_renders_an_em_dash():
    """Renderers must tolerate a missing rank rather than format None."""
    score = PlayerScore(player=Player(key="a", name="No Rank", team="KC",
                                      position="RB"), final=50.0)
    bundle = WaiverBundle(label="L", scoring="half", week=3,
                          drops=[DropCandidate(score, "RB depth")])
    assert "—" in render_waiver_digest(3, [bundle])


def test_league_notes_render_inside_their_own_league_section():
    """A note carrying one league's numbers must be attributable to that league.

    Pooled into the shared footer, three leagues' coverage lines became three
    near-identical unattributed sentences at the bottom of the page, split apart
    by the run-wide notes that happened to be interleaved with them.
    """
    a = _bundle(label="workTG")
    a.league_notes.append("Season-long ranks cover 270/278 players.")
    a.notes.append("Scores are normalized within each position's candidate set.")
    b = _bundle(label="AaronRun")
    b.league_notes.append("Season-long ranks cover 314/330 players.")
    b.notes.append("Scores are normalized within each position's candidate set.")

    out = render_waiver_digest(1, [a, b])
    footer = out.index("\n---\n")
    # Each league's own line sits above the footer, under its own heading...
    assert out.index("270/278") < out.index("## AaronRun") < out.index("314/330")
    assert out.index("314/330") < footer
    # ...and the note both leagues share is deduped into the footer, once.
    assert out.count("Scores are normalized") == 1
    assert out.index("Scores are normalized") > footer


def test_html_league_notes_render_inside_their_own_details_block():
    a = _bundle(label="workTG")
    a.league_notes.append("Season-long ranks cover 270/278 players.")
    b = _bundle(label="AaronRun")
    b.league_notes.append("Season-long ranks cover 314/330 players.")
    b.notes.append("Shared methodology note.")

    html = build_waivers_html(1, [a, b], "2026-09-08")
    # Each coverage line lands before its league's </details> closes...
    assert html.index("270/278") < html.index("AaronRun")
    assert html.index("314/330") < html.rindex("</details>")
    # ...and the run-wide note trails every league section.
    assert html.index("Shared methodology note") > html.rindex("</details>")


# --- no_trades_reason ------------------------------------------------------

def _no_trade_bundle(**rules_kw):
    """A bundle whose trade search ran and came back empty."""
    b = WaiverBundle(label="work", scoring="ppr", week=9,
                     rules=LeagueRules(team_count=12, **rules_kw))
    b.trades_considered = True
    return b


def test_no_trades_reason_names_the_superflex_refusal():
    """`suggest_trades` declines these leagues outright; the reader could not
    tell that from a section that simply wasn't there."""
    b = _no_trade_bundle(flex_slots={"SUPER_FLEX": 1})
    assert "superflex" in b.no_trades_reason()

    b = _no_trade_bundle(roster_slots={"QB": 2})
    assert "superflex" in b.no_trades_reason()


def test_no_trades_reason_distinguishes_an_outage_from_a_quiet_league():
    outage = WaiverBundle(label="w", scoring="ppr", week=9,
                          rules=LeagueRules(team_count=1))
    outage.trades_considered = True
    assert "data outage" in outage.no_trades_reason()

    quiet = _no_trade_bundle()
    reason = quiet.no_trades_reason()
    assert "data outage" not in reason
    assert "both starting lineups better" in reason


def test_no_trades_reason_is_silent_when_something_else_explains_it():
    # Never searched: --no-trades, FF_TRADE_SUGGESTIONS=0, or an unknown team
    # (which build_bundle already covers with a league_note).
    never = WaiverBundle(label="w", scoring="ppr", week=9)
    assert never.no_trades_reason() is None

    for field, value in (("banner", "preseason"), ("caveat", "ROS ranks are out")):
        b = _no_trade_bundle()
        setattr(b, field, value)
        assert b.no_trades_reason() is None, field


def test_league_rules_superflex_matches_what_suggest_trades_refuses():
    """One predicate, so the refusal and its explanation cannot disagree."""
    from ff_startsit.waivers.trades import suggest_trades
    from ff_startsit.waivers.models import FantasyTeam

    rules = LeagueRules(team_count=12, flex_slots={"SUPER_FLEX": 1})
    assert rules.superflex
    teams = [FantasyTeam(team_id="1", name="Mine", is_mine=True, players=[])]
    assert suggest_trades(teams, {}, rules) == []


def test_all_three_renderers_explain_an_empty_trade_section():
    b = _no_trade_bundle(flex_slots={"SUPER_FLEX": 1})
    expected = b.no_trades_reason()

    assert expected in render_waiver_digest(9, [b])
    assert expected in build_waivers_html(9, [b], "2026-09-09")
    payload = json.dumps(build_waiver_payload(9, [b]))
    assert "superflex" in payload


# --- a table of streamers still leaves the real positions unexplained -------

def _streamers_only_bundle():
    """The shipped Week 1 shape: adds exist, but only a kicker and a defense."""
    b = WaiverBundle(label="work", scoring="half", week=1,
                     rules=LeagueRules(roster_slots={"QB": 1, "RB": 2, "WR": 2,
                                                     "TE": 1, "K": 1, "DEF": 1},
                                       team_count=10))
    for key, name, pos in (("k1", "Some Kicker", "K"), ("d1", "Some D/ST", "DEF")):
        b.adds.append(WaiverTarget(score=_ps(key, name, pos, 84.0),
                                   drop=_ps(f"o{key}", f"Old {pos}", pos, 60.0),
                                   reasons=("takes the roster spot",)))
    b.pool_size = 200
    b.coverage = {"ecr": 180}
    return b


def test_a_streamer_only_table_says_what_happened_at_rb_and_wr():
    """``no_adds_reason`` only speaks when the table is *entirely* empty, so a
    report listing a kicker and a defense said nothing about the positions that
    decide a week — and a reader could not tell a bare wire from a bar set too
    high. All three renderers carry the same sentence."""
    b = _streamers_only_bundle()
    b.considered_adds = {"RB": 12, "WR": 8, "K": 3, "DEF": 4}

    reason = b.no_adds_at_positions()
    assert reason is not None
    for pos in ("QB", "RB", "WR", "TE"):
        assert pos in reason
    assert "12" in reason and "8" in reason          # what was actually weighed
    assert "K" not in reason.replace("Kicker", "")   # streamers are not news

    md = render_waiver_digest(1, [b])
    html = build_waivers_html(1, [b], "2026-09-09")
    embed = json.dumps(build_waiver_payload(1, [b]))
    for out in (md, html, embed):
        assert "beat anyone you could drop" in out


def test_it_separates_a_bare_wire_from_a_bar_set_too_high():
    """Two different silences: nobody at that position was even a candidate, or
    candidates were weighed and none won. They call for different responses."""
    weighed = _streamers_only_bundle()
    weighed.considered_adds = {"RB": 12, "WR": 8, "QB": 4, "TE": 3}
    assert "ranked and compared" in weighed.no_adds_at_positions()

    bare = _streamers_only_bundle()
    bare.considered_adds = {}
    text = bare.no_adds_at_positions()
    assert "No ranked free agent was available" in text
    assert "nothing there was compared" in text


def test_it_stays_quiet_when_something_else_already_explains_the_silence():
    """Same contract as ``no_adds_reason``: a banner or a caveat owns the
    explanation, and an entirely empty table belongs to ``no_adds_reason``."""
    b = _streamers_only_bundle()
    b.considered_adds = {"RB": 12}

    b.banner = "Preseason: nothing scored."
    assert b.no_adds_at_positions() is None
    b.banner = None
    b.caveat = "The free-agent pool was unreachable."
    assert b.no_adds_at_positions() is None
    b.caveat = None
    b.adds = []
    assert b.no_adds_at_positions() is None
    assert b.no_adds_reason() is not None


def test_it_stays_quiet_when_every_starting_position_produced_an_add():
    b = _streamers_only_bundle()
    b.rules = LeagueRules(roster_slots={"WR": 2, "K": 1}, team_count=10)
    b.adds = [WaiverTarget(score=_ps("f1", "A Wideout", "WR", 70.0))]
    b.considered_adds = {"WR": 9}
    assert b.no_adds_at_positions() is None
