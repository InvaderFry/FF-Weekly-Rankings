"""Append-only decision log — the seam #7 (self-calibration) grows from.

Every rank/compare run writes one JSONL row capturing the week, the candidates,
each signal's raw + normalized value, the weights used, and the chosen pick.
``calibrate/`` reads them back, joins them against actual fantasy outcomes from
Sleeper, and grid-searches the weights that would have ranked *your* leagues best;
``calibrate/backtest.py`` replays them read-only for the hit-rate split. Writing
stays here and stays append-only — nothing in this module ever learns or edits.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .models import Recommendation


def log_recommendation(rec: Recommendation, path: Path, command: str = "",
                       league: str = "") -> None:
    """Append one row describing ``rec`` to the JSONL log at ``path``.

    ``league`` is provenance, not a filter: the log is append-only, so a field not
    written today is one no future analysis can recover for this season's rows.
    A plain string rather than a whole ``Settings`` — this module takes a
    ``Recommendation`` and a path, and should keep that shape.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "command": command,
        "league": league,
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
