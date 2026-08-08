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
