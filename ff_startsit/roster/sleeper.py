"""Sleeper league sync (free public API, no auth).

Resolves a username -> user id -> league -> roster -> players, returning canonical
``Player`` objects keyed by Sleeper player id. The big player-metadata blob
(~5MB) is cached on disk so we only download it once a day.

Parsing of the metadata blob into Players is separated from HTTP so it can be
tested against a small fixture.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import requests

from ..data.teams import normalize_team
from ..models import Player

BASE = "https://api.sleeper.app/v1"
PLAYERS_CACHE_TTL = 24 * 3600  # seconds

# Fantasy-relevant positions we keep.
_KEEP_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF"}


class SleeperError(RuntimeError):
    pass


class SleeperClient:
    def __init__(self, data_dir: Path, session: Optional[requests.Session] = None,
                 timeout: int = 30):
        self.data_dir = data_dir
        self.session = session or requests.Session()
        self.timeout = timeout

    # --- HTTP helpers -----------------------------------------------------
    def _get(self, path: str):
        resp = self.session.get(f"{BASE}{path}", timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    # --- Public API -------------------------------------------------------
    def current_week(self) -> int:
        state = self._get("/state/nfl")
        # Off-season weeks report 0/1; default to 1 so the tool still runs.
        return int(state.get("week") or state.get("display_week") or 1) or 1

    def current_season(self) -> str:
        return str(self._get("/state/nfl").get("season", ""))

    def resolve_user_id(self, username: str) -> str:
        user = self._get(f"/user/{username}")
        if not user or not user.get("user_id"):
            raise SleeperError(f"Sleeper user not found: {username!r}")
        return user["user_id"]

    def pick_league(self, user_id: str, season: str, league_id: str = "") -> str:
        leagues = self._get(f"/user/{user_id}/leagues/nfl/{season}") or []
        if league_id:
            if any(l.get("league_id") == league_id for l in leagues):
                return league_id
            return league_id  # trust an explicit id even if not in the listing
        if not leagues:
            raise SleeperError(f"No NFL leagues for user in season {season}.")
        return leagues[0]["league_id"]

    def roster_player_ids(self, league_id: str, user_id: str) -> list[str]:
        rosters = self._get(f"/league/{league_id}/rosters") or []
        for r in rosters:
            if r.get("owner_id") == user_id or user_id in (r.get("co_owners") or []):
                return [str(pid) for pid in (r.get("players") or [])]
        raise SleeperError(f"User {user_id} has no roster in league {league_id}.")

    def load_player_metadata(self) -> dict:
        """Return the Sleeper player metadata blob, caching it on disk."""
        cache = self.data_dir / "sleeper_players.json"
        if cache.exists() and (time.time() - cache.stat().st_mtime) < PLAYERS_CACHE_TTL:
            return json.loads(cache.read_text())
        data = self._get("/players/nfl")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data))
        return data

    def get_roster_players(self, username: str, league_id: str = "") -> list[Player]:
        season = self.current_season() or _fallback_season()
        user_id = self.resolve_user_id(username)
        league = self.pick_league(user_id, season, league_id)
        player_ids = self.roster_player_ids(league, user_id)
        meta = self.load_player_metadata()
        return build_players(player_ids, meta)


def build_players(player_ids: list[str], meta: dict) -> list[Player]:
    """Turn Sleeper player ids + metadata into canonical Players (pure)."""
    players: list[Player] = []
    for pid in player_ids:
        info = meta.get(str(pid))
        if not info:
            continue
        position = (info.get("position") or "").upper()
        if position not in _KEEP_POSITIONS:
            continue
        if position == "DEF":
            # Team defenses use the team code as id/name.
            name = info.get("full_name") or f"{pid} D/ST"
            team = normalize_team(info.get("team") or pid)
        else:
            name = info.get("full_name") or " ".join(
                x for x in [info.get("first_name"), info.get("last_name")] if x
            )
            team = normalize_team(info.get("team"))
        players.append(Player(key=str(pid), name=name, team=team, position=position))
    return players


def _fallback_season() -> str:
    from datetime import datetime
    now = datetime.utcnow()
    return str(now.year if now.month >= 3 else now.year - 1)
