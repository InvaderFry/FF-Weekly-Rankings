"""Discord webhook delivery of the weekly start/sit summary.

Builds a concise embed — suggested lineup + any alerts (injury flags on your
starters, close-call positions) + a link to the full dashboard — and POSTs it to
a Discord incoming webhook. The full per-position detail lives on the dashboard;
the notification is the at-a-glance nudge.

Payload-building is pure and separated from the HTTP POST so it can be tested
offline against an injected session, matching the rest of the codebase.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Sequence

import requests

from ..models import PlayerScore, Recommendation
from .board import close_call_comparison, waiver_cell
from .render import LINEUP_UNSCORED_NOTE, lineup_unscored_keys

if TYPE_CHECKING:                    # the duck-typed bundle, named for the reader
    from ..report import LeagueBundle

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


def _lineup_lines(lineup: Sequence[tuple[str, Optional[PlayerScore]]],
                  unscored: frozenset = frozenset()) -> str:
    lines: list[str] = []
    any_unscored = False
    for slot, pick in lineup:
        if pick is None:
            lines.append(f"**{slot}** — _(no option)_")
        else:
            team = pick.player.team or "BYE"
            if pick.player.key in unscored:
                any_unscored = True
                verdict = "—"
            else:
                verdict = f"{pick.final:.1f}"
            lines.append(f"**{slot}** {pick.player.name} ({team}) — {verdict}")
    if any_unscored:
        lines.append(f"-# {LINEUP_UNSCORED_NOTE}")
    return "\n".join(lines)


def _hole_text(lineup) -> str:
    """The lineup holes and their pickups, bolded, for the embed description."""
    from ..report import hole_lines
    out = []
    for line in hole_lines(lineup):
        out.append(f"**{line}**" if line.startswith("🚨") else line)
    return "\n".join(out)


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
            comparison = close_call_comparison(rec)
            if comparison:
                alerts.append(f"[{pos}] {comparison}")
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
    description = _lineup_lines(lineup, unscored=lineup_unscored_keys(recs))
    # How the FLEX slot was decided travels with the lineup everywhere else, so
    # it has to reach Discord readers too — otherwise they see an incomparable
    # FLEX score, or a template fallback, with nothing saying so.
    caveat = getattr(lineup, "caveat", None)
    if caveat:
        description = f"{description}\n\n-# ⚠️ {caveat}"
    holes = _hole_text(lineup)
    if holes:
        # Above the lineup: a slot nobody can play is the one thing on the card
        # the owner has to act on before kickoff.
        description = f"{holes}\n\n{description}"
    if banner:
        description = f"**{banner}**\n\n{description}"
    embed: dict = {
        "title": _clip(f"🏈 Week {week} start/sit — {scoring.upper()}{label_suffix}",
                       _TITLE_MAX),
        "description": _clip(description, 4096),
        "color": _BANNER_COLOR if (banner or holes) else _EMBED_COLOR,
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


# --- waiver wire & trades --------------------------------------------------
#: Blurple, distinct from the green start/sit post: two scheduled messages land
#: in the same channel each week and the colour is what tells them apart at a
#: glance, before any text is read.
_WAIVER_COLOR = 0x5865F2


def _waiver_trade_lines(bundle) -> list[str]:
    if not bundle.trades:
        # A league this tool declines to price must not read like a league where
        # nothing was worth doing — same contract as the adds description above.
        reason = bundle.no_trades_reason()
        return [reason] if reason else []
    lines: list[str] = []
    for idea in bundle.trades:
        send = ", ".join(s.player.name for s in idea.you_send)
        get = ", ".join(s.player.name for s in idea.you_get)
        lines.append(f"**{idea.partner}**: send {send} → get {get} "
                     f"(you +{idea.your_gain:.0f})")
    return lines


def _waiver_roster_lines(bundle) -> list[str]:
    """The drafted roster, one line per position.

    Names only: the run that carries a roster is the preseason refusal, which
    scored nothing, so any number beside a name would be invented.
    """
    return [f"**{position}** — "
            + ", ".join(f"{p.name} ({p.team})" if p.team else p.name
                        for p in players)
            for position, players in bundle.roster_by_position()]


def _waiver_move_lines(bundle) -> list[str]:
    """``WaiverBundle.moves`` as numbered lines: the same "Your moves" the page
    and the issue open with. The trade line is left out because the card keeps
    its own trade field, which lists every idea (or says why there are none)."""
    moves = [m for m in bundle.moves() if m.kind != "trade"]
    return [f"{i}. **{m.action}** {m.detail}".rstrip() for i, m in enumerate(moves, start=1)]


def _build_waiver_embed(bundle, url: Optional[str] = None) -> dict:
    """One league's waiver embed.

    It opens with the league's "Your moves" (claims, what didn't fit, what was
    held back, stashes, bye holes) and links its title to that league's tab on
    the page (``url``). Trades come last on purpose: ``_fit_message_budget``
    trims the longest block when the message overruns, and a trade idea is the
    part a reader can most afford to open the page for. A waiver claim has a
    deadline tonight.
    """
    label = f" · {bundle.label}" if bundle.label else ""
    title = _clip(f"🔄 Week {bundle.week} waivers — "
                  f"{bundle.scoring.upper()}{label}", _TITLE_MAX)

    banner = bundle.banner
    # One shared definition of every sentence here (``WaiverBundle.moves``),
    # including what an empty wire means — an outage reads differently from a
    # quiet one. Empty under the preseason banner, which explains itself.
    description = "\n".join(_waiver_move_lines(bundle))
    # This embed is the copy most people actually read, so the positions that
    # produced no add are worth the two lines here too.
    gap = bundle.no_adds_at_positions()
    if gap:
        description = f"{description}\n\n{gap}"
    if bundle.freshness:
        description = f"{description}\n\n-# ⏳ {bundle.freshness}".strip()
    if bundle.caveat:
        description = f"**{bundle.caveat}**\n\n{description}".rstrip()
    if banner:
        description = f"**{banner}**\n\n{description}".rstrip()

    embed = {
        "title": title,
        "description": _clip(description, 4096),
        "color": _BANNER_COLOR if (banner or bundle.caveat) else _WAIVER_COLOR,
        "fields": [],
    }
    if url:
        embed["url"] = url
    # "Also consider", "Held back", stashes and bye holes are lines of the
    # description now (``moves``); a veto is still always visible, just there.
    if bundle.drops or bundle.ir_drops:
        names = [d.score.player.name for d in bundle.drops]
        names += [f"{d.score.player.name} (IR: frees an IR slot only)"
                  for d in bundle.ir_drops]
        embed["fields"].append({
            "name": "✂️ Conditional drop candidates",
            "value": _clip(", ".join(names),
                           _FIELD_VALUE_MAX),
            "inline": False,
        })
    roster = _waiver_roster_lines(bundle)
    if roster:
        embed["fields"].append({
            "name": "🏈 Your team (drafted)",
            "value": _clip("\n".join(roster), _FIELD_VALUE_MAX),
            "inline": False,
        })
    trades = _waiver_trade_lines(bundle)
    if trades:
        embed["fields"].append({
            "name": "🤝 Trade ideas",
            "value": _clip("\n".join(trades), _FIELD_VALUE_MAX),
            "inline": False,
        })
    return embed


def build_waiver_payload(week: int, bundles: Sequence,
                         dashboard_url: Optional[str] = None,
                         commands_url: Optional[str] = None) -> dict:
    """The Tuesday waiver message: one embed per league, trimmed to fit.

    Same budgeting contract as ``build_multi_discord_payload`` — Discord refuses
    a payload over 10 embeds or 6000 characters outright, so the message is
    trimmed here rather than sent to be rejected, and the trim is stated in the
    last embed instead of left for the reader to spot.
    """
    links = _league_links(bundles, dashboard_url)
    embeds: list[dict] = []
    budget = _TOTAL_CHARS_MAX
    for b, link in zip(bundles[:_MAX_EMBEDS], links):
        embed = _build_waiver_embed(b, url=link)
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
    league_link = last.get("url")
    _dashboard_field(last, dashboard_url)
    if league_link:
        last["url"] = league_link          # keep the title on its own league's tab
    _commands_field(last, commands_url)
    _fit_message_budget(embeds)
    return {"embeds": embeds}


def _league_links(bundles: Sequence, dashboard_url: Optional[str]) -> list[Optional[str]]:
    """``waivers.html#<league>`` per bundle: the page's own tab names
    (``html.league_slugs``), so a card opens the league it is about. A single
    league has no tabs, so it links the page itself."""
    if not dashboard_url:
        return [None] * len(bundles)
    if len(bundles) < 2:
        return [dashboard_url] * len(bundles)
    from .html import league_slugs
    base = dashboard_url.split("#", 1)[0]
    return [f"{base}#{slug}" for slug in league_slugs([b.label for b in bundles])]


# --- the Tuesday expert board ------------------------------------------------
#: Discord caps an embed at 25 fields; one player per field reads better on a
#: phone than a monospace table, which wraps.
_FIELDS_PER_EMBED = 25


def _board_field(index: int, row, boards) -> dict:
    p = row.player
    name = _clip(f"{index}. {p.name} ({p.position}, {p.team or 'FA'})", _TITLE_MAX)
    from ..waivers.board import cell_position
    parts = [f"{b.header} {waiver_cell(row.entries.get(b.source_id), cell_position(b, p))}"
             for b in boards]
    value = " | ".join(parts)
    if row.tool:
        value += f"\nThis tool: {row.tool}"
    return {"name": name, "value": _clip(value, _FIELD_VALUE_MAX), "inline": False}


def build_waiver_board_payloads(week: int, bundles: Sequence,
                                dashboard_url: Optional[str] = None) -> list[dict]:
    """The Tuesday board, one Discord message (or more) per league.

    Separate from ``build_waiver_payload``'s summary because even the short
    table outgrows one message: every league gets its own, split into 25-field
    embeds and, past Discord's 10-embed / 6000-character message limits, into
    further messages. It carries the rows the page shows first
    (``board_visible_keys``: where the writers agree, and every player this
    tool acted on) and says how many more are on the page, linked to that
    league's tab. The full table used to go out here, ~70 fields a league.
    """
    from ..waivers.board import board_visible_keys
    links = _league_links(bundles, dashboard_url)
    payloads: list[dict] = []
    for bundle, link in zip(bundles, links):
        boards = getattr(bundle, "expert_boards", None)
        if not boards:
            continue
        label = f" · {bundle.label}" if bundle.label else ""
        title = _clip(f"📋 Week {week} — what the writers say{label}", _TITLE_MAX)
        from ..waivers.board import board_reasons
        notes = board_reasons(boards)
        description = "\n".join(f"-# {r}" for r in notes)
        if not bundle.expert_rows:
            description = ("No free agent in this league was named by a writer this week.\n"
                           + description).strip()
        visible = board_visible_keys(bundle.expert_rows)
        hidden = len(bundle.expert_rows) - len(
            [r for r in bundle.expert_rows if r.player.key in visible])
        if hidden:
            more = (f"[on the page]({link})" if link else "on the page")
            description = (f"{hidden} more player{'s' if hidden != 1 else ''} the writers "
                           f"named are {more}.\n" + description).strip()
        # The list position stays the row's number, so a gap shows where the
        # folded rows sit.
        fields = [_board_field(i, row, boards)
                  for i, row in enumerate(bundle.expert_rows, start=1)
                  if row.player.key in visible]
        # Chunk by field count *and* characters: one embed of 25 long fields can
        # exceed the 6000-character message limit on its own, and no amount of
        # splitting into messages fixes an embed that is itself too big.
        first = {"title": title, "color": _WAIVER_COLOR, "fields": []}
        if link:
            first["url"] = link
        if description:
            first["description"] = _clip(description, 4096)
        embeds: list[dict] = [first]
        for f in fields:
            current = embeds[-1]
            size = len(f["name"]) + len(f["value"])
            if current["fields"] and (len(current["fields"]) >= _FIELDS_PER_EMBED
                                      or _embed_chars(current) + size > _TOTAL_CHARS_MAX):
                current = {"title": _clip(f"{title} (cont.)", _TITLE_MAX),
                           "color": _WAIVER_COLOR, "fields": []}
                embeds.append(current)
            current["fields"].append(f)
        message: list[dict] = []
        used = 0
        for embed in embeds:
            cost = _embed_chars(embed)
            if message and (len(message) >= _MAX_EMBEDS or used + cost > _TOTAL_CHARS_MAX):
                payloads.append({"embeds": message})
                message, used = [], 0
            message.append(embed)
            used += cost
        if message:
            payloads.append({"embeds": message})
    return payloads


# --- the Wednesday values sheet ----------------------------------------------
_VALUES_COLOR = 0xA371F7   # purple: the third weekly post, told apart at a glance


def build_values_payloads(week: int, sheets: Sequence) -> list[dict]:
    """Every league's values sheet, full table, one message (or more) each.

    One field per rostered player ("Richard 56 · RB1 | Boone 104 · RB1 | FP ROS
    #3"), then the trade ideas with their per-chart verdicts. Chunked by field
    count and characters, as the Tuesday board is.
    """
    from ..waivers.board import board_reasons, cell_position
    from .board import trade_line, verdict_text
    payloads: list[dict] = []
    for sheet in sheets:
        label = f" · {sheet.label}" if sheet.label else ""
        title = _clip(f"📈 Week {week} trade values{label}", _TITLE_MAX)
        notes = [sheet.banner, sheet.caveat, *board_reasons(sheet.boards)]
        description = "\n".join(f"-# {n}" for n in notes if n)
        fields = []
        for row in sheet.rows:
            p = row.player
            value = " | ".join(f"{b.header} {waiver_cell(row.entries.get(b.source_id), cell_position(b, p))}"
                               for b in sheet.boards)
            if row.split:
                value += f"\n⚠️ {row.split}"
            fields.append({"name": _clip(f"{p.name} ({p.position}, {p.team or 'FA'})", _TITLE_MAX),
                           "value": _clip(value, _FIELD_VALUE_MAX), "inline": False})
        for v in sheet.verdicts:
            fields.append({"name": _clip(f"🤝 {trade_line(v.idea)}", _TITLE_MAX),
                           "value": _clip(verdict_text(v), _FIELD_VALUE_MAX), "inline": False})
        first = {"title": title, "color": _VALUES_COLOR, "fields": []}
        if description:
            first["description"] = _clip(description, 4096)
        embeds = [first]
        for f in fields:
            current = embeds[-1]
            if current["fields"] and (len(current["fields"]) >= _FIELDS_PER_EMBED or
                                      _embed_chars(current) + len(f["name"]) + len(f["value"]) > _TOTAL_CHARS_MAX):
                current = {"title": _clip(f"{title} (cont.)", _TITLE_MAX), "color": _VALUES_COLOR,
                           "fields": []}
                embeds.append(current)
            current["fields"].append(f)
        message, used = [], 0
        for embed in embeds:
            cost = _embed_chars(embed)
            if message and (len(message) >= _MAX_EMBEDS or used + cost > _TOTAL_CHARS_MAX):
                payloads.append({"embeds": message})
                message, used = [], 0
            message.append(embed)
            used += cost
        if message:
            payloads.append({"embeds": message})
    return payloads
