"""Season-long roster guards, separate from the weekly blend and its log.

Overall ROS ranks are an ordering, not prices or projected fantasy points.
Unknown, stale, wrong-season and wrong-format data cannot authorize a move.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Optional, Sequence

import requests

from ..data.matching import match_rows
from ..models import Player
from ..season import season_year
from ..sources.ecr import parse_api_response

_DATA = re.compile(r"var\s+ecrData\s*=\s*(\{.*?\})\s*;", re.DOTALL)
URLS = {
    "half": "https://www.fantasypros.com/nfl/rankings/ros-half-point-ppr-overall.php",
    "ppr": "https://www.fantasypros.com/nfl/rankings/ros-ppr-overall.php",
    "std": "https://www.fantasypros.com/nfl/rankings/ros-overall.php",
}


def parse_season_rows(html: str, scoring: str, now: datetime):
    match = _DATA.search(html)
    if not match:
        raise ValueError("no embedded ROS rankings")
    data = json.loads(match.group(1))
    if (str(data.get("year")) != str(season_year(now.date()))
            or data.get("ranking_type_name") != "ros"
            or data.get("position_id") != "ALL"
            or data.get("scoring") != {"half": "HALF", "ppr": "PPR", "std": "STD"}[scoring]):
        raise ValueError("ROS rankings have the wrong season, type or scoring")
    age = now.timestamp() - float(data.get("last_updated_ts", 0))
    if not math.isfinite(age) or not -86400 <= age <= 14 * 86400:
        raise ValueError("ROS rankings are stale or undated")
    rows = [r for r in parse_api_response(data) if math.isfinite(r.value) and r.value > 0]
    if not rows:
        raise ValueError("ROS rankings are empty")
    return rows


class SeasonValueProvider:
    def __init__(self, session: Optional[requests.Session] = None, timeout: int = 20):
        self.session = session or requests.Session()
        self.timeout = timeout

    def fetch(self, players: Sequence[Player], scoring: str) -> dict[str, float]:
        response = self.session.get(URLS[scoring], timeout=self.timeout,
                                    headers={"User-Agent": "Mozilla/5.0 (ff-startsit)"})
        response.raise_for_status()
        rows = parse_season_rows(response.text, scoring, datetime.now(timezone.utc))
        matches = match_rows(players, rows)
        return {key: row.value for key, row in matches.matched.items()}


def protected_rank(team_count: Optional[int]) -> int:
    # Keep at least a typical first eight rounds of value. A weekly bench slot
    # is not evidence that a drafted starter is disposable.
    return max(100, (team_count or 12) * 8)


def comparable_trade(send_rank: Optional[float], get_rank: Optional[float]) -> bool:
    if send_rank is None or get_rank is None:
        return False
    lo, hi = sorted((send_rank, get_rank))
    # Both a relative and absolute bound: no star-for-depth offers, and no
    # treating rank 200 vs 250 as equally valuable because the ratio is small.
    return lo > 0 and hi / lo <= 1.25 and hi - lo <= 12
