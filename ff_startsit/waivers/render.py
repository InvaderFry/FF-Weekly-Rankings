"""Markdown for the waiver/trade digest — the GitHub issue comment and stdout.

Mirrors ``report.render_multi_digest``: one document, one section per league,
built from ``WaiverBundle`` and nothing else. Cell text goes through
``output.render.md_cell`` so a player name with a pipe in it can't break a table.
"""

from __future__ import annotations

from datetime import date
from typing import Optional, Sequence

from ..output.render import md_cell
from .models import WaiverBundle, WaiverTarget

_NO_ADDS = "_Nothing on the wire beats anyone you could drop this week._"


def _bid_cell(target: WaiverTarget) -> str:
    return target.bid or "—"


def _adds_table(bundle: WaiverBundle) -> list[str]:
    if not bundle.adds:
        # With a banner standing (preseason), _NO_ADDS would claim a comparison
        # that never ran — the banner already says why the section is empty.
        return [] if bundle.banner else [_NO_ADDS, ""]
    lines = ["| Add | Pos | Score | Drop for him | Bid | Why |",
             "|---|---|---:|---|---|---|"]
    for t in bundle.adds:
        drop = t.drop.player.name if t.drop else "—"
        why = "; ".join(t.reasons[1:]) or "—"  # reasons[0] repeats the drop column
        lines.append(
            f"| **{md_cell(t.score.player.name)}** | {md_cell(t.score.player.position)} "
            f"| {t.score.final:.1f} | {md_cell(drop)} (+{t.margin:.1f}) "
            f"| {md_cell(_bid_cell(t))} | {md_cell(why)} |"
        )
    lines.append("")
    return lines


def _drops_table(bundle: WaiverBundle) -> list[str]:
    if not bundle.drops:
        return []
    lines = ["**Safe to cut** (lineup starters excluded)", "",
             "| Player | Pos | Score | Why |", "|---|---|---:|---|"]
    for d in bundle.drops:
        lines.append(f"| {md_cell(d.score.player.name)} | {md_cell(d.score.player.position)} "
                     f"| {d.score.final:.1f} | {md_cell(d.reason)} |")
    lines.append("")
    return lines


def _trades_section(bundle: WaiverBundle) -> list[str]:
    if not bundle.trades:
        return []
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
