"""Injury status — a soft availability signal (no API key required).

Reads the same free, public Sleeper player-metadata blob the roster sync already
caches (``injury_status`` per player) and turns it into a 0-100 availability
score: a healthy player scores 100, ``Questionable`` 75, ``Doubtful`` 35, and an
``Out``/IR-type designation 0. The score blends in at a small weight so an injured
player sinks without being removed, and any non-healthy designation is attached as
a ``note`` so it surfaces to the user as a flag.

The metadata is keyed by Sleeper player id, but rosters can come from ESPN/manual
(different ids), so we match by (name, position) via the same ``match_rows`` glue
ECR uses — never by ``Player.key``. Parsing is separated from HTTP so it can be
unit-tested against a small fixture.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from ..data.matching import ExternalRow, match_rows
from ..data.teams import normalize_team
from ..models import Player, SignalValue
from ..roster.sleeper import SleeperClient, _KEEP_POSITIONS
from .base import Signal

# Sleeper ``injury_status`` -> availability score (higher = more available).
# Anything not listed (or empty / "Active") is treated as fully healthy.
INJURY_SCORES = {
    "QUESTIONABLE": 75.0,
    "DOUBTFUL": 35.0,
    "OUT": 0.0,
    "IR": 0.0,
    "PUP": 0.0,
    "SUS": 0.0,
    "NA": 0.0,
    "COV": 0.0,
    "DNR": 0.0,
}
HEALTHY_SCORE = 100.0


def score_for_status(status: Optional[str]) -> float:
    """Map a raw Sleeper injury status to a 0-100 availability score."""
    return INJURY_SCORES.get((status or "").strip().upper(), HEALTHY_SCORE)


def is_noteworthy(status: Optional[str]) -> bool:
    """True when a status is worth flagging (anything other than healthy)."""
    return (status or "").strip().upper() in INJURY_SCORES


def is_ruled_out(status: Optional[str]) -> bool:
    """True when a designation means the player cannot play at all.

    Derived from the score table rather than kept as a second list of statuses,
    so a designation added to ``INJURY_SCORES`` at 0.0 is ruled out without a
    second edit — drift between two hand-maintained lists is exactly the kind of
    silent gap the "four places" rule elsewhere in this repo warns about.

    ``Questionable`` and ``Doubtful`` are deliberately *not* ruled out: they
    score low and sink in the ranking, which is the right treatment for a player
    who is likely-to-probably playing. Only a designation that means "not this
    week" (OUT/IR/PUP/SUS/...) takes a player out of the candidate set.
    """
    return score_for_status(status) == 0.0


def parse_injury_rows(meta: dict) -> list[ExternalRow]:
    """Turn the Sleeper player-metadata blob into injury ExternalRows (pure)."""
    rows: list[ExternalRow] = []
    for pid, info in meta.items():
        if not info:
            continue
        position = (info.get("position") or "").upper()
        if position not in _KEEP_POSITIONS:
            continue
        if position == "DEF":
            name = info.get("full_name") or f"{pid} D/ST"
            team = normalize_team(info.get("team") or pid)
        else:
            name = info.get("full_name") or " ".join(
                x for x in [info.get("first_name"), info.get("last_name")] if x
            )
            team = normalize_team(info.get("team"))
        if not name:
            continue
        status = (info.get("injury_status") or "").strip()
        rows.append(
            ExternalRow(
                name=name,
                team=team,
                position=position,
                value=score_for_status(status),
                extra={"status": status},
            )
        )
    return rows


def assign(players: Iterable[Player], rows: list[ExternalRow]) -> dict[str, SignalValue]:
    """Match injury rows onto the roster and build SignalValues (pure; tested)."""
    players = list(players)
    result = match_rows(players, rows)
    out: dict[str, SignalValue] = {}
    for p in players:
        row = result.matched.get(p.key)
        if row is None:
            # No injury record -> assume healthy, but don't fabricate a value:
            # mark unavailable so the blend falls back to the other signals.
            out[p.key] = SignalValue(raw=None, available=False, note="")
            continue
        status = (row.extra or {}).get("status", "")
        note = f"{status}" if is_noteworthy(status) else ""
        out[p.key] = SignalValue(raw=row.value, available=True, note=note)
    return out


class InjurySignal(Signal):
    name = "injury"
    higher_is_better = True  # a higher availability score is better

    def __init__(self, data_dir: Path, enabled: bool = True,
                 client: Optional[SleeperClient] = None):
        self.enabled = enabled
        self.client = client or SleeperClient(data_dir)
        self._meta: Optional[dict] = None  # per-run cache

    def is_available(self) -> bool:
        return self.enabled

    def rules_out(self, value: SignalValue) -> bool:
        """An OUT/IR-type designation means he cannot play — not merely that he
        scores badly. Reads the availability score rather than the note text,
        since the score is what ``score_for_status`` already normalized."""
        return value.available and value.raw is not None and value.raw == 0.0

    def fetch(self, week: int, players: Iterable[Player]) -> dict[str, SignalValue]:
        players = list(players)
        if not self.enabled:
            return {p.key: SignalValue(raw=None, available=False, note="injury disabled")
                    for p in players}
        if self._meta is None:
            self._meta = self.client.load_player_metadata()
        rows = parse_injury_rows(self._meta)
        return assign(players, rows)
