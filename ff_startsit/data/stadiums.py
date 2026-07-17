"""NFL stadium locations + roof type — the reference data the weather signal needs.

Keyed by the same canonical abbreviation set as ``data/teams.py`` (so it lines up
with ``Player.team``). Each entry carries the stadium's latitude/longitude (for the
Open-Meteo forecast lookup) and whether it is a dome or retractable/fixed-roof
venue. Roofed stadiums play in controlled conditions, so the weather signal scores
them neutral and never makes a network call for them.

Coordinates are approximate stadium centers — precise enough for a city-level
forecast. Home team is the lookup; a player's game is at their team's stadium.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Stadium:
    lat: float
    lon: float
    dome: bool  # True for dome / retractable / fixed-roof (weather-controlled)


# team abbrev -> Stadium. Retractable-roof and fixed-roof venues are flagged
# ``dome=True`` because start/sit weather effects don't apply under a roof.
STADIUMS: dict[str, Stadium] = {
    "ARI": Stadium(33.5277, -112.2626, dome=True),   # State Farm (retractable)
    "ATL": Stadium(33.7554, -84.4008, dome=True),    # Mercedes-Benz (retractable)
    "BAL": Stadium(39.2780, -76.6227, dome=False),
    "BUF": Stadium(42.7738, -78.7870, dome=False),
    "CAR": Stadium(35.2258, -80.8528, dome=False),
    "CHI": Stadium(41.8623, -87.6167, dome=False),
    "CIN": Stadium(39.0954, -84.5160, dome=False),
    "CLE": Stadium(41.5061, -81.6995, dome=False),
    "DAL": Stadium(32.7473, -97.0945, dome=True),    # AT&T (retractable)
    "DEN": Stadium(39.7439, -105.0201, dome=False),
    "DET": Stadium(42.3400, -83.0456, dome=True),    # Ford Field (dome)
    "GB": Stadium(44.5013, -88.0622, dome=False),
    "HOU": Stadium(29.6847, -95.4107, dome=True),    # NRG (retractable)
    "IND": Stadium(39.7601, -86.1639, dome=True),    # Lucas Oil (retractable)
    "JAX": Stadium(30.3239, -81.6373, dome=False),
    "KC": Stadium(39.0489, -94.4839, dome=False),
    "LV": Stadium(36.0909, -115.1833, dome=True),    # Allegiant (dome)
    "LAC": Stadium(33.9535, -118.3392, dome=True),   # SoFi (fixed roof)
    "LAR": Stadium(33.9535, -118.3392, dome=True),   # SoFi (fixed roof)
    "MIA": Stadium(25.9580, -80.2389, dome=False),
    "MIN": Stadium(44.9736, -93.2575, dome=True),    # U.S. Bank (fixed roof)
    "NE": Stadium(42.0909, -71.2643, dome=False),
    "NO": Stadium(29.9511, -90.0812, dome=True),     # Caesars Superdome (dome)
    "NYG": Stadium(40.8135, -74.0745, dome=False),   # MetLife
    "NYJ": Stadium(40.8135, -74.0745, dome=False),   # MetLife
    "PHI": Stadium(39.9008, -75.1675, dome=False),
    "PIT": Stadium(40.4468, -80.0158, dome=False),
    "SF": Stadium(37.4030, -121.9700, dome=False),   # Levi's
    "SEA": Stadium(47.5952, -122.3316, dome=False),
    "TB": Stadium(27.9759, -82.5033, dome=False),
    "TEN": Stadium(36.1665, -86.7713, dome=False),
    "WAS": Stadium(38.9077, -76.8645, dome=False),   # Northwest Stadium (Landover)
}
