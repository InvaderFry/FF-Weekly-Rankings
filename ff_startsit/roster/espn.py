"""ESPN fantasy roster provider (the new default source).

Pulls a single league via ESPN's (unofficial) read API. Private leagues need the
``espn_s2`` + ``SWID`` browser cookies; public leagues need neither but require an
explicit team id since there's no identity to match on. When a SWID is present we
auto-detect *your* team by matching it against each team's owners.

Player names/teams/positions come straight from the league response, so unlike
Sleeper there's no separate metadata blob to download. ``parse_roster`` is pure
(HTTP-free) for testing.

The same league response already carries **every** team's roster and the
league's acquisition settings, so the waiver/trade pass (``waivers/``) reads its
trade partners and FAAB rules out of the request the start/sit pass was making
anyway — see ``parse_league_teams`` / ``parse_league_rules``. Only the free-agent
pool needs a second call, because ESPN keeps unrostered players behind a
different view (``kona_player_info``).
"""

from __future__ import annotations

import json
import sys
from typing import Any, Optional

import requests

from ..data.espn_maps import position_code, team_abbrev
from ..models import Player
from ..waivers.base import LeagueViewProvider
from ..waivers.models import (ACQ_FAAB, ACQ_PRIORITY, ACQ_UNKNOWN, FantasyTeam,
                              LeagueRules, PoolPlayer)
from .base import RosterError, RosterProvider

BASE = ("https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/"
        "{season}/segments/0/leagues/{league_id}")

#: Views requested on the one league call. ``mSettings`` rides along for free and
#: is what tells the waiver report FAAB from rolling priority.
LEAGUE_VIEWS = [("view", "mRoster"), ("view", "mTeam"), ("view", "mSettings")]

_UA = {"User-Agent": "Mozilla/5.0 (ff-startsit)"}

#: ESPN lineupSlotId -> our position code, for the free-agent filter and for
#: reading starting-slot counts out of ``mSettings``. Flex/bench/IR slots are
#: deliberately absent: they don't name a position.
SLOT_ID_TO_POS = {0: "QB", 2: "RB", 4: "WR", 6: "TE", 16: "DEF", 17: "K"}

#: The slots we ask the free-agent endpoint for — the positions this app ranks.
FREE_AGENT_SLOT_IDS = sorted(SLOT_ID_TO_POS)


def _norm_swid(swid: Optional[str]) -> str:
    """Strip braces/whitespace and uppercase so SWID comparisons are robust."""
    if not swid:
        return ""
    return swid.strip().strip("{}").upper()


def _select_team(teams: list[dict], team_id: Optional[str], swid: Optional[str]) -> dict:
    # An explicit team id wins over SWID auto-detection. ESPN_SWID is a global
    # account setting while team_id is per-league and per-flag, so checking SWID
    # first made `--team <other>` silently return your own roster in any league
    # you own a team in — despite the flag documenting itself as an override.
    norm = _norm_swid(swid)
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
        for t in teams:
            owners = [_norm_swid(o) for o in (t.get("owners") or [])]
            if norm in owners:
                return t
    if norm:
        raise RosterError(
            "Couldn't match your SWID to a team in this league. "
            "Double-check the cookie, or set ESPN_TEAM_ID."
        )
    raise RosterError(
        "ESPN public league needs a team: set ESPN_TEAM_ID or pass --team <id>."
    )


def _player_from_info(info: dict) -> Optional[Player]:
    """Build a canonical Player from an ESPN player blob, or None if unusable.

    Shared by the roster parse and the free-agent parse so both key players the
    same way (``espn-{id}``) — that identical keying is what lets a free agent
    and a rostered player land in one scoring candidate set.
    """
    pid = info.get("id")
    pos = position_code(info.get("defaultPositionId"))
    if pid is None or pos is None:
        return None  # unknown/unsupported slot (e.g. IDP) -> skip
    return Player(
        key=f"espn-{pid}",
        name=info.get("fullName") or str(pid),
        team=team_abbrev(info.get("proTeamId")),
        position=pos,
    )


def parse_team_players(team: dict) -> list[Player]:
    """Extract one team's canonical roster from its ``mRoster`` entry (pure)."""
    entries = ((team.get("roster") or {}).get("entries")) or []
    players: list[Player] = []
    for entry in entries:
        info = (entry.get("playerPoolEntry") or {}).get("player") or {}
        player = _player_from_info(info)
        if player is not None:
            players.append(player)
    return players


def parse_roster(payload: dict, *, team_id: Optional[str] = None,
                 swid: Optional[str] = None) -> list[Player]:
    """Extract one team's canonical roster from an mRoster+mTeam response."""
    teams = payload.get("teams") or []
    if not teams:
        raise RosterError("ESPN response had no teams (bad league id or auth?).")
    return parse_team_players(_select_team(teams, team_id, swid))


def _team_name(team: dict) -> str:
    """ESPN has moved this field around; try each shape before giving up."""
    name = (team.get("name") or "").strip()
    if name:
        return name
    parts = [str(team.get("location") or "").strip(),
             str(team.get("nickname") or "").strip()]
    joined = " ".join(p for p in parts if p)
    return joined or f"Team {team.get('id')}"


def parse_league_teams(payload: dict, *, team_id: Optional[str] = None,
                       swid: Optional[str] = None) -> list[FantasyTeam]:
    """Every team in the league, with ``is_mine`` on the one that's yours (pure).

    Unlike ``parse_roster`` this does **not** raise when your team can't be
    identified: the trade section is worth less without knowing which side is
    yours, but the rest of the report still stands, so we return the teams and
    let the caller notice that no team is marked mine.
    """
    teams = payload.get("teams") or []
    if not teams:
        return []
    try:
        mine = _select_team(teams, team_id, swid)
    except RosterError:
        mine = None
    mine_id = mine.get("id") if mine else None

    out: list[FantasyTeam] = []
    for team in teams:
        counter = team.get("transactionCounter") or {}
        spent = counter.get("acquisitionBudgetSpent")
        out.append(FantasyTeam(
            team_id=str(team.get("id")),
            name=_team_name(team),
            players=tuple(parse_team_players(team)),
            is_mine=team.get("id") == mine_id and mine_id is not None,
            faab_spent=float(spent) if isinstance(spent, (int, float)) else None,
            waiver_priority=team.get("waiverRank"),
        ))
    return out


def parse_league_rules(payload: dict) -> LeagueRules:
    """Read acquisition type / budget / starting slots out of ``mSettings``.

    Every field is optional on ESPN's side, and a wrong guess here is worse than
    no guess: a FAAB bid printed for a rolling-priority league is actively
    misleading. So anything we can't read leaves ``acquisition_type`` at
    ``"unknown"`` and the renderers omit the bid line entirely.
    """
    settings = payload.get("settings") or {}
    acq = settings.get("acquisitionSettings") or {}

    budget = acq.get("acquisitionBudget")
    budget = float(budget) if isinstance(budget, (int, float)) else None
    acq_type_raw = str(acq.get("acquisitionType") or "").upper()

    if acq.get("isUsingAcquisitionBudget") or "FAAB" in acq_type_raw or "BUDGET" in acq_type_raw:
        acq_type = ACQ_FAAB
    elif acq_type_raw or acq.get("waiverProcessDays"):
        acq_type = ACQ_PRIORITY
    else:
        acq_type = ACQ_UNKNOWN
    # A budget-less "FAAB" league is a contradiction we can't render a bid for.
    if acq_type == ACQ_FAAB and budget is None:
        acq_type = ACQ_PRIORITY

    slots: dict[str, int] = {}
    counts = ((settings.get("rosterSettings") or {}).get("lineupSlotCounts")) or {}
    for slot_id, count in counts.items():
        pos = SLOT_ID_TO_POS.get(int(slot_id)) if str(slot_id).lstrip("-").isdigit() else None
        if pos and count:
            slots[pos] = slots.get(pos, 0) + int(count)

    return LeagueRules(acquisition_type=acq_type, faab_budget=budget,
                       roster_slots=slots)


def parse_free_agents(payload: dict) -> list[PoolPlayer]:
    """Turn a ``kona_player_info`` response into an addable pool (pure).

    ESPN nests the player blob differently here than in ``mRoster``, and has
    shipped both shapes over time, so try each rather than assume one.
    """
    out: list[PoolPlayer] = []
    seen: set[str] = set()
    for entry in payload.get("players") or []:
        info = entry.get("player") or (entry.get("playerPoolEntry") or {}).get("player") or {}
        if not info and entry.get("defaultPositionId") is not None:
            info = entry  # already the player blob itself
        player = _player_from_info(info)
        if player is None or player.key in seen:
            continue
        seen.add(player.key)
        owned = (info.get("ownership") or {}).get("percentOwned")
        out.append(PoolPlayer(
            player=player,
            percent_owned=round(float(owned), 1) if isinstance(owned, (int, float)) else None,
            injury_status=str(info.get("injuryStatus") or "").upper(),
        ))
    return out


def free_agent_filter(week: int, limit: int) -> dict[str, Any]:
    """The ``x-fantasy-filter`` body for the free-agent query.

    Sorting by percent-owned descending is what keeps this to the players anyone
    might actually claim: unsorted, ``limit`` would slice an arbitrary few
    hundred out of the league's entire player database.
    """
    return {
        "players": {
            "filterStatus": {"value": ["FREEAGENT", "WAIVERS"]},
            "filterSlotIds": {"value": FREE_AGENT_SLOT_IDS},
            "limit": int(limit),
            "offset": 0,
            "sortPercOwned": {"sortAsc": False, "sortPriority": 1},
            "filterRanksForScoringPeriodIds": {"value": [int(week)]},
        }
    }


class ESPNProvider(RosterProvider, LeagueViewProvider):
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
        # The league call answers roster, teams and rules alike, so memoize it:
        # a waiver run asks all three and should still cost one request.
        self._payload: Optional[dict] = None

    def cache_tag(self) -> str:
        # Season and team belong in the key, not just the league: without them a
        # roster cached last season is served verbatim this season, and two
        # FF_LEAGUES profiles on one league with different team_ids collide on a
        # single file (so `--team 7` returns team 3's players from cache).
        return f"espn_{self.season}_{self.league_id}_{self.team_id or 'auto'}"

    def get_roster_players(self) -> list[Player]:
        payload = self._league_payload()
        return parse_roster(payload, team_id=self.team_id, swid=self.swid)

    # --- league view (waivers/trades) -------------------------------------
    def get_league_teams(self) -> list[FantasyTeam]:
        return parse_league_teams(self._league_payload(), team_id=self.team_id,
                                  swid=self.swid)

    def get_league_rules(self) -> LeagueRules:
        try:
            return parse_league_rules(self._league_payload())
        except RosterError as exc:
            # Rules are a nicety; the adds/drops still stand without them.
            print(f"warning: couldn't read ESPN league settings: {exc}", file=sys.stderr)
            return LeagueRules()

    def get_free_agents(self, week: int, limit: int = 150) -> list[PoolPlayer]:
        """Fetch the addable pool. Warns and returns ``[]`` on any failure."""
        try:
            payload = self._fetch(
                params={"view": "kona_player_info", "scoringPeriodId": int(week)},
                extra_headers={"x-fantasy-filter": json.dumps(free_agent_filter(week, limit))},
            )
        except (RosterError, requests.RequestException) as exc:
            print(f"warning: ESPN free-agent list unavailable: {exc}", file=sys.stderr)
            return []
        try:
            return parse_free_agents(payload)
        except (AttributeError, TypeError, ValueError) as exc:
            print(f"warning: couldn't parse ESPN free agents: {exc}", file=sys.stderr)
            return []

    # --- HTTP -------------------------------------------------------------
    def _league_payload(self) -> dict:
        if self._payload is None:
            self._payload = self._fetch(params=LEAGUE_VIEWS)
        return self._payload

    def _fetch(self, params, extra_headers: Optional[dict] = None) -> dict:
        cookies = {}
        if self.espn_s2 and self.swid:
            cookies = {"espn_s2": self.espn_s2, "SWID": self.swid}
        headers = dict(_UA)
        if extra_headers:
            headers.update(extra_headers)
        try:
            resp = self.session.get(
                BASE.format(season=self.season, league_id=self.league_id),
                params=params,
                cookies=cookies or None,
                headers=headers,
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
