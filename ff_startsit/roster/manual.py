"""Manual roster provider — a hand-edited CSV.

For leagues the APIs can't reach, or quick what-ifs. The CSV has headers
``name,team,position`` (case/space tolerant). Teams are normalized through
``data/teams.py`` so "Kansas City", "kc", and "KC" all work. Malformed rows are
reported and skipped rather than crashing the run.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Optional

from ..data.matching import normalize_name
from ..data.teams import normalize_team
from ..models import Player
from .base import RosterError, RosterProvider

VALID_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF"}

TEMPLATE = (
    "name,team,position\n"
    "Patrick Mahomes,KC,QB\n"
    "Bijan Robinson,ATL,RB\n"
    "CeeDee Lamb,DAL,WR\n"
    "Sam LaPorta,DET,TE\n"
    "Harrison Butker,KC,K\n"
    "San Francisco,SF,DEF\n"
)


def parse_manual_csv(text: str, warn=lambda msg: print(msg, file=sys.stderr)) -> list[Player]:
    """Parse manual-roster CSV text into canonical Players (pure, testable)."""
    reader = csv.DictReader(line for line in text.splitlines() if line.strip())
    if not reader.fieldnames:
        raise RosterError("Manual roster CSV is empty.")

    # Tolerate header casing/spaces.
    field_map = {(f or "").strip().lower(): f for f in reader.fieldnames}
    missing = {"name", "position"} - set(field_map)
    if missing:
        raise RosterError(
            f"Manual roster CSV missing column(s): {', '.join(sorted(missing))}. "
            "Expected headers: name,team,position"
        )

    players: list[Player] = []
    seen: set[str] = set()
    for i, row in enumerate(reader, start=2):  # row 1 is the header
        name = (row.get(field_map["name"]) or "").strip()
        pos = (row.get(field_map.get("position", "")) or "").strip().upper()
        team_raw = (row.get(field_map.get("team", "")) or "").strip() if "team" in field_map else ""
        if pos == "DST":
            pos = "DEF"
        if not name:
            warn(f"manual roster line {i}: skipped (no name)")
            continue
        if pos not in VALID_POSITIONS:
            warn(f"manual roster line {i}: skipped ({name!r} has invalid position {pos!r})")
            continue
        key = f"manual-{normalize_name(name)}"
        if key in seen:
            warn(f"manual roster line {i}: skipped duplicate {name!r}")
            continue
        seen.add(key)
        players.append(Player(key=key, name=name, team=normalize_team(team_raw), position=pos))

    if not players:
        raise RosterError("Manual roster CSV had no valid players.")
    return players


def write_template(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TEMPLATE)


class ManualProvider(RosterProvider):
    name = "manual"

    def __init__(self, path: Path):
        self.path = Path(path)

    def get_roster_players(self) -> list[Player]:
        if not self.path.exists():
            example = self.path.with_suffix(self.path.suffix + ".example")
            write_template(example)
            raise RosterError(
                f"Manual roster file not found: {self.path}. "
                f"A template was written to {example} — copy it and fill it in."
            )
        return parse_manual_csv(self.path.read_text())
