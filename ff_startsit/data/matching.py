"""Match externally-sourced player rows (ECR) onto our canonical roster.

The riskiest glue in the app: a FantasyPros row like ("Patrick Mahomes II", "KC",
"QB") must land on the Sleeper player it describes. We match on a normalized name
+ position key and validate with team when both sides have one. Unmatched rows are
reported, never silently dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from .teams import normalize_team
from ..models import Player

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
_DROP = re.compile(r"[.']")          # collapse initials: "D.J." -> "DJ"
_PUNCT = re.compile(r"[^a-z0-9 ]")   # other punctuation -> space
_WS = re.compile(r"\s+")

#: Every spelling of the team-defense position seen across our sources: Sleeper,
#: ESPN and manual rosters say "DEF", FantasyPros says "DST". They are one
#: position and must produce one match key.
_DEF_POSITIONS = {"DEF", "DST", "D/ST", "DEFENSE"}

#: Name tokens that decorate a defense rather than identify it ("Chiefs D/ST").
#: Stripped before the remainder is resolved to a team abbreviation.
_DEF_NAME_TOKENS = {"d", "st", "dst", "def", "defense", "dst1"}


def normalize_name(name: str) -> str:
    """Lowercase, strip punctuation and generational suffixes, collapse spaces.

    Periods/apostrophes are removed (not spaced) so "D.J. Moore" and "DJ Moore"
    normalize identically; other punctuation becomes a space.
    """
    s = _PUNCT.sub(" ", _DROP.sub("", (name or "").lower()))
    tokens = [t for t in _WS.sub(" ", s).strip().split(" ") if t and t not in _SUFFIXES]
    return " ".join(tokens)


def is_defense(position: str) -> bool:
    """True for any of our sources' spellings of the team-defense position."""
    return (position or "").upper() in _DEF_POSITIONS


def _defense_key(name: str, team: Optional[str]) -> Optional[tuple[str, str]]:
    """Key a team defense by its canonical team abbreviation, or None.

    Defenses are named four incompatible ways across our sources — ESPN's
    "Chiefs D/ST", Sleeper's "Kansas City" (or "KC D/ST" when metadata is thin),
    FantasyPros' "Kansas City Chiefs", and manual rosters' free text. Matching
    those on the player name cannot work, so a defense is identified by the one
    thing every source agrees on: which team it is.
    """
    for candidate in (team, name):
        if not candidate:
            continue
        tokens = [t for t in normalize_name(candidate).split(" ")
                  if t and t not in _DEF_NAME_TOKENS]
        stripped = " ".join(tokens)
        # ``normalize_team`` falls back to a loose substring match, which a
        # one-character leftover matches arbitrarily ("a" -> ARI). Two is the
        # shortest real identifier ("KC"), so anything shorter is not a team.
        if len(stripped) < 2:
            continue
        abbrev = normalize_team(stripped)
        if abbrev:
            return (f"def:{abbrev}", "DEF")
    return None


def player_match_key(name: str, position: str,
                     team: Optional[str] = None) -> tuple[str, str]:
    """The (identity, position) pair a roster player and an external row join on.

    ``team`` is optional and consulted only for defenses, where it is the more
    reliable identifier; for every other position the name is the identity and
    team is left to ``_disambiguate`` as a tie-break.
    """
    pos = (position or "").upper()
    if is_defense(pos):
        return _defense_key(name, team) or (normalize_name(name), "DEF")
    return (normalize_name(name), pos)


@dataclass
class MatchResult:
    matched: dict[str, "ExternalRow"]   # player.key -> external row
    unmatched: list["ExternalRow"]      # external rows that found no roster player


@dataclass
class ExternalRow:
    """A position-ranked row from an external source (e.g. ECR)."""

    name: str
    team: Optional[str]
    position: str
    value: float                        # native value (e.g. ECR rank)
    extra: dict = None                  # source-specific payload

    def key(self) -> tuple[str, str]:
        return player_match_key(self.name, self.position, self.team)


def match_rows(players: Iterable[Player], rows: Iterable[ExternalRow]) -> MatchResult:
    """Attach external ``rows`` to roster ``players`` by (name, position).

    When multiple roster players share a name+position key (rare), team
    disambiguates. Rows with no roster match are returned in ``unmatched``.
    """
    players = list(players)
    # Index roster players by match key (may collide; keep a list).
    by_key: dict[tuple[str, str], list[Player]] = {}
    for p in players:
        by_key.setdefault(player_match_key(p.name, p.position, p.team), []).append(p)

    matched: dict[str, ExternalRow] = {}
    unmatched: list[ExternalRow] = []
    used: set[str] = set()

    for row in rows:
        candidates = by_key.get(row.key())
        if not candidates:
            unmatched.append(row)
            continue
        chosen = _disambiguate(candidates, row, used)
        if chosen is None:
            unmatched.append(row)
            continue
        matched[chosen.key] = row
        used.add(chosen.key)

    return MatchResult(matched=matched, unmatched=unmatched)


def _disambiguate(candidates: list[Player], row: ExternalRow, used: set[str]) -> Optional[Player]:
    free = [c for c in candidates if c.key not in used]
    if not free:
        return None
    if len(free) == 1:
        return free[0]
    row_team = normalize_team(row.team)
    if row_team:
        for c in free:
            if c.team == row_team:
                return c
    return free[0]
