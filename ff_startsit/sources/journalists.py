"""Preferred-journalist rankings — a display-only view, not a blend signal.

Fetches each configured FantasyPros expert's individual weekly ranks (the
consensus-rankings machinery from ``ecr.py`` with a single-expert ``filters=``)
and averages them per player. The result renders as its own "Preferred
journalists" section in the digest/dashboard; it never enters the ensemble
blend, the weights, or the calibration loop.

Experts come from ``FF_PREFERRED_EXPERTS`` as ``id:Name`` pairs. Any expert
whose fetch fails is dropped from the average with a warning; if nothing at all
can be fetched the view is ``None`` and the section is simply omitted — a
broken journalist feed must never take down a publish run.
"""

from __future__ import annotations

import statistics
import sys
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

import requests

from ..data.matching import ExternalRow, match_rows
from ..models import Player
from .ecr import _canon_pos, _current_season, fetch_api_rows, fetch_scrape_rows

# Values of FF_PREFERRED_EXPERTS that disable the feature entirely.
_DISABLED = {"", "0", "false", "no", "off"}


@dataclass(frozen=True)
class Expert:
    """One journalist: FantasyPros expert id + the name shown in outputs."""

    id: str
    name: str


@dataclass
class JournalistRow:
    """One player's ranks across the preferred experts, plus their average."""

    player: Player
    avg_rank: float
    ranks: dict[str, Optional[float]] = field(default_factory=dict)  # expert id -> rank


@dataclass
class JournalistView:
    """Everything the renderers need for the Preferred journalists section."""

    experts: list[Expert]
    by_position: dict[str, list[JournalistRow]]  # position -> rows sorted by avg


def parse_experts(spec: str) -> list[Expert]:
    """Parse ``FF_PREFERRED_EXPERTS`` (``"id:Name,id:Name"``) into experts.

    A bare id gets the placeholder name ``Expert <id>``; malformed entries are
    warned about and skipped; a disabled/empty spec yields no experts.
    """
    if (spec or "").strip().lower() in _DISABLED:
        return []
    experts: list[Expert] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        expert_id, _, name = chunk.partition(":")
        expert_id = expert_id.strip()
        name = name.strip()
        if not expert_id.isdigit():
            print(f"warning: ignoring malformed FF_PREFERRED_EXPERTS entry "
                  f"{chunk!r} (expected id:Name).", file=sys.stderr)
            continue
        experts.append(Expert(id=expert_id, name=name or f"Expert {expert_id}"))
    return experts


class JournalistFetcher:
    """Fetches per-expert weekly ranks and builds the journalist view.

    Same constructor shape as ``ECRSignal`` (session injectable so tests stay
    offline); fetches are memoized per ``(position, week, expert_id)``.
    """

    def __init__(self, experts: Sequence[Expert], api_key: str = "",
                 scoring: str = "ppr", season: Optional[int] = None,
                 session: Optional[requests.Session] = None, timeout: int = 20):
        self.experts = list(experts)
        self.api_key = api_key
        self.scoring = scoring
        self.season = season or _current_season()
        self.session = session or requests.Session()
        self.timeout = timeout
        self._rows_cache: dict[tuple[str, int, str], list[ExternalRow]] = {}

    def build_view(self, players: Iterable[Player], week: int) -> Optional[JournalistView]:
        """Join each expert's ranks onto the roster and average per player.

        Returns ``None`` when no experts are configured or no expert produced
        any usable data (section omitted).
        """
        players = list(players)
        if not self.experts or not players:
            return None

        positions = sorted({_canon_pos(p.position) for p in players})
        # expert id -> {player.key -> rank}; experts with no data are dropped.
        ranks_by_expert: dict[str, dict[str, float]] = {}
        for expert in self.experts:
            rows: list[ExternalRow] = []
            for pos in positions:
                rows.extend(self._rows_for(pos, week, expert.id))
            if not rows:
                print(f"warning: no rankings from preferred journalist "
                      f"{expert.name} (id {expert.id}); skipping.", file=sys.stderr)
                continue
            matched = match_rows(players, rows).matched
            ranks_by_expert[expert.id] = {key: row.value for key, row in matched.items()}

        if not ranks_by_expert:
            return None
        self._warn_if_filter_ignored(ranks_by_expert)

        by_position: dict[str, list[JournalistRow]] = {}
        for p in players:
            per_expert = {e.id: ranks_by_expert.get(e.id, {}).get(p.key)
                          for e in self.experts if e.id in ranks_by_expert}
            known = [r for r in per_expert.values() if r is not None]
            if not known:
                continue  # bye / unranked everywhere -> leave the player out
            row = JournalistRow(player=p, avg_rank=statistics.mean(known),
                                ranks=per_expert)
            by_position.setdefault(p.position, []).append(row)

        if not by_position:
            return None
        for rows_ in by_position.values():
            rows_.sort(key=lambda r: r.avg_rank)
        experts = [e for e in self.experts if e.id in ranks_by_expert]
        return JournalistView(experts=experts, by_position=by_position)

    def _rows_for(self, position: str, week: int, expert_id: str) -> list[ExternalRow]:
        cache_key = (position, week, expert_id)
        if cache_key in self._rows_cache:
            return self._rows_cache[cache_key]
        rows = self._rows_uncached(position, week, expert_id)
        self._rows_cache[cache_key] = rows
        return rows

    def _rows_uncached(self, position: str, week: int, expert_id: str) -> list[ExternalRow]:
        if self.api_key:
            try:
                rows = fetch_api_rows(self.session, self.api_key, self.season,
                                      self.scoring, position, week,
                                      timeout=self.timeout, filters=expert_id)
                if rows:
                    return rows
            except (requests.RequestException, ValueError):
                pass  # fall through to scrape, same as ECRSignal
        try:
            return fetch_scrape_rows(self.session, self.scoring, position,
                                     timeout=self.timeout, filters=expert_id)
        except requests.RequestException:
            return []

    def _warn_if_filter_ignored(self, ranks_by_expert: dict[str, dict[str, float]]) -> None:
        """Warn when every expert returned identical ranks for 2+ experts.

        That pattern usually means FantasyPros ignored the ``filters=`` ids
        (wrong/stale expert ids) and served plain consensus for each request —
        the section would silently show consensus, not the journalists.
        """
        distinct = {tuple(sorted(r.items())) for r in ranks_by_expert.values()}
        if len(ranks_by_expert) >= 2 and len(distinct) == 1:
            print("warning: all preferred journalists returned identical ranks — "
                  "the expert-id filter may be ignored; verify the ids in "
                  "FF_PREFERRED_EXPERTS.", file=sys.stderr)
