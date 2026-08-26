"""Sleeper league sync (free public API, no auth).

Resolves a username -> user id -> league -> roster -> players, returning canonical
``Player`` objects keyed by Sleeper player id. The big player-metadata blob
(~5MB) is cached on disk so we only download it once a day.

Parsing of the metadata blob into Players is separated from HTTP so it can be
tested against a small fixture.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Optional

import requests

from ..data.teams import normalize_team
from ..models import Player
from ..season import date_week
from ..waivers.base import LeagueViewProvider
from ..waivers.models import (ACQ_FAAB, ACQ_PRIORITY, ACQ_UNKNOWN, FantasyTeam,
                              LeagueRules, PoolPlayer)
from .base import RosterError, RosterProvider

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
        """The fantasy week, or the date's best guess when there isn't one yet.

        ``/state/nfl`` counts *preseason* weeks through August — ``season_type:
        "pre"`` with ``week: 3`` is the third preseason game, not Week 3 of the
        season — and reporting that as the fantasy week labels a whole report
        with a number off the wrong calendar. Only a regular-season reading is a
        real fantasy week; pre/post/off defer to ``season.date_week``, which
        returns 1 before kickoff and clamps at 18 after.
        """
        state = self._get("/state/nfl")
        if str(state.get("season_type", "")).lower() != "regular":
            return date_week()
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

    def all_rosters(self, league_id: str) -> list[dict]:
        """Every roster in the league — the raw blobs, for waivers and trades."""
        return self._get(f"/league/{league_id}/rosters") or []

    def league_users(self, league_id: str) -> dict[str, str]:
        """user_id -> display name, for naming trade partners."""
        users = self._get(f"/league/{league_id}/users") or []
        names: dict[str, str] = {}
        for u in users:
            uid = str(u.get("user_id") or "")
            if not uid:
                continue
            meta = u.get("metadata") or {}
            names[uid] = (meta.get("team_name") or u.get("display_name")
                          or f"Team {uid}")
        return names

    def league_settings(self, league_id: str) -> dict:
        return self._get(f"/league/{league_id}") or {}

    def trending_adds(self, lookback_hours: int = 24, limit: int = 50) -> dict[str, int]:
        """player_id -> add count over the lookback window (free, keyless)."""
        rows = self._get(
            f"/players/nfl/trending/add?lookback_hours={int(lookback_hours)}"
            f"&limit={int(limit)}"
        ) or []
        return {str(r.get("player_id")): int(r.get("count") or 0)
                for r in rows if r.get("player_id") is not None}

    @staticmethod
    def _owns(roster: dict, user_id: str) -> bool:
        return (roster.get("owner_id") == user_id
                or user_id in (roster.get("co_owners") or []))

    def roster_player_ids(self, league_id: str, user_id: str) -> list[str]:
        rosters = self.all_rosters(league_id)
        for r in rosters:
            if self._owns(r, user_id):
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


#: Sleeper's ``settings.waiver_type``: 2 means blind bidding (FAAB).
_SLEEPER_FAAB_TYPE = 2
#: Sort key for players Sleeper gives no ``search_rank`` — pushes them last
#: rather than first, which is what a falsy-default (0) would have done.
_UNRANKED = 10 ** 9


def parse_league_rules(league: dict) -> LeagueRules:
    """Read waiver type/budget and starting slots off a Sleeper league blob (pure)."""
    settings = (league or {}).get("settings") or {}
    budget = settings.get("waiver_budget")
    budget = float(budget) if isinstance(budget, (int, float)) else None
    waiver_type = settings.get("waiver_type")

    if waiver_type == _SLEEPER_FAAB_TYPE and budget is not None:
        acq = ACQ_FAAB
    elif waiver_type is not None:
        acq = ACQ_PRIORITY
    else:
        acq = ACQ_UNKNOWN

    slots: dict[str, int] = {}
    for pos in (league or {}).get("roster_positions") or []:
        pos = str(pos).upper()
        if pos in _KEEP_POSITIONS:
            slots[pos] = slots.get(pos, 0) + 1

    return LeagueRules(acquisition_type=acq, faab_budget=budget, roster_slots=slots)


def build_league_teams(rosters: list[dict], names: dict[str, str], meta: dict,
                       user_id: str) -> list[FantasyTeam]:
    """Turn Sleeper rosters + user names + metadata into FantasyTeams (pure)."""
    teams: list[FantasyTeam] = []
    for r in rosters:
        owner = str(r.get("owner_id") or "")
        rsettings = r.get("settings") or {}
        spent = rsettings.get("waiver_budget_used")
        teams.append(FantasyTeam(
            team_id=str(r.get("roster_id") or owner),
            name=names.get(owner) or f"Roster {r.get('roster_id')}",
            players=tuple(build_players([str(p) for p in (r.get("players") or [])], meta)),
            is_mine=SleeperClient._owns(r, user_id),
            faab_spent=float(spent) if isinstance(spent, (int, float)) else None,
            waiver_priority=rsettings.get("waiver_position"),
        ))
    return teams


def build_free_agents(rosters: list[dict], meta: dict, limit: int = 150,
                      trending: Optional[dict[str, int]] = None) -> list[PoolPlayer]:
    """Everyone in the player universe nobody rosters, best first (pure).

    Sleeper has no free-agent endpoint — the pool is the metadata blob minus
    every rostered id. That blob is ~11k players, most of them practice-squad
    linemen, so ``search_rank`` (Sleeper's own relevance ordering) plus ``limit``
    is what turns it into a list of players somebody might actually claim.
    """
    taken = {str(pid) for r in rosters for pid in (r.get("players") or [])}
    trending = trending or {}

    candidates: list[tuple[int, str, dict]] = []
    for pid, info in (meta or {}).items():
        pid = str(pid)
        if pid in taken or not isinstance(info, dict):
            continue
        if (info.get("position") or "").upper() not in _KEEP_POSITIONS:
            continue
        # ``active`` is absent for team defenses, so only reject an explicit False.
        if info.get("active") is False:
            continue
        rank = info.get("search_rank")
        candidates.append((int(rank) if isinstance(rank, int) else _UNRANKED, pid, info))

    candidates.sort(key=lambda c: (c[0], c[1]))
    top = candidates[:max(0, int(limit))]

    players = build_players([pid for _, pid, _ in top], meta)
    by_key = {p.key: p for p in players}
    pool: list[PoolPlayer] = []
    for _, pid, info in top:
        player = by_key.get(pid)
        if player is None:
            continue
        pool.append(PoolPlayer(
            player=player,
            trending_adds=trending.get(pid),
            injury_status=str(info.get("injury_status") or "").upper(),
        ))
    return pool


class SleeperProvider(RosterProvider, LeagueViewProvider):
    """Adapt the existing SleeperClient to the roster + league-view interfaces."""

    name = "sleeper"

    def __init__(self, username: str, league_id: str, data_dir: Path,
                 client: Optional[SleeperClient] = None):
        if not username:
            raise RosterError("SLEEPER_USERNAME is not set (see .env.example).")
        self.username = username
        self.league_id = league_id
        self.client = client or SleeperClient(data_dir)
        self._resolved: Optional[tuple[str, str]] = None
        self._rosters_cache: Optional[list[dict]] = None

    def cache_tag(self) -> str:
        # Username is part of the identity: one league holds many teams, and the
        # username is what selects which of them this roster is.
        return "_".join(p for p in ("sleeper", self.league_id, self.username) if p)

    def get_roster_players(self) -> list[Player]:
        return self.client.get_roster_players(self.username, self.league_id)

    # --- league view (waivers/trades) -------------------------------------
    def _resolve(self) -> tuple[str, str]:
        """(user_id, league_id), memoized so one waiver pass resolves once."""
        if self._resolved is None:
            season = self.client.current_season() or _fallback_season()
            user_id = self.client.resolve_user_id(self.username)
            league_id = self.client.pick_league(user_id, season, self.league_id)
            self._resolved = (user_id, league_id)
        return self._resolved

    def _rosters(self) -> list[dict]:
        if self._rosters_cache is None:
            _, league_id = self._resolve()
            self._rosters_cache = self.client.all_rosters(league_id)
        return self._rosters_cache

    def get_league_teams(self) -> list[FantasyTeam]:
        try:
            user_id, league_id = self._resolve()
            names = self.client.league_users(league_id)
            return build_league_teams(self._rosters(), names,
                                      self.client.load_player_metadata(), user_id)
        except (SleeperError, requests.RequestException) as exc:
            print(f"warning: Sleeper league teams unavailable: {exc}", file=sys.stderr)
            return []

    def get_league_rules(self) -> LeagueRules:
        try:
            _, league_id = self._resolve()
            return parse_league_rules(self.client.league_settings(league_id))
        except (SleeperError, requests.RequestException) as exc:
            print(f"warning: Sleeper league settings unavailable: {exc}", file=sys.stderr)
            return LeagueRules()

    def get_free_agents(self, week: int, limit: int = 150) -> list[PoolPlayer]:
        try:
            rosters = self._rosters()
            meta = self.client.load_player_metadata()
        except (SleeperError, requests.RequestException) as exc:
            print(f"warning: Sleeper free-agent list unavailable: {exc}", file=sys.stderr)
            return []
        try:
            trending = self.client.trending_adds()
        except requests.RequestException:
            trending = {}  # color only — never worth failing the pool for
        return build_free_agents(rosters, meta, limit=limit, trending=trending)


def _fallback_season() -> str:
    from datetime import datetime
    now = datetime.utcnow()
    return str(now.year if now.month >= 3 else now.year - 1)
