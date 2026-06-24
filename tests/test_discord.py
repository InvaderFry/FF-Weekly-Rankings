from ff_startsit.models import Player, PlayerScore, Recommendation
from ff_startsit.output.discord import build_discord_payload, send_discord


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
