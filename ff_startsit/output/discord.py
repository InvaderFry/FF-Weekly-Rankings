"""Discord webhook delivery of the weekly start/sit summary.

Builds a concise embed — suggested lineup + any alerts (injury flags on your
starters, close-call positions) + a link to the full dashboard — and POSTs it to
a Discord incoming webhook. The full per-position detail lives on the dashboard;
the notification is the at-a-glance nudge.

Payload-building is pure and separated from the HTTP POST so it can be tested
offline against an injected session, matching the rest of the codebase.
"""

from __future__ import annotations

from typing import Optional, Sequence

import requests

from ..models import PlayerScore, Recommendation

# Discord limits we stay safely under.
_FIELD_VALUE_MAX = 1024
_EMBED_COLOR = 0x2EA043  # green


def _lineup_lines(lineup: Sequence[tuple[str, Optional[PlayerScore]]]) -> str:
    lines: list[str] = []
    for slot, pick in lineup:
        if pick is None:
            lines.append(f"**{slot}** — _(no option)_")
        else:
            team = pick.player.team or "BYE"
            lines.append(f"**{slot}** {pick.player.name} ({team}) — {pick.final:.1f}")
    return "\n".join(lines)


def _alerts(lineup: Sequence[tuple[str, Optional[PlayerScore]]],
            recs: dict[str, Recommendation]) -> list[str]:
    """Flags on your starters first, then close-call positions."""
    alerts: list[str] = []
    for _slot, pick in lineup:
        if pick is not None and pick.flags:
            alerts.append(f"{pick.player.name}: {'; '.join(pick.flags)}")
    for pos, rec in recs.items():
        if rec.close_call:
            for note in rec.notes:
                alerts.append(f"[{pos}] {note}")
    return alerts


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_discord_payload(week: int, scoring: str,
                          lineup: Sequence[tuple[str, Optional[PlayerScore]]],
                          recs: dict[str, Recommendation],
                          dashboard_url: Optional[str] = None) -> dict:
    """Return a Discord webhook JSON body for the week's summary."""
    description = _clip(_lineup_lines(lineup), 4096)
    embed: dict = {
        "title": f"🏈 Week {week} start/sit — {scoring.upper()}",
        "description": description,
        "color": _EMBED_COLOR,
        "fields": [],
    }
    if dashboard_url:
        embed["url"] = dashboard_url

    alerts = _alerts(lineup, recs)
    if alerts:
        value = _clip("\n".join(f"• {a}" for a in alerts), _FIELD_VALUE_MAX)
    else:
        value = "None — all clear 🎉"
    embed["fields"].append({"name": "⚠️ Alerts", "value": value, "inline": False})

    if dashboard_url:
        embed["fields"].append(
            {"name": "Full dashboard", "value": dashboard_url, "inline": False}
        )

    return {"embeds": [embed]}


def send_discord(webhook_url: str, payload: dict,
                 session: Optional[requests.Session] = None, timeout: int = 20) -> None:
    """POST the payload to a Discord incoming webhook."""
    sess = session or requests.Session()
    resp = sess.post(webhook_url, json=payload, timeout=timeout)
    resp.raise_for_status()
