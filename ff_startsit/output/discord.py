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
_BANNER_COLOR = 0xD29922  # amber — something needs the reader's attention

# The /commands only work as GitHub issue comments (chatops.py); Discord
# delivery is a one-way webhook, so tell readers where the commands live.
_COMMANDS_NOTE = ("`/lineup`, `/report`, `/rank RB`, `/compare A | B` work as "
                  "comments on the weekly GitHub issue — not here in Discord.")


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


def _build_embed(week: int, scoring: str,
                 lineup: Sequence[tuple[str, Optional[PlayerScore]]],
                 recs: dict[str, Recommendation],
                 dashboard_url: Optional[str] = None,
                 banner: Optional[str] = None,
                 label: str = "") -> dict:
    """Build one league's embed (title, lineup description, alerts field)."""
    label_suffix = f" · {label}" if label else ""
    description = _lineup_lines(lineup)
    if banner:
        description = f"**{banner}**\n\n{description}"
    embed: dict = {
        "title": f"🏈 Week {week} start/sit — {scoring.upper()}{label_suffix}",
        "description": _clip(description, 4096),
        "color": _BANNER_COLOR if banner else _EMBED_COLOR,
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
    return embed


def _commands_field(embed: dict, commands_url: Optional[str]) -> None:
    """Attach the '/commands live on GitHub' hint to an embed (field or footer)."""
    if commands_url:
        embed["fields"].append(
            {"name": "💬 Commands",
             "value": _clip(f"{_COMMANDS_NOTE}\n{commands_url}", _FIELD_VALUE_MAX),
             "inline": False}
        )
    else:
        embed["footer"] = {"text": _COMMANDS_NOTE.replace("`", "")}


def build_discord_payload(week: int, scoring: str,
                          lineup: Sequence[tuple[str, Optional[PlayerScore]]],
                          recs: dict[str, Recommendation],
                          dashboard_url: Optional[str] = None,
                          banner: Optional[str] = None,
                          commands_url: Optional[str] = None,
                          label: str = "") -> dict:
    """Return a Discord webhook JSON body for the week's summary.

    ``banner`` (the preseason sample-data warning) leads the description and
    flips the embed amber; ``commands_url`` adds a field pointing readers at
    the GitHub issue where the ``/`` commands actually work. ``label`` (a league
    name) is appended to the embed title when set.
    """
    embed = _build_embed(week, scoring, lineup, recs, dashboard_url=dashboard_url,
                         banner=banner, label=label)
    _commands_field(embed, commands_url)
    return {"embeds": [embed]}


def build_multi_discord_payload(week: int, bundles: Sequence["LeagueBundle"],
                                dashboard_url: Optional[str] = None,
                                commands_url: Optional[str] = None) -> dict:
    """One message, one embed per league (Discord allows up to 10 embeds).

    ``bundles`` are ``report.LeagueBundle`` objects (duck-typed to avoid an import
    cycle). The dashboard link + commands hint ride the last embed so the message
    stays scannable.
    """
    embeds: list[dict] = []
    for i, b in enumerate(bundles):
        last = i == len(bundles) - 1
        embed = _build_embed(week, b.scoring, b.lineup, b.recs,
                             dashboard_url=dashboard_url if last else None,
                             banner=b.banner, label=b.label)
        if last:
            _commands_field(embed, commands_url)
        embeds.append(embed)
    return {"embeds": embeds}


def send_discord(webhook_url: str, payload: dict,
                 session: Optional[requests.Session] = None, timeout: int = 20) -> None:
    """POST the payload to a Discord incoming webhook."""
    sess = session or requests.Session()
    resp = sess.post(webhook_url, json=payload, timeout=timeout)
    resp.raise_for_status()
