"""Terminal rendering + CSV/JSON export of a Recommendation."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

from ..models import Recommendation

_console = Console()


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
            row.append("—" if n is None else f"{n:.0f}")
        row.append("; ".join(s.flags))
        style = "bold green" if i == 1 and s.final is not None else None
        table.add_row(*row, style=style)

    _console.print(table)

    if rec.close_call:
        _console.print("[bold yellow]⚠ Close call[/bold yellow] — lean, don't bank on it:")
        for note in rec.notes:
            _console.print(f"  • {note}")
    elif rec.scores and rec.scores[0].final is not None:
        _console.print(f"[bold green]✓ Start:[/bold green] {rec.scores[0].player.name}")


def render_markdown(rec: Recommendation, title: str = "") -> str:
    """Render a Recommendation as GitHub-flavored markdown (for issues/ChatOps)."""
    signal_names = sorted({name for s in rec.scores for name in s.normalized})
    lines: list[str] = []
    if title:
        lines.append(f"### {title}")

    header = ["#", "Player", "Pos", "Team", "Score", *[n.upper() for n in signal_names], "Flags"]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join("---" for _ in header) + "|")

    for i, s in enumerate(rec.scores, start=1):
        verdict = "—" if s.final is None else f"{s.final:.1f}"
        cells = [str(i), s.player.name, s.player.position, s.player.team or "BYE", verdict]
        for name in signal_names:
            n = s.normalized.get(name)
            cells.append("—" if n is None else f"{n:.0f}")
        cells.append("; ".join(s.flags))
        lines.append("| " + " | ".join(cells) + " |")

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
