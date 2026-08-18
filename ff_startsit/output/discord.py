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
_TITLE_MAX = 256
#: Discord rejects a webhook payload carrying more than 10 embeds, or more than
#: 6000 characters summed across every embed. One embed per league means a
#: multi-league user reaches both on their own roster count, and the whole
#: message is refused — so the payload is trimmed to fit rather than sent to be
#: rejected. Losing the tail of a league list beats delivering nothing.
_MAX_EMBEDS = 10
_TOTAL_CHARS_MAX = 6000
#: Characters held back from the budget for the trailer the last embed carries
#: (dropped-leagues note + dashboard link + commands hint), so trimming to fit
#: never leaves the message with no room for the parts that explain it.
_TRAILER_RESERVE = 600
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
    # How the FLEX slot was decided travels with the lineup everywhere else, so
    # it has to reach Discord readers too — otherwise they see an incomparable
    # FLEX score, or a template fallback, with nothing saying so.
    caveat = getattr(lineup, "caveat", None)
    if caveat:
        description = f"{description}\n\n-# ⚠️ {caveat}"
    if banner:
        description = f"**{banner}**\n\n{description}"
    embed: dict = {
        "title": _clip(f"🏈 Week {week} start/sit — {scoring.upper()}{label_suffix}",
                       _TITLE_MAX),
        "description": _clip(description, 4096),
        "color": _BANNER_COLOR if banner else _EMBED_COLOR,
        "fields": [],
    }
    alerts = _alerts(lineup, recs)
    if alerts:
        value = _clip("\n".join(f"• {a}" for a in alerts), _FIELD_VALUE_MAX)
    else:
        value = "None — all clear 🎉"
    embed["fields"].append({"name": "⚠️ Alerts", "value": value, "inline": False})

    _dashboard_field(embed, dashboard_url)
    return embed


def _dashboard_field(embed: dict, dashboard_url: Optional[str]) -> None:
    """Link the embed to the full dashboard (title link + field)."""
    if not dashboard_url:
        return
    embed["url"] = dashboard_url
    embed["fields"].append(
        {"name": "Full dashboard",
         "value": _clip(dashboard_url, _FIELD_VALUE_MAX), "inline": False}
    )


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


def _embed_chars(embed: dict) -> int:
    """Characters Discord counts against the 6000-per-message budget."""
    total = len(embed.get("title") or "") + len(embed.get("description") or "")
    total += len((embed.get("footer") or {}).get("text") or "")
    for f in embed.get("fields") or []:
        total += len(f.get("name") or "") + len(f.get("value") or "")
    return total


def _fit_message_budget(embeds: list[dict]) -> None:
    """Shrink the longest text blocks in place until the message fits.

    The per-field caps are individually generous enough that their sum can still
    exceed the 6000-character message limit, so the budget has to be enforced on
    the assembled message rather than assumed from the parts. Titles and field
    names are left alone — they are short, bounded, and carry the structure —
    which also guarantees this terminates below the limit.
    """
    def blocks(embed: dict) -> list[tuple[dict, str]]:
        """Every (container, key) holding shrinkable text in one embed."""
        return [(embed, "description")] + [(f, "value") for f in embed["fields"]]

    while sum(_embed_chars(e) for e in embeds) > _TOTAL_CHARS_MAX:
        holder, key = max(
            (pair for e in embeds for pair in blocks(e)),
            key=lambda pair: len(pair[0].get(pair[1]) or ""),
        )
        current = holder.get(key) or ""
        if len(current) <= 1:
            return                      # nothing left to give back
        holder[key] = _clip(current, max(1, len(current) // 2))


def build_multi_discord_payload(week: int, bundles: Sequence["LeagueBundle"],
                                dashboard_url: Optional[str] = None,
                                commands_url: Optional[str] = None) -> dict:
    """One message, one embed per league, trimmed to what Discord will accept.

    ``bundles`` are ``report.LeagueBundle`` objects (duck-typed to avoid an import
    cycle). The dashboard link + commands hint ride the last *emitted* embed so
    the message stays scannable even when trailing leagues were dropped, and the
    drop is stated in that embed rather than left for the reader to notice.
    """
    embeds: list[dict] = []
    budget = _TOTAL_CHARS_MAX
    for b in bundles[:_MAX_EMBEDS]:
        embed = _build_embed(week, b.scoring, b.lineup, b.recs,
                             dashboard_url=None, banner=b.banner, label=b.label)
        # Reserve room for the trailer the last embed still has to carry.
        cost = _embed_chars(embed)
        if embeds and cost > budget - _TRAILER_RESERVE:
            break
        budget -= cost
        embeds.append(embed)

    if not embeds:
        return {"embeds": []}

    dropped = len(bundles) - len(embeds)
    last = embeds[-1]
    if dropped > 0:
        last["fields"].append({
            "name": "⚠️ Not shown",
            "value": _clip(f"{dropped} more league(s) didn't fit in one Discord "
                           "message — see the dashboard for the full set.",
                           _FIELD_VALUE_MAX),
            "inline": False,
        })
    _dashboard_field(last, dashboard_url)
    _commands_field(last, commands_url)
    _fit_message_budget(embeds)
    return {"embeds": embeds}


def send_discord(webhook_url: str, payload: dict,
                 session: Optional[requests.Session] = None, timeout: int = 20) -> None:
    """POST the payload to a Discord incoming webhook."""
    sess = session or requests.Session()
    resp = sess.post(webhook_url, json=payload, timeout=timeout)
    resp.raise_for_status()
