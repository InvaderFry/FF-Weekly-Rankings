from ff_startsit.models import Player, PlayerScore, Recommendation
from ff_startsit.output.discord import (build_discord_payload,
                                        build_multi_discord_payload, send_discord)
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


def test_build_payload_has_embed_with_lineup_and_url():
    qb = _ps("1", "Quincy", "QB", 88.0)
    payload = build_discord_payload(3, "ppr", [("QB", qb)], {"QB": _rec(qb)},
                                    dashboard_url="https://example.test/site")
    embed = payload["embeds"][0]
    assert "Week 3 start/sit" in embed["title"]
    assert "Quincy" in embed["description"]
    assert embed["url"] == "https://example.test/site"
    # Dashboard link and an alerts field are present.
    names = [f["name"] for f in embed["fields"]]
    assert "⚠️ Alerts" in names
    assert any("https://example.test/site" in f["value"] for f in embed["fields"])


def test_build_payload_surfaces_starter_flags_and_close_calls():
    injured = _ps("1", "Hurt Guy", "RB", 5.0, flags=["injury: Out"])
    rb = _rec(injured, _ps("2", "Healthy", "RB", 80.0),
              close_call=True, notes=["Too close to call."])
    payload = build_discord_payload(3, "ppr", [("RB", injured)], {"RB": rb})
    alerts = next(f for f in payload["embeds"][0]["fields"] if f["name"] == "⚠️ Alerts")
    assert "Hurt Guy: injury: Out" in alerts["value"]
    assert "Too close to call." in alerts["value"]


def test_build_payload_no_alerts_message():
    qb = _ps("1", "Quincy", "QB", 88.0)
    payload = build_discord_payload(3, "ppr", [("QB", qb)], {"QB": _rec(qb)})
    alerts = next(f for f in payload["embeds"][0]["fields"] if f["name"] == "⚠️ Alerts")
    assert "All clear" in alerts["value"] or "all clear" in alerts["value"]


def test_build_payload_label_in_title():
    qb = _ps("1", "Quincy", "QB", 88.0)
    payload = build_discord_payload(3, "ppr", [("QB", qb)], {"QB": _rec(qb)},
                                    label="dynasty")
    assert "· dynasty" in payload["embeds"][0]["title"]


def test_build_multi_payload_one_embed_per_league():
    work = _ps("1", "AlphaWork", "RB", 90.0)
    dyno = _ps("2", "BravoDyno", "RB", 80.0)
    bundles = [
        LeagueBundle("work", "ppr", {"RB": _rec(work)}, [("RB", work)]),
        LeagueBundle("dynasty", "half", {"RB": _rec(dyno)}, [("RB", dyno)]),
    ]
    payload = build_multi_discord_payload(3, bundles,
                                          dashboard_url="https://example.test/site")
    embeds = payload["embeds"]
    assert len(embeds) == 2
    assert "work" in embeds[0]["title"] and "dynasty" in embeds[1]["title"]
    # The dashboard link rides only the last embed to keep the message scannable.
    assert "url" not in embeds[0]
    assert embeds[1]["url"] == "https://example.test/site"


def test_send_discord_posts_json_payload():
    sent = {}

    class FakeResp:
        def raise_for_status(self):
            sent["raised"] = True

    class FakeSession:
        def post(self, url, json=None, timeout=None):
            sent["url"] = url
            sent["json"] = json
            sent["timeout"] = timeout
            return FakeResp()

    payload = {"embeds": [{"title": "hi"}]}
    send_discord("https://discord.test/webhook", payload, session=FakeSession())
    assert sent["url"] == "https://discord.test/webhook"
    assert sent["json"] == payload
    assert sent["raised"] is True


def test_embed_surfaces_the_lineup_caveat():
    """How the FLEX pick was decided has to reach Discord readers too.

    Otherwise they see an incomparable FLEX score, or a template fallback, with
    nothing saying so -- while every other renderer shows the caveat.
    """
    from ff_startsit.report import build_lineup

    by_pos = {
        "RB": [_ps("rb1", "RB One", "RB", 90), _ps("rb2", "RB Two", "RB", 40)],
        "WR": [_ps("wr1", "WR One", "WR", 80), _ps("wr2", "WR Two", "WR", 30)],
        "TE": [_ps("te1", "TE One", "TE", 70), _ps("te2", "TE Two", "TE", 20)],
    }
    lineup = build_lineup(by_pos)                       # positional fallback
    assert lineup.caveat
    payload = build_discord_payload(3, "ppr", lineup, {}, None)
    assert "standard-template" in payload["embeds"][0]["description"]


def _bundle(i, lineup_len=1):
    ps = _ps(str(i), f"Player {i}", "RB", 90.0)
    return LeagueBundle(label=f"League {i}", scoring="ppr",
                        recs={"RB": _rec(ps)}, lineup=[("RB", ps)] * lineup_len)


def _embed_chars(embed):
    total = len(embed.get("title") or "") + len(embed.get("description") or "")
    total += len((embed.get("footer") or {}).get("text") or "")
    for f in embed.get("fields") or []:
        total += len(f.get("name") or "") + len(f.get("value") or "")
    return total


def test_multi_payload_caps_embeds_at_discord_limit():
    """Discord refuses a payload with more than 10 embeds — the whole message."""
    payload = build_multi_discord_payload(3, [_bundle(i) for i in range(14)])
    assert len(payload["embeds"]) == 10


def test_multi_payload_says_when_leagues_were_dropped():
    payload = build_multi_discord_payload(3, [_bundle(i) for i in range(14)])
    last_fields = {f["name"]: f["value"] for f in payload["embeds"][-1]["fields"]}
    assert "4 more league(s)" in last_fields["⚠️ Not shown"]


def test_multi_payload_stays_under_the_total_character_budget():
    """Ten embeds can breach the 6000-char message limit before the embed cap."""
    bundles = [_bundle(i, lineup_len=200) for i in range(10)]
    payload = build_multi_discord_payload(3, bundles)
    assert sum(_embed_chars(e) for e in payload["embeds"]) <= 6000
    assert len(payload["embeds"]) >= 1


def test_multi_payload_trailer_rides_the_last_emitted_embed():
    """Dropping the tail must not drop the dashboard link and commands hint."""
    payload = build_multi_discord_payload(
        3, [_bundle(i) for i in range(14)],
        dashboard_url="https://example.test/site", commands_url="https://example.test/issue")
    embeds = payload["embeds"]
    last_fields = {f["name"]: f["value"] for f in embeds[-1]["fields"]}
    assert last_fields["Full dashboard"] == "https://example.test/site"
    assert "https://example.test/issue" in last_fields["💬 Commands"]
    assert embeds[-1]["url"] == "https://example.test/site"
    # ...and only on the last one.
    assert all("Full dashboard" not in [f["name"] for f in e["fields"]] for e in embeds[:-1])


def test_multi_payload_under_the_cap_is_unchanged():
    payload = build_multi_discord_payload(3, [_bundle(i) for i in range(3)])
    assert len(payload["embeds"]) == 3
    assert all("⚠️ Not shown" not in [f["name"] for f in e["fields"]]
               for e in payload["embeds"])


def test_multi_payload_fits_the_budget_even_with_pathological_content():
    """The per-field caps individually sum past the 6000-char message limit.

    Long names, many flags, a long label and a long dashboard URL together
    breach it, so the budget has to be enforced on the assembled message rather
    than assumed from the parts.
    """
    ps = _ps("1", "N" * 300, "RB", 90.0, flags=["F" * 300] * 50)
    rec = _rec(ps, close_call=True, notes=["N" * 500] * 20)
    bundles = [LeagueBundle(label="L" * 5000, scoring="ppr", recs={"RB": rec},
                            lineup=[("RB", ps)] * 400, banner="B" * 400)
               for _ in range(10)]

    payload = build_multi_discord_payload(
        3, bundles, dashboard_url="https://example.test/" + "u" * 5000,
        commands_url="https://example.test/" + "c" * 5000)

    embeds = payload["embeds"]
    assert 1 <= len(embeds) <= 10
    assert sum(_embed_chars(e) for e in embeds) <= 6000
    assert all(len(e["title"]) <= 256 for e in embeds)


def test_single_league_payload_title_is_capped():
    qb = _ps("1", "Quincy", "QB", 88.0)
    payload = build_discord_payload(3, "ppr", [("QB", qb)], {"QB": _rec(qb)},
                                    label="L" * 500)
    assert len(payload["embeds"][0]["title"]) <= 256
