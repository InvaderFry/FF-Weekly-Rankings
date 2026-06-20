"""ESPN fantasy numeric-id maps.

ESPN's roster payload identifies a player's NFL team and position with integer
ids, not abbreviations. These maps translate them to the canonical abbreviations
used everywhere else in the app (``data/teams.py``) and to our position codes.
"""

from __future__ import annotations

from typing import Optional

# ESPN proTeamId -> canonical abbreviation. 0 = free agent / no team.
PRO_TEAM_ID_TO_ABBREV = {
    0: None,
    1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN",
    8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV", 14: "LAR",
    15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG", 20: "NYJ", 21: "PHI",
    22: "ARI", 23: "PIT", 24: "LAC", 25: "SF", 26: "SEA", 27: "TB", 28: "WAS",
    29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU",
}

# ESPN defaultPositionId -> our position code.
POSITION_ID_TO_POS = {
    1: "QB",
    2: "RB",
    3: "WR",
    4: "TE",
    5: "K",
    16: "DEF",
}


def team_abbrev(pro_team_id: Optional[int]) -> Optional[str]:
    if pro_team_id is None:
        return None
    return PRO_TEAM_ID_TO_ABBREV.get(int(pro_team_id))


def position_code(position_id: Optional[int]) -> Optional[str]:
    if position_id is None:
        return None
    return POSITION_ID_TO_POS.get(int(position_id))
