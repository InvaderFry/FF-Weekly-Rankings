"""Terminal rendering + CSV/JSON export of a Recommendation."""

from __future__ import annotations

import csv
import json
from html import escape
from pathlib import Path

from rich.console import Console
from rich.table import Table

from ..models import Recommendation, _fmt_raw
from ..engine.analyst import AnalystConflict

_console = Console()


#: Shown under a table with a single candidate. See ``Recommendation.unranked``.
UNRANKED_NOTE = ("Only one candidate here, so there is nothing to rank against — the per-signal columns are blank rather than showing a midpoint placeholder.")

#: Shown under the suggested-lineup table when a pick's score is a fabricated
#: midpoint rather than a real reading. See ``lineup_unscored_keys``.
LINEUP_UNSCORED_NOTE = ("— = too few candidates at that slot to score against "
                        "each other, so the number would be a fabricated "
                        "midpoint, not a real signal.")

#: Legend for the waiver add table's "Depth" column (`waivers.score.depth_ratio`).
#: Lives here, not in ``waivers/``, because ``output/html.py`` cannot import
#: upward from ``waivers/`` and both it and ``waivers/render.py`` need the same
#: wording rather than two invented copies.
DEPTH_LEGEND = ("Depth = positional rank ÷ what the league starts there. Below "
                "1.00 is a startable player; above is bench depth.")


def lineup_unscored_keys(recs: dict[str, Recommendation]) -> frozenset[str]:
    """Player keys whose lineup ``final`` is a fabricated midpoint, not a reading.

    ``engine.normalize.to_0_100`` maps a single usable value to the neutral
    midpoint (50), so a position with fewer than two scored candidates —
    ``Recommendation.unranked`` — blends into an exact 50.0 with nothing real
    behind it. 10 of 27 Week 1 lineup rows read exactly ``50.0`` this way while
    the section right below said "no signal could be scored" — one report
    contradicting itself. ``report.build_lineup`` still fills the slot from
    that number (the player still has to be startable), but showing it in a
    table reads as a score. One definition, shared by the digest, the
    dashboard and the Discord embed.
    """
    return frozenset(s.player.key for rec in recs.values() if rec.unranked
                     for s in rec.scores)


def render_table(rec: Recommendation, title: str = "") -> None:
    """Print a ranked start/sit table, then any close-call notes."""
    signal_names = sorted({name for s in rec.scores for name in s.normalized})
    header = title or f"Week {rec.week} • {rec.scoring.upper()} • weights {rec.weights}"

    table = Table(title=header, header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("Player")
    table.add_column("Pos")
    table.add_column("Team")
    table.add_column("Score", justify="right")
    for name in signal_names:
        table.add_column(name.upper(), justify="right")
    table.add_column("Flags")

    for i, s in enumerate(rec.scores, start=1):
        verdict = "—" if s.final is None else f"{s.final:.1f}"
        row = [str(i), s.player.name, s.player.position, s.player.team or "BYE", verdict]
        for name in signal_names:
            n = s.normalized.get(name)
            row.append("—" if n is None or rec.unranked else f"{n:.0f}")
        row.append("; ".join(s.flags))
        style = "bold green" if i == 1 and s.final is not None else None
        table.add_row(*row, style=style)

    _console.print(table)

    if rec.unranked and rec.scores:
        _console.print(f"[dim]{UNRANKED_NOTE}[/dim]")

    if rec.close_call:
        _console.print("[bold yellow]⚠ Close call[/bold yellow] — lean, don't bank on it:")
        for note in rec.notes:
            _console.print(f"  • {note}")
    elif rec.scores and rec.scores[0].final is not None:
        _console.print(f"[bold green]✓ Start:[/bold green] {rec.scores[0].player.name}")


def md_cell(text: str) -> str:
    """Make ``text`` safe to drop into a GFM table cell.

    An unescaped ``|`` in a player name, flag or expert name opens a phantom
    column, and a newline ends the table outright — so the rest of the report
    renders as prose. ``html.py`` escapes every interpolation for the same
    reason; this is the markdown side of that.
    """
    return str(text).replace("|", "\\|").replace("\r\n", " ").replace("\n", " ")


def _lone_candidate_markdown(rec: Recommendation) -> list[str]:
    """The whole section as one sentence, keeping every reading that was real."""
    s = rec.scores[0]
    team = s.player.team or "BYE"
    lines = ["",
             f"✅ **Start:** {s.player.name} ({team}) — your only "
             f"{s.player.position} this week, so there was nothing to rank him "
             "against and no signal could be scored."]
    if s.flags:
        # The flags are the one thing here that is a real reading rather than a
        # placeholder, so they survive the collapse.
        lines.append(f"_Flags: {md_cell('; '.join(s.flags))}._")
    return lines


def flat_signal_note(rec: Recommendation) -> str:
    """One sentence naming columns whose 0-100 spread is drawn from almost nothing.

    Without it the table's most extreme-looking column can be its least
    meaningful: Week 1 put the #1 back at ``VEGAS 0`` in two leagues, which is
    what min-max does to the low end of a set that may span half an implied
    point. The raw units are the only scale that can say which it was.
    """
    flat = rec.flat_signals()
    if not flat:
        return ""
    parts = [f"{name} spans {_fmt_raw(spread)} (separation threshold {_fmt_raw(gap)})"
             for name, spread, gap in flat]
    column = "column magnifies" if len(flat) == 1 else "columns magnify"
    return ("Read with care: " + "; ".join(parts) +
            f" in raw units across these candidates, so that 0-100 {column} "
            "a gap too small to rank on.")


def analyst_conflict_text(conflict: AnalystConflict) -> str:
    """Plain wording shared by HTML and Markdown, escaped by each renderer."""
    pos = "DST" if conflict.position in {"DEF", "DST"} else conflict.position
    preferred = f"{conflict.preferred} (his {pos}{_fmt_raw(conflict.preferred_rank)})"
    leader = f"{conflict.leader} (his {pos}{_fmt_raw(conflict.leader_rank)})"
    if conflict.material:
        action = ("would flip your last starting spot" if conflict.boundary
                  else "disagrees: he starts")
        separator = ": " if conflict.boundary else " "
        return f"{conflict.analyst} {action}{separator}{preferred} over {leader}."
    spot = " at your last starting spot" if conflict.boundary else ""
    return (f"{conflict.analyst} has these two within {_fmt_raw(conflict.gap)} spots{spot} "
            f"({conflict.leader} {pos}{_fmt_raw(conflict.leader_rank)}, "
            f"{conflict.preferred} {pos}{_fmt_raw(conflict.preferred_rank)}), "
            f"edge to {conflict.preferred}.")


def render_markdown(rec: Recommendation, title: str = "") -> str:
    """Render a Recommendation as GitHub-flavored markdown (for issues/ChatOps)."""
    signal_names = sorted({name for s in rec.scores for name in s.normalized})
    lines: list[str] = []
    if title:
        lines.append(f"### {title}")

    if rec.lone_candidate:
        lines.extend(_lone_candidate_markdown(rec))
        return "\n".join(lines)

    header = ["#", "Player", "Pos", "Team", "Score",
              *[md_cell(n.upper()) for n in signal_names], "Flags"]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join("---" for _ in header) + "|")

    for i, s in enumerate(rec.scores, start=1):
        verdict = "—" if s.final is None else f"{s.final:.1f}"
        cells = [str(i), md_cell(s.player.name), md_cell(s.player.position),
                 md_cell(s.player.team or "BYE"), verdict]
        for name in signal_names:
            n = s.normalized.get(name)
            cells.append("—" if n is None or rec.unranked else f"{n:.0f}")
        cells.append(md_cell("; ".join(s.flags)))
        lines.append("| " + " | ".join(cells) + " |")

    lines.append("")
    if rec.unranked and rec.scores:
        lines.append(f"_{UNRANKED_NOTE}_")
        lines.append("")
    flat = flat_signal_note(rec)
    if flat:
        lines.append(f"_{flat}_")
        lines.append("")
    for conflict in rec.analyst_conflicts:
        text = md_cell(escape(analyst_conflict_text(conflict)))
        lines.append(f"> ⚠️ {text}" if conflict.material else text)
        lines.append("")
    if rec.close_call:
        lines.append("> ⚠️ **Close call** — lean, don't bank on it:")
        for note in rec.notes:
            lines.append(f"> - {note}")
    elif rec.scores and rec.scores[0].final is not None:
        lines.append(f"✅ **Start:** {rec.scores[0].player.name}")
    return "\n".join(lines)


def to_rows(rec: Recommendation) -> list[dict]:
    rows = []
    for rank, s in enumerate(rec.scores, start=1):
        row = {
            "rank": rank,
            "player": s.player.name,
            "position": s.player.position,
            "team": s.player.team or "",
            "final": s.final if s.final is not None else "",
            "flags": "; ".join(s.flags),
        }
        for name, val in s.normalized.items():
            row[f"norm_{name}"] = val
        rows.append(row)
    return rows


def export_csv(rec: Recommendation, path: Path) -> None:
    rows = to_rows(rec)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for r in rows:
        for k in r:
            if k not in fieldnames:
                fieldnames.append(k)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def export_json(rec: Recommendation, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "week": rec.week,
        "scoring": rec.scoring,
        "weights": rec.weights,
        "close_call": rec.close_call,
        "notes": rec.notes,
        "scores": to_rows(rec),
    }
    path.write_text(json.dumps(payload, indent=2))
