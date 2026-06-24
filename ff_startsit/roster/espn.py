"""ESPN fantasy roster provider (the new default source).

Pulls a single league via ESPN's (unofficial) read API. Private leagues need the
``espn_s2`` + ``SWID`` browser cookies; public leagues need neither but require an
explicit team id since there's no identity to match on. When a SWID is present we
auto-detect *your* team by matching it against each team's owners.

Player names/teams/positions come straight from the league response, so unlike
Sleeper there's no separate metadata blob to download. ``parse_roster`` is pure
(HTTP-free) for testing.
"""

from __future__ import annotations

from typing import Optional

import requests

from ..data.espn_maps import position_code, team_abbrev
from ..models import Player
from .base import RosterError, RosterProvider

BASE = ("https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/"
        "{season}/segments/0/leagues/{league_id}")


def _norm_swid(swid: Optional[str]) -> str:
    """Strip braces/whitespace and uppercase so SWID comparisons are robust."""
    if not swid:
        return ""
    return swid.strip().strip("{}").upper()


def _select_team(teams: list[dict], team_id: Optional[str], swid: Optional[str]) -> dict:
    norm = _norm_swid(swid)
    if norm:
        for t in teams:
            owners = [_norm_swid(o) for o in (t.get("owners") or [])]
            if norm in owners:
                return t
    if team_id not in (None, ""):
        try:
            wanted = int(team_id)
        except (TypeError, ValueError):
            wanted = None
        for t in teams:
            if t.get("id") == wanted:
                return t
        raise RosterError(f"ESPN team id {team_id!r} not found in this league.")
    if norm:
        raise RosterError(
            "Couldn't match your SWID to a team in this league. "
            "Double-check the cookie, or set ESPN_TEAM_ID."
        )
    raise RosterError(
        "ESPN public league needs a team: set ESPN_TEAM_ID or pass --team <id>."
    )


def parse_roster(payload: dict, *, team_id: Optional[str] = None,
                 swid: Optional[str] = None) -> list[Player]:
    """Extract one team's canonical roster from an mRoster+mTeam response."""
    teams = payload.get("teams") or []
    if not teams:
        raise RosterError("ESPN response had no teams (bad league id or auth?).")

    team = _select_team(teams, team_id, swid)
    entries = ((team.get("roster") or {}).get("entries")) or []

    players: list[Player] = []
    for entry in entries:
        info = (entry.get("playerPoolEntry") or {}).get("player") or {}
        pid = info.get("id")
        pos = position_code(info.get("defaultPositionId"))
        if pid is None or pos is None:
            continue  # unknown/unsupported slot (e.g. IDP) -> skip
        players.append(
            Player(
                key=f"espn-{pid}",
                name=info.get("fullName") or str(pid),
                team=team_abbrev(info.get("proTeamId")),
                position=pos,
            )
        )
    return players


class ESPNProvider(RosterProvider):
    name = "espn"

    def __init__(self, league_id: str, season: str, team_id: str = "",
                 espn_s2: str = "", swid: str = "",
                 session: Optional[requests.Session] = None, timeout: int = 30):
        if not league_id:
            raise RosterError("ESPN_LEAGUE_ID is not set (see .env.example).")
        self.league_id = league_id
        self.season = season
        self.team_id = team_id
        self.espn_s2 = espn_s2
        self.swid = swid
        self.session = session or requests.Session()
        self.timeout = timeout

    def cache_tag(self) -> str:
        return f"espn_{self.league_id}"

    def get_roster_players(self) -> list[Player]:
        payload = self._fetch()
        return parse_roster(payload, team_id=self.team_id, swid=self.swid)

    def _fetch(self) -> dict:
        cookies = {}
        if self.espn_s2 and self.swid:
            cookies = {"espn_s2": self.espn_s2, "SWID": self.swid}
        try:
            resp = self.session.get(
                BASE.format(season=self.season, league_id=self.league_id),
                params=[("view", "mRoster"), ("view", "mTeam")],
                cookies=cookies or None,
                headers={"User-Agent": "Mozilla/5.0 (ff-startsit)"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise RosterError(f"ESPN request failed: {exc}") from exc
        if resp.status_code in (401, 403):
            had_cookies = bool(self.espn_s2 and self.swid)
            if had_cookies:
                raise RosterError(
                    "ESPN denied access (401/403) despite cookies — they have "
                    "likely expired. Re-grab ESPN_S2 and ESPN_SWID from your "
                    "browser (DevTools -> Application -> Cookies) and update .env."
                )
            raise RosterError(
                "ESPN denied access (401/403). For a private league set ESPN_S2 "
                "and ESPN_SWID; for a public one the league must be viewable."
            )
        resp.raise_for_status()
        return resp.json()
