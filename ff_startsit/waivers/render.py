"""Markdown for the waiver/trade digest — the GitHub issue comment and stdout.

Mirrors ``report.render_multi_digest``: one document, one section per league,
built from ``WaiverBundle`` and nothing else. Cell text goes through
``output.render.md_cell`` so a player name with a pipe in it can't break a table.
"""

from __future__ import annotations

from datetime import date
from typing import Optional, Sequence

from ..output.render import DEPTH_LEGEND, md_cell
from .models import WaiverBundle, WaiverTarget



def _bid_cell(target: WaiverTarget) -> str:
    return target.bid or "—"


def _depth_cell(ratio: Optional[float]) -> str:
    """An add's positional depth ratio for display, or an em dash."""
    return "—" if ratio is None else f"{ratio:.2f}"


def _adds_table(bundle: WaiverBundle) -> list[str]:
    if not bundle.adds:
        # The bundle decides what an empty section means — outage, thin read, or
        # a genuinely quiet wire — so all three renderers say the same thing.
        # None means a banner is standing and already explains it.
        reason = bundle.no_adds_reason()
        return [f"_{reason}_", ""] if reason else []
    lines = ["| Add | Pos | Depth | Drop for him | Bid | Why |",
             "|---|---|---:|---|---|---|"]
    for t in bundle.adds:
        drop = t.drop.player.name if t.drop else "—"
        # A margin only exists when the add and his drop share a position; across
        # positions the two scores came from different candidate sets and their
        # difference is not a number to print.
        if t.margin is not None:
            drop = f"{md_cell(drop)} (+{t.margin:.1f})"
        else:
            drop = md_cell(drop)
        why = "; ".join(t.reasons[1:]) or "—"  # reasons[0] repeats the drop column
        lines.append(
            f"| **{md_cell(t.score.player.name)}** | {md_cell(t.score.player.position)} "
            f"| {_depth_cell(t.depth_ratio)} | {drop} "
            f"| {md_cell(_bid_cell(t))} | {md_cell(why)} |"
        )
    # The table is ordered by depth ratio, not by final score (see CLAUDE.md's
    # depth_ratio section) — the legend is what makes the column's own numbers
    # readable rather than just visibly sorted.
    lines.extend(["", f"_{DEPTH_LEGEND}_"])
    # A table listing only streamers still leaves the positions that decide a
    # week unexplained; the bundle owns that sentence too.
    gap = bundle.no_adds_at_positions()
    if gap:
        # Blank line first: without it the italic sits flush against the table
        # rows and markdown folds it into the table.
        lines.extend(["", f"_{gap}_"])
    lines.append("")
    return lines


def _ros(rank) -> str:
    """An overall rest-of-season rank for display, or an em dash."""
    return "—" if rank is None else f"{rank:g}"


def _drops_table(bundle: WaiverBundle) -> list[str]:
    if not bundle.drops:
        return []
    # The overall rest-of-season rank, not this week's blend score: it is the key
    # this list is *ordered* by (worst first) and the criterion each row was
    # selected on, and a table sorted by a column it doesn't show reads as
    # unsorted. It is also the only number here that means the same thing for a
    # quarterback and a tight end — the weekly finals came from separate
    # per-position min-max sets, so reading down that column compared nothing.
    lines = ["**Conditional drop candidates** (only for a verified upgrade)", "",
             "| Player | Pos | ROS rank | Why |", "|---|---|---:|---|"]
    for d in bundle.drops:
        lines.append(f"| {md_cell(d.score.player.name)} | {md_cell(d.score.player.position)} "
                     f"| {_ros(d.score.season_rank)} | {md_cell(d.reason)} |")
    lines.append("")
    return lines


def _trades_section(bundle: WaiverBundle) -> list[str]:
    if not bundle.trades:
        # Same contract as the adds section above: the bundle owns what an empty
        # section means, so all three renderers say the same thing, and None
        # means something else already explains the silence.
        reason = bundle.no_trades_reason()
        return ["### Trade ideas", "", f"_{reason}_", ""] if reason else []
    lines = ["### Trade ideas", ""]
    for idea in bundle.trades:
        send = ", ".join(s.player.name for s in idea.you_send)
        get = ", ".join(s.player.name for s in idea.you_get)
        lines.append(f"- **{md_cell(idea.partner)}** — send {md_cell(send)}, "
                     f"get {md_cell(get)} "
                     f"_(your lineup +{idea.your_gain:.1f}, theirs +{idea.their_gain:.1f})_")
        if idea.rationale:
            lines.append(f"  - {idea.rationale}")
    lines.append("")
    return lines


def _stash_section(bundle: WaiverBundle) -> list[str]:
    if not bundle.stashes:
        return []
    lines = ["### Stash watch", ""]
    for s in bundle.stashes:
        lines.append(f"- {md_cell(s.score.player.name)} "
                     f"({md_cell(s.score.player.position)}) — {md_cell(s.reason)}")
    lines.append("")
    return lines


def _bye_section(bundle: WaiverBundle) -> list[str]:
    if not bundle.byes:
        return []
    lines = ["### Bye-week holes ahead", ""]
    for gap in bundle.byes:
        lines.append(f"- Week {gap.week}: only {gap.available} healthy "
                     f"{gap.position}{'s' if gap.available != 1 else ''} for "
                     f"{gap.needed} slot{'s' if gap.needed != 1 else ''}")
    lines.append("")
    return lines


def _roster_section(bundle: WaiverBundle) -> list[str]:
    """Your drafted team, under a banner that would otherwise stand alone.

    Names only — no scores, no ranks. The run that shows this is the one that
    refused to score anything, and a number here would be the invented number
    the refusal exists to avoid.
    """
    groups = bundle.roster_by_position()
    if not groups:
        return []
    lines = ["### Your team (drafted)", ""]
    for position, players in groups:
        names = ", ".join(_with_team(p) for p in players)
        lines.append(f"- **{md_cell(position)}** — {md_cell(names)}")
    lines.append("")
    return lines


def _with_team(player) -> str:
    return f"{player.name} ({player.team})" if player.team else player.name


def _mentions_section(bundle: WaiverBundle) -> list[str]:
    """Quotes from the writers, attributed and linked back to their columns."""
    quoted = [(t, m) for t in bundle.adds for m in t.mentions]
    if not quoted:
        return []
    lines = ["### What the writers said", ""]
    for target, mention in quoted:
        body = mention.snippet or f"named {target.score.player.name} as an add"
        lines.append(f"- **{md_cell(target.score.player.name)}** — "
                     f"[{md_cell(mention.author)}]({mention.url}): "
                     f"“{md_cell(body)}”")
    lines.append("")
    return lines


def render_bundle(bundle: WaiverBundle, heading: str = "###") -> list[str]:
    """One league's section."""
    lines: list[str] = []
    title = f"{heading} {bundle.label}" if bundle.label else f"{heading} Waiver wire"
    lines += [f"{title} · {bundle.scoring.upper()}", ""]
    if bundle.banner:
        lines += [f"> **{bundle.banner}**", ""]
    if bundle.caveat:
        lines += [f"> ⚠️ {bundle.caveat}", ""]
    lines += _adds_table(bundle)
    lines += _drops_table(bundle)
    lines += _trades_section(bundle)
    lines += _stash_section(bundle)
    lines += _bye_section(bundle)
    lines += _mentions_section(bundle)
    lines += _roster_section(bundle)
    # Inside the league's own section, where the numbers they carry can be
    # attributed. Only run-wide notes go to the shared footer below.
    if bundle.league_notes:
        lines += [f"- {n}" for n in bundle.league_notes] + [""]
    return lines


def render_waiver_digest(week: int, bundles: Sequence[WaiverBundle],
                         generated_on: Optional[str] = None) -> str:
    """The full multi-league markdown digest."""
    generated_on = generated_on or date.today().isoformat()
    lines = [f"# Week {week} waiver wire & trades", "",
             f"_Generated {generated_on}._", ""]
    if not bundles:
        lines += ["No configured league could be scored.", ""]
        return "\n".join(lines)

    multi = len(bundles) > 1
    for bundle in bundles:
        lines += render_bundle(bundle, heading="##" if multi else "###")

    notes: list[str] = []
    for bundle in bundles:
        for note in bundle.notes:
            if note not in notes:
                notes.append(note)
    if notes:
        lines += ["---", ""] + [f"- {n}" for n in notes] + [""]

    sources: list[tuple[str, str]] = []
    for bundle in bundles:
        for source in bundle.sources:
            if source not in sources:
                sources.append(source)
    if sources:
        lines += ["**Sources**: " + ", ".join(f"[{a}]({u})" for a, u in sources), ""]
    return "\n".join(lines)
