"""Read the append-only decision log back into structured calibration inputs.

Each ``rank``/``compare`` run wrote one JSONL row (see ``results_log.py``) holding
the candidates' per-signal ``normalized`` scores — everything the learner needs to
re-blend under trial weights. We only need the season (to fetch the right week's
outcomes), the scoring mode, and each candidate's id/name/position/normalized.

The log never stored a season, so we infer it from the row timestamp: an NFL season
spans Sep–Feb, so a January Week-17/18 row belongs to the *previous* calendar year.
This mirrors ``cli._date_season``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class Candidate:
    key: str
    name: str
    position: str
    normalized: dict[str, float]   # signal name -> 0..100


@dataclass(frozen=True)
class Decision:
    """One logged rank/compare run, reduced to what calibration consumes.

    ``weights`` and ``close_call`` are what the run actually used/flagged; the
    learner ignores them (it searches its own weights), but ``backtest`` replays
    the logged weights and buckets by the close-call flag. Both default so older
    readers/rows stay valid.
    """

    week: int
    season: str
    scoring: str
    candidates: list[Candidate]
    weights: dict[str, float] = field(default_factory=dict)
    close_call: bool = False


def season_from_ts(ts: str) -> Optional[str]:
    """NFL season year from an ISO timestamp (Jan/Feb belong to the prior year)."""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return str(dt.year if dt.month >= 3 else dt.year - 1)


def load_decisions(path: Path, season: Optional[str] = None,
                   week: Optional[int] = None) -> list[Decision]:
    """Parse ``path`` (JSONL) into Decisions, optionally filtered by season/week.

    Malformed lines and rows without a week are skipped rather than crashing the run.
    "Malformed" covers the whole row, not just its JSON syntax: a non-numeric
    score, a week that isn't a number, or a line that parses to something other
    than an object costs that one row. One corrupt field must not discard a
    season's worth of valid decisions — the log is append-only and never
    rewritten, so a bad row stays bad forever.
    """
    if not path.exists():
        return []
    decisions: list[Decision] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            decision = _parse_row(row, season, week)
        except (ValueError, TypeError, AttributeError):
            continue
        if decision is not None:
            decisions.append(decision)
    return decisions


def _parse_row(row, season: Optional[str], week: Optional[int]) -> Optional[Decision]:
    """One log row -> a Decision, or None when it is filtered out.

    Raises ValueError/TypeError/AttributeError on a malformed row; the caller
    turns that into a skip.
    """
    wk = row.get("week")
    if wk is None:
        return None
    row_season = season_from_ts(row.get("ts", "")) or ""
    if season is not None and row_season != str(season):
        return None
    if week is not None and int(wk) != int(week):
        return None
    candidates = []
    for c in row.get("candidates", []):
        norm = {k: float(v) for k, v in (c.get("normalized") or {}).items()
                if v is not None}
        candidates.append(Candidate(
            key=str(c.get("key", "")),
            name=c.get("name", ""),
            position=(c.get("position") or "").upper(),
            normalized=norm,
        ))
    weights = {str(k): float(v) for k, v in (row.get("weights") or {}).items()
               if v is not None}
    return Decision(
        week=int(wk),
        season=row_season,
        scoring=(row.get("scoring") or "ppr").lower(),
        candidates=candidates,
        weights=weights,
        close_call=bool(row.get("close_call", False)),
    )
