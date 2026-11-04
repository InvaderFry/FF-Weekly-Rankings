"""Discord's waiver cards lead with "Your moves" and link each league's tab,
and the writers' board sends only the rows the page shows first."""

from ff_startsit.output.discord import (build_waiver_board_payloads,
                                        build_waiver_payload)

from .test_boards_writers import bundle_with_board
from .test_waiver_moves import _bundle

URL = "https://x.github.io/FF/waivers.html"


def _league(label):
    b = _bundle()
    b.label = label
    return b


def test_each_card_opens_with_moves_and_links_its_own_tab():
    payload = build_waiver_payload(9, [_league("workTG"), _league("AndyLOT")], dashboard_url=URL)
    first, last = payload["embeds"]
    assert first["description"].startswith(
        "1. **Claim Jonah Coleman (RB), drop Zach Charbonnet.** Worth your top waiver claim.")
    assert first["url"] == f"{URL}#workTG"
    # The last card also carries the "Full dashboard" field, but its title
    # still opens its own league.
    assert last["url"] == f"{URL}#AndyLOT"
    assert any(f["name"] == "Full dashboard" and f["value"] == URL for f in last["fields"])


def test_a_single_league_links_the_page():
    embed = build_waiver_payload(9, [_league("solo")], dashboard_url=URL)["embeds"][0]
    assert embed["url"] == URL


def test_moves_replace_the_old_fields_but_trades_keep_their_own():
    embed = build_waiver_payload(9, [_league("work")])["embeds"][0]
    desc = embed["description"]
    for line in ("**Also worth adding if you free a spot:**", "**Held back:**",
                 "**Stash:** Carnell Tate (WR).", "**Bye-week hole:**"):
        assert line in desc
    names = [f["name"] for f in embed["fields"]]
    assert not any(n.startswith(("➕", "✋", "🗓️")) for n in names)
    # Every trade idea is in the trade field, so the moves' trade line is not repeated.
    assert "Trade idea with" not in desc
    assert any(n.startswith("🤝") for n in names)


def test_the_board_message_sends_lead_rows_and_points_to_the_rest():
    b = bundle_with_board(40)
    b.label = "workTG"
    payloads = build_waiver_board_payloads(9, [b, bundle_with_board(2)], dashboard_url=URL)
    first = payloads[0]["embeds"][0]
    fields = [f for e in payloads[0]["embeds"] for f in e["fields"]]
    assert len(fields) == 10
    assert first["description"].startswith(f"30 more players the writers named are [on the page]({URL}#workTG).")
    assert first["url"] == f"{URL}#workTG"


def test_an_acted_on_row_deep_in_the_list_still_goes_out_with_its_number():
    b = bundle_with_board(40)
    b.expert_rows[30].tool = "also consider"
    fields = [f for e in build_waiver_board_payloads(9, [b])[0]["embeds"] for f in e["fields"]]
    assert fields[-1]["name"].startswith("31. ")
    assert "on the page." in build_waiver_board_payloads(9, [b])[0]["embeds"][0]["description"]
