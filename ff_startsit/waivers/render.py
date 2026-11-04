"""Markdown for the waiver/trade digest — the GitHub issue comment and stdout.

Mirrors ``report.render_multi_digest``: one document, one section per league,
built from ``WaiverBundle`` and nothing else. Cell text goes through
``output.render.md_cell`` so a player name with a pipe in it can't break a table.
"""

from __future__ import annotations

from typing import Optional, Sequence

from ..output.board import waiver_cell, waiver_legend
from ..output.render import DEPTH_LEGEND, md_cell
from .board import board_marker, board_reasons, bundles_read, cell_position
from ..season import generated_stamp
from .models import STREAM_TABLE_NOTE, WaiverBundle, WaiverTarget
from .values import chart_cell, sheet_cell



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
        drop = t.drop_label
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


def _alternates_table(bundle: WaiverBundle) -> list[str]:
    """The "Also consider adding" section — adds with no roster spot behind them.

    Deliberately a separate table rather than more rows in the adds table: every
    row up there is an instruction ("drop X for Y") and these carry no drop, so
    folding them in would print an add list whose Drop column was half empty and
    leave a reader guessing which half was real. There is no reason sentence for
    an empty section here — the adds table above already accounts for the wire,
    and silence means only that it took everything worth taking.
    """
    if not bundle.alternates:
        return []
    lines = ["**Also consider adding** (no roster spot open — these clear the "
             "same bar as the table above)", "",
             "| Add | Pos | Depth | Bid | Why |", "|---|---|---:|---|---|"]
    for t in bundle.alternates:
        why = "; ".join(t.reasons) or "—"  # no drop line to skip here
        lines.append(
            f"| {md_cell(t.score.player.name)} | {md_cell(t.score.player.position)} "
            f"| {_depth_cell(t.depth_ratio)} | {md_cell(_bid_cell(t))} "
            f"| {md_cell(why)} |"
        )
    lines.append("")
    return lines


def _held_back_section(bundle: WaiverBundle) -> list[str]:
    """Adds the blend would have made that the writers didn't back."""
    if not bundle.held_back:
        return []
    return ["**Held back: the experts don't back these**", "",
            *[f"- {md_cell(line)}" for line in bundle.held_back], ""]


def _stream_section(bundle: WaiverBundle) -> list[str]:
    """Your kicker and defense beside the best free agents, experts side by side."""
    if not bundle.stream_rows:
        return []
    cols = list(bundle.stream_columns)
    head = ["Player", "Team", "Score", "FP Wk", *cols, "This tool"]
    lines = ["**Kicker and defense: this week's experts**", ""]
    for pos, rows in bundle.stream_rows.items():
        lines += [f"_{pos}_", "", "| " + " | ".join(head) + " |",
                  "|---|---|---:|---|" + "---|" * len(cols) + "---|"]
        for row in rows:
            cells = [md_cell(c) for c in row.cells(cols)]
            if row.mine:
                cells[0] = f"**{cells[0]}** (yours)"
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    lines += [f"_{STREAM_TABLE_NOTE}_", ""]
    return lines


def _ros(rank) -> str:
    """An overall rest-of-season rank for display, or an em dash."""
    return "—" if rank is None else f"{rank:g}"


def _drops_table(bundle: WaiverBundle) -> list[str]:
    if not bundle.drops and not bundle.ir_drops:
        return []
    # The overall rest-of-season rank, not this week's blend score: it is the key
    # this list is *ordered* by (worst first) and the criterion each row was
    # selected on, and a table sorted by a column it doesn't show reads as
    # unsorted. It is also the only number here that means the same thing for a
    # quarterback and a tight end — the weekly finals came from separate
    # per-position min-max sets, so reading down that column compared nothing.
    # Each writer's trade chart beside the ROS rank: what the people you trust
    # say he is worth before you cut him. Display only — the drops were chosen
    # on the ROS rank, and these columns change none of them.
    charts = _chart_boards(bundle)
    head = "".join(f" {md_cell(b.header)} |" for b in charts)
    lines = ["**Conditional drop candidates** (only for a verified upgrade)", "",
             f"| Player | Pos | ROS rank |{head} Why |",
             "|---|---|---:|" + "---|" * len(charts) + "---|"]
    # IR cuts ride at the bottom, their reason saying why none is paired with
    # an add: the slot they free is an IR slot, not a roster spot.
    for d in list(bundle.drops) + list(bundle.ir_drops):
        cells = "".join(f" {md_cell(chart_cell(b, d.score.player))} |" for b in charts)
        lines.append(f"| {md_cell(d.score.player.name)} | {md_cell(d.score.player.position)} "
                     f"| {_ros(d.score.season_rank)} |{cells} {md_cell(d.reason)} |")
    lines.append("")
    return lines


def _chart_boards(bundle: WaiverBundle) -> list:
    from .values import chart_boards
    return chart_boards(bundle.values) if bundle.values is not None else []


def _value_table_lines(sheet) -> list[str]:
    """A sheet's roster rows with every chart side by side, its legend and the
    reasons any chart couldn't be read. Shared by the Wednesday values sheet and
    the waiver page's "My roster" table, so the two can't disagree."""
    lines: list[str] = []
    if sheet.rows:
        header = ["Player", "Pos", "Team", *[b.header for b in sheet.boards], "Charts"]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("|" + "|".join("---" for _ in header) + "|")
        for row in sheet.rows:
            cells = [md_cell(row.player.name), row.player.position, row.player.team or "FA",
                     *[md_cell(sheet_cell(b, row)) for b in sheet.boards],
                     md_cell(row.charts_cell)]
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    lines.append(f"_{md_cell(waiver_legend(sheet.boards))} Values are each writer's own "
                 "scale, so they're never added across charts._")
    for reason in board_reasons(sheet.boards):
        lines.append(f"- {md_cell(reason)}")
    lines.append("")
    return lines


def _roster_values_section(bundle: WaiverBundle) -> list[str]:
    sheet = bundle.values
    if sheet is None or not sheet.boards or not sheet.rows:
        return []
    return ["**My roster: rest-of-season value** (trade charts double as "
            "rest-of-season rankings)", "", *_value_table_lines(sheet)]


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
        # The bundle decides what an empty section means, exactly as it does for
        # adds and trades — a quiet wire or a gate that held, not a blank space.
        reason = bundle.no_stashes_reason()
        return ["### Stash watch", "", f"_{reason}_", ""] if reason else []
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
    """Quotes from the writers, attributed and linked back to their columns.

    Only when the writers' board is off: with it on, the quotes sit under the
    board beside the writers' own numbers (``_expert_board_section``).
    """
    if bundle.expert_boards:
        return []
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


def _board_table(boards, rows) -> list[str]:
    header = ["Player", "Pos", "Team", *[b.header for b in boards], "This tool"]
    lines = ["| " + " | ".join(header) + " |",
             "|" + "|".join("---" for _ in header) + "|"]
    for row in rows:
        cells = [md_cell(row.player.name), row.player.position, row.player.team or "FA",
                 *[md_cell(waiver_cell(row.entries.get(b.source_id), cell_position(b, row.player)))
                   for b in boards],
                 md_cell(row.tool or "—")]
        lines.append("| " + " | ".join(cells) + " |")
    return lines + [""]


def _quote_lines(quoted) -> list[str]:
    return [f"- **{md_cell(row.player.name)}** — [{md_cell(q.author)}]({q.url}): "
            f"“{md_cell(q.snippet)}”" for row, q in quoted] + [""]


def _expert_board_section(bundle: WaiverBundle) -> list[str]:
    """Every free agent a writer named, each writer's reading side by side.

    The rows ``board_visible_keys`` picks (the same ones the page shows first)
    lead; the rest, with their quotes, sit in a folded ``<details>`` below, so
    the issue stays the complete weekly record without opening on ~70 rows.
    """
    boards = bundle.expert_boards
    if not boards:
        return []
    from .board import board_visible_keys
    rows = bundle.expert_rows
    visible = board_visible_keys(rows)
    shown = [r for r in rows if r.player.key in visible]
    folded = [r for r in rows if r.player.key not in visible]
    lines = ["### What the writers say", ""]
    if rows:
        if folded:
            lines += [f"_Showing {len(shown)} of {len(rows)}: the top of the list, where "
                      "the writers agree most, plus every player this tool acted on._", ""]
        lines += _board_table(boards, shown)
    else:
        lines += ["_No free agent in this league was named by a writer this week._", ""]
    lines.append(f"_{md_cell(waiver_legend(boards))}_")
    for reason in board_reasons(boards) + list(getattr(bundle, "board_pending", [])):
        lines.append(f"- {md_cell(reason)}")
    lines.append("")
    quoted = [(row, q) for row in shown for q in row.quotes]
    if quoted:
        lines += ["**Why, in their words**", ""] + _quote_lines(quoted)
    if folded:
        # Blank lines inside <details> are what make GitHub render the markdown.
        lines += [f"<details><summary>The other {len(folded)} players the writers named"
                  "</summary>", ""] + _board_table(boards, folded)
        more = [(row, q) for row in folded for q in row.quotes]
        if more:
            lines += ["**Why, in their words**", ""] + _quote_lines(more)
        lines += ["</details>", ""]
    return lines


def _moves_section(bundle: WaiverBundle) -> list[str]:
    """``WaiverBundle.moves``: the page's "Your moves" card, as a numbered list."""
    moves = bundle.moves()
    if not moves:
        return []
    return ["**Your moves**", "",
            *[f"{i}. **{md_cell(m.action)}** {md_cell(m.detail)}"
              for i, m in enumerate(moves, start=1)], ""]


def render_bundle(bundle: WaiverBundle, heading: str = "###") -> list[str]:
    """One league's section."""
    lines: list[str] = []
    title = f"{heading} {bundle.label}" if bundle.label else f"{heading} Waiver wire"
    lines += [f"{title} · {bundle.scoring.upper()}", ""]
    if bundle.banner:
        lines += [f"> **{bundle.banner}**", ""]
    if bundle.caveat:
        lines += [f"> ⚠️ {bundle.caveat}", ""]
    if bundle.freshness:
        lines += [f"> ⏳ {md_cell(bundle.freshness)}", ""]
    lines += _roster_section(bundle)
    lines += _moves_section(bundle)
    # The page's order: decisions first, then the research they rest on (the
    # writers' board and the roster's chart values), which used to open the
    # section and push the claims below ~70 rows of table.
    claims = _adds_table(bundle)
    if claims:
        lines += ["**Claims**", ""] + claims
    lines += _alternates_table(bundle)
    lines += _held_back_section(bundle)
    lines += _stream_section(bundle)
    lines += _drops_table(bundle)
    lines += _trades_section(bundle)
    lines += _stash_section(bundle)
    lines += _bye_section(bundle)
    lines += _mentions_section(bundle)
    lines += _expert_board_section(bundle)
    lines += _roster_values_section(bundle)
    # Inside the league's own section, where the numbers they carry can be
    # attributed. Only run-wide notes go to the shared footer below.
    if bundle.league_notes:
        lines += [f"- {n}" for n in bundle.league_notes] + [""]
    return lines


def render_waiver_digest(week: int, bundles: Sequence[WaiverBundle],
                         generated_on: Optional[str] = None) -> str:
    """The full multi-league markdown digest."""
    generated_on = generated_on or generated_stamp()
    lines = [f"# Week {week} waiver wire & trades", "",
             f"_Generated {generated_on}._", ""]
    if not bundles:
        lines += ["No configured league could be scored.", ""]
        return "\n".join(lines)

    from ..data_status import bundle_status
    status = bundle_status(bundles)
    if status:
        lines += [status.markdown(), ""]
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
    # Invisible in the rendered issue; read back by the Tuesday retry gate.
    # Written even with every board off (``read=``): the season/week it carries
    # is what tells next week's report from a retry of this one, and without it
    # the default config (FF_ANALYSTS unset) let last week's open issue
    # suppress every report after it.
    from ..season import season_year
    from .board import run_mode
    lines += [board_marker(season_year(), week, bundles_read(bundles), run_mode(bundles)), ""]
    # Read back by Wednesday's rebuild, which posts to Discord only if these changed.
    from .board import adds_marker
    lines += [adds_marker(season_year(), week, bundles), ""]
    return "\n".join(lines)


# --- the Wednesday values sheet ------------------------------------------------

def _values_section(sheet, heading: str = "###") -> list[str]:
    from ..output.board import trade_line, verdict_text
    title = f"{heading} {sheet.label}" if sheet.label else f"{heading} Your team"
    lines = [f"{title} · {sheet.scoring.upper()}", ""]
    if sheet.banner:
        lines += [f"> **{sheet.banner}**", ""]
    if sheet.caveat:
        lines += [f"> ⚠️ {sheet.caveat}", ""]
    if not sheet.boards:
        return lines
    lines += _value_table_lines(sheet)
    lines.append(f"{heading}# Trade ideas, judged on each chart")
    lines.append("")
    if sheet.verdicts:
        for v in sheet.verdicts:
            lines.append(f"- {md_cell(trade_line(v.idea))} — {md_cell(verdict_text(v))}")
        lines.append("")
        lines.append("_Net = value you get minus value you send, on that chart. "
                     "\"Off chart\" means someone in the deal has no value there._")
    elif sheet.trades_considered:
        lines.append("_No trade idea cleared the waiver pass's fairness checks this week._")
    else:
        lines.append("_Trade ideas weren't searched for this league._")
    lines.append("")
    return lines


def render_values_digest(week: int, sheets, generated_on: Optional[str] = None) -> str:
    """The Wednesday digest: every league's values sheet plus the retry marker."""
    from .values import sheets_read
    generated_on = generated_on or generated_stamp()
    lines = [f"# Week {week} trade values", "", f"_Generated {generated_on}._", ""]
    if not sheets:
        return "\n".join(lines + ["No configured league could be scored.", ""])
    from ..data_status import bundle_status
    status = bundle_status([s.bundle for s in sheets if s.bundle is not None])
    if status:
        lines += [status.markdown(), ""]
    multi = len(sheets) > 1
    for sheet in sheets:
        lines += _values_section(sheet, heading="##" if multi else "###")
    sources: list[tuple[str, str]] = []
    for sheet in sheets:
        for b in sheet.boards:
            if b.url and (b.label, b.url) not in sources:
                sources.append((b.label, b.url))
    if sources:
        lines += ["**Sources**: " + ", ".join(f"[{a}]({u})" for a, u in sources), ""]
    from ..season import season_year
    from .board import run_mode
    lines += [board_marker(season_year(), week, sheets_read(sheets), run_mode(sheets)), ""]
    return "\n".join(lines)
