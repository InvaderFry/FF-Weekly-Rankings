"""NFL team-abbreviation normalization across data sources.

Sleeper and FantasyPros use abbreviations (KC, JAX, ...); The Odds API uses full
names ("Kansas City Chiefs"). Everything in the app is normalized to the Sleeper
abbreviation set so ``Player.team`` and ``Game`` line up.
"""

from __future__ import annotations

from typing import Optional

# Canonical abbreviation -> full team name (The Odds API style).
TEAM_FULL_NAMES = {
    "ARI": "Arizona Cardinals",
    "ATL": "Atlanta Falcons",
    "BAL": "Baltimore Ravens",
    "BUF": "Buffalo Bills",
    "CAR": "Carolina Panthers",
    "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals",
    "CLE": "Cleveland Browns",
    "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos",
    "DET": "Detroit Lions",
    "GB": "Green Bay Packers",
    "HOU": "Houston Texans",
    "IND": "Indianapolis Colts",
    "JAX": "Jacksonville Jaguars",
    "KC": "Kansas City Chiefs",
    "LV": "Las Vegas Raiders",
    "LAC": "Los Angeles Chargers",
    "LAR": "Los Angeles Rams",
    "MIA": "Miami Dolphins",
    "MIN": "Minnesota Vikings",
    "NE": "New England Patriots",
    "NO": "New Orleans Saints",
    "NYG": "New York Giants",
    "NYJ": "New York Jets",
    "PHI": "Philadelphia Eagles",
    "PIT": "Pittsburgh Steelers",
    "SF": "San Francisco 49ers",
    "SEA": "Seattle Seahawks",
    "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans",
    "WAS": "Washington Commanders",
}

# Alternate abbreviations seen in the wild -> canonical.
_ABBREV_ALIASES = {
    "JAC": "JAX",
    "WSH": "WAS",
    "WFT": "WAS",
    "LA": "LAR",
    "STL": "LAR",
    "SD": "LAC",
    "OAK": "LV",
    "LVR": "LV",
    "GNB": "GB",
    "KAN": "KC",
    "NWE": "NE",
    "NOR": "NO",
    "SFO": "SF",
    "TAM": "TB",
    "ARZ": "ARI",
}

# Full name (lowercased) -> canonical abbreviation.
_FULL_NAME_TO_ABBREV = {name.lower(): abbr for abbr, name in TEAM_FULL_NAMES.items()}


#: Values sources put in a team field that name no team. FantasyPros stamps a
#: free agent ``FA``, which the loose match below would find inside "Atlanta
#: Falcons".
_NOT_A_TEAM = {"FA", "FREE AGENT"}


def normalize_team(value: Optional[str]) -> Optional[str]:
    """Return the canonical abbreviation for an abbreviation or full team name.

    The loose substring fallback answers only when exactly **one** team
    matches. It used to return the first match, so a bare city with two teams
    ("New York", "Los Angeles") or a fragment ("Bay") was silently one of
    them. ``None`` is the honest answer there: an unknown team drops a signal
    and re-weights the rest, while a wrong one presents another team's data
    as fact.
    """
    if not value:
        return None
    v = value.strip()
    upper = v.upper()
    if upper in _NOT_A_TEAM:
        return None
    if upper in TEAM_FULL_NAMES:
        return upper
    if upper in _ABBREV_ALIASES:
        return _ABBREV_ALIASES[upper]
    lower = v.lower()
    if lower in _FULL_NAME_TO_ABBREV:
        return _FULL_NAME_TO_ABBREV[lower]
    # Loose match on full names (handles minor punctuation/whitespace differences).
    matches = {abbr for full_lower, abbr in _FULL_NAME_TO_ABBREV.items()
               if full_lower in lower or lower in full_lower}
    return matches.pop() if len(matches) == 1 else None
