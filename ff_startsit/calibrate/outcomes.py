"""Actual weekly fantasy outcomes — the data source #7 calibration learns against.

Sleeper's free, no-auth weekly stats endpoint returns one row per player keyed by
the *same* Sleeper player id the roster sync uses, and ships precomputed fantasy
points per scoring mode (``pts_ppr`` / ``pts_half_ppr`` / ``pts_std``) — so there is
no scoring math to redo and no name-matching for Sleeper-sourced logs.

To also cover ESPN (``espn-{id}``) and manual rosters — whose log keys are *not*
Sleeper ids — we additionally index points by (normalized name, position) using the
Sleeper player-metadata blob, and fall back to that when a key lookup misses.

HTTP is separated from parsing (per the project convention) so the pure functions
test offline against a small fixture.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Mapping, Optional

import requests

from ..data.matching import player_match_key

BASE = "https://api.sleeper.app/v1"
STATS_CACHE_TTL = 6 * 3600  # seconds; finalized weeks rarely move, recent ones can

# User-facing scoring choice -> Sleeper stats field holding precomputed points.
_PTS_FIELD = {"ppr": "pts_ppr", "half": "pts_half_ppr", "std": "pts_std"}


def points_field(scoring: str) -> str:
    """Sleeper stats key for ``scoring`` (defaults to PPR for unknown values)."""
    return _PTS_FIELD.get((scoring or "").lower(), "pts_ppr")


def parse_stats(blob: Mapping[str, dict], scoring: str) -> dict[str, float]:
    """Map a Sleeper weekly-stats blob to ``{sleeper_id: points}`` for ``scoring``.

    Players with no posted points for the mode (didn't play) are omitted.
    """
    field = points_field(scoring)
    out: dict[str, float] = {}
    for pid, stats in (blob or {}).items():
        if not isinstance(stats, dict):
            continue
        val = stats.get(field)
        if val is None:
            continue
        try:
            out[str(pid)] = float(val)
        except (TypeError, ValueError):
            continue
    return out


class OutcomeIndex:
    """Resolve a logged candidate to its actual points, key-first then name/pos.

    ``stats_by_id`` joins Sleeper-sourced logs directly; ``by_name_pos`` (built from
    player metadata) covers ESPN/manual logs whose keys are not Sleeper ids.
    """

    def __init__(self, stats_by_id: Mapping[str, float],
                 by_name_pos: Mapping[tuple[str, str], float]):
        self._by_id = dict(stats_by_id)
        self._by_name_pos = dict(by_name_pos)

    def get(self, key: str, name: str, position: str) -> Optional[float]:
        if key in self._by_id:
            return self._by_id[key]
        return self._by_name_pos.get(player_match_key(name, position))

    def __len__(self) -> int:
        return len(self._by_id)


def build_outcome_lookup(stats_by_id: Mapping[str, float],
                         meta: Mapping[str, dict]) -> OutcomeIndex:
    """Combine id->points with a (name, position)->points fallback from ``meta``."""
    by_name_pos: dict[tuple[str, str], float] = {}
    for pid, pts in stats_by_id.items():
        info = meta.get(str(pid))
        if not info:
            continue
        position = (info.get("position") or "").upper()
        if position == "DEF":
            name = info.get("full_name") or info.get("team") or str(pid)
        else:
            name = info.get("full_name") or " ".join(
                x for x in [info.get("first_name"), info.get("last_name")] if x
            )
        if name and position:
            by_name_pos[player_match_key(name, position)] = pts
    return OutcomeIndex(stats_by_id, by_name_pos)


class SleeperStatsClient:
    """Fetch + disk-cache Sleeper weekly stats (mirrors ``roster.sleeper`` style)."""

    def __init__(self, data_dir: Path, session: Optional[requests.Session] = None,
                 timeout: int = 30):
        self.data_dir = data_dir
        self.session = session or requests.Session()
        self.timeout = timeout

    def _cache_path(self, season, week) -> Path:
        return self.data_dir / f"sleeper_stats_{season}_{week}.json"

    def weekly_stats(self, season, week) -> dict:
        """Raw stats blob for ``season``/``week``, cached on disk."""
        cache = self._cache_path(season, week)
        if cache.exists() and (time.time() - cache.stat().st_mtime) < STATS_CACHE_TTL:
            return json.loads(cache.read_text())
        resp = self.session.get(
            f"{BASE}/stats/nfl/regular/{season}/{week}", timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json() or {}
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data))
        return data

    def weekly_points(self, season, week, scoring: str) -> dict[str, float]:
        """``{sleeper_id: points}`` for the given week and scoring mode."""
        return parse_stats(self.weekly_stats(season, week), scoring)
