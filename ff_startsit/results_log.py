"""Append-only decision log — the seam #7 (self-calibration) grows from.

Every rank/compare run writes one JSONL row capturing the week, the candidates,
each signal's raw + normalized value, the weights used, and the chosen pick. A
future calibrator joins these rows against actual fantasy outcomes to learn which
signals deserve more weight in *your* leagues. v1 only writes; it never learns.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .models import Recommendation


def log_recommendation(rec: Recommendation, path: Path, command: str = "") -> None:
    """Append one row describing ``rec`` to the JSONL log at ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "command": command,
        "week": rec.week,
        "scoring": rec.scoring,
        "weights": rec.weights,
        "close_call": rec.close_call,
        "notes": rec.notes,
        "pick": rec.scores[0].player.name if rec.scores and rec.scores[0].final is not None else None,
        "candidates": [
            {
                "key": s.player.key,
                "name": s.player.name,
                "team": s.player.team,
                "position": s.player.position,
                "final": s.final,
                "normalized": s.normalized,
                "raw": {
                    name: sv.raw for name, sv in s.raw.items() if sv.available
                },
                "flags": s.flags,
            }
            for s in rec.scores
        ],
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
