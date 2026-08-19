"""Assemble one league's waiver/trade report from its provider.

One pass per league: read the league view once, score every position once, and
hand the renderers a single ``WaiverBundle``. This is the waiver-side analogue of
``report.score_week`` + ``cli._league_bundles``, and it keeps the same promises:
a section whose data source is unreachable is omitted with a warning, and the
league still reports.
"""

from __future__ import annotations

import sys
from typing import Optional, Sequence

from ..config import Settings
from ..models import Player, PlayerScore
from ..pipeline import build_signals
from ..sources.schedule import ScheduleProvider
from .base import LeagueViewProvider, pool_players
from .columns import ColumnFetcher, index_mentions
from .models import LeagueRules, WaiverBundle
from .score import (BYE_HORIZON, bye_gaps, dedupe_players, droppable,
                    find_stashes, pick_adds, score_positions, team_players)
from .trades import suggest_trades

#: Margins are computed inside a position's own candidate set, so a 9-point WR
#: gain and a 9-point TE gain are not the same nine points. Said once, in the
#: report, rather than pretended away by sorting as if they were.
MARGIN_NOTE = ("Scores are normalized within each position's candidate set, so "
               "compare an add to the player it replaces — not to an add at "
               "another position.")
TRADE_NOTE = ("Trade ideas are built from this week's ensemble scores only — no "
              "rest-of-season projections, strength of schedule, or keeper "
              "value — and they value starting slots, not FLEX depth. Treat them "
              "as conversation starters.")


def journalist_ranks(settings: Settings, players: Sequence[Player],
                     week: int) -> dict[str, float]:
    """Flatten the preferred-journalists view to ``key -> average rank``.

    Reuses ``report.build_journalist_view`` unchanged, which already warns and
    returns None on any failure — Boone/Eisenberg/Richard reach the waiver report
    through exactly the same path they reach the start/sit report through.
    """
    from ..report import build_journalist_view

    view = build_journalist_view(settings, players, week)
    if view is None:
        return {}
    return {row.player.key: row.avg_rank
            for rows in view.by_position.values() for row in rows}


def _lineup_keys(my_scores: Sequence[PlayerScore]) -> set[str]:
    """Keys of players the lineup builder would start — never drop candidates.

    Uses ``report.build_lineup`` rather than a second definition of "starter", so
    the Tuesday report can't propose cutting somebody Sunday's report starts.
    """
    from ..report import build_lineup

    by_pos: dict[str, list[PlayerScore]] = {}
    for s in my_scores:
        by_pos.setdefault(s.player.position, []).append(s)
    for scores in by_pos.values():
        scores.sort(key=lambda s: (s.final is not None, s.final or 0), reverse=True)
    return {pick.player.key for _, pick in build_lineup(by_pos) if pick is not None}


def _weeks_playing(schedule: ScheduleProvider, week: int,
                   horizon: int = BYE_HORIZON) -> dict[int, set[str]]:
    """week -> teams with a game. A week we couldn't read is left out entirely,
    since an empty set would read as "everyone is on bye"."""
    out: dict[int, set[str]] = {}
    for ahead in range(0, horizon + 1):
        target = week + ahead
        try:
            games = schedule.for_week(target)
        except Exception:  # for_week already degrades; belt and braces
            continue
        if games:
            out[target] = set(games)
    return out


def build_bundle(settings: Settings, label: str, provider: LeagueViewProvider,
                 my_players: Sequence[Player], week: int, *,
                 signals: Optional[Sequence] = None,
                 schedule: Optional[ScheduleProvider] = None,
                 column_fetcher: Optional[ColumnFetcher] = None,
                 limit: int = 150, max_adds: int = 8, max_trades: int = 5,
                 include_trades: bool = True,
                 include_columns: bool = True) -> WaiverBundle:
    """Score one league's waiver wire and build its report."""
    rules = _rules(provider)
    teams = _teams(provider)
    pool = _pool(provider, week, limit)

    bundle = WaiverBundle(label=label, scoring=settings.scoring, week=week,
                          rules=rules, notes=[MARGIN_NOTE])

    if not pool:
        bundle.caveat = ("No free-agent list was available for this league, so "
                         "there are no add suggestions this week.")

    # One candidate set per position, spanning your roster, every other roster,
    # and the pool — the only way an add's score is comparable to a starter's.
    candidates = dedupe_players(my_players, team_players(teams, mine=False),
                                pool_players(pool))
    if not candidates:
        bundle.caveat = "This league returned no players to score."
        return bundle

    signals = list(signals) if signals is not None else build_signals(settings)
    _, index = score_positions(settings, candidates, week, signals=signals)

    my_scores = [index[p.key] for p in my_players if p.key in index]
    protected = _lineup_keys(my_scores)
    drops = droppable(my_scores, rules, protected=protected)

    ranks = journalist_ranks(settings, dedupe_players(pool_players(pool), my_players), week)

    mentions: dict[str, list] = {}
    if include_columns:
        fetcher = column_fetcher or ColumnFetcher()
        mentions = index_mentions(fetcher.fetch(week, pool_players(pool)))
        bundle.sources = list(fetcher.read)

    my_team = next((t for t in teams if t.is_mine), None)
    faab_left = rules.faab_remaining(my_team.faab_spent if my_team else None)

    bundle.adds = pick_adds(index, pool, drops, rules, faab_remaining=faab_left,
                            journalist_ranks=ranks, mentions=mentions,
                            max_adds=max_adds)
    bundle.drops = drops[:max_adds]

    schedule = schedule or ScheduleProvider(cache_dir=settings.data_dir)
    playing = _weeks_playing(schedule, week)
    bye_teams = _bye_teams(candidates, playing.get(week))

    taken = {t.score.player.key for t in bundle.adds}
    bundle.stashes = find_stashes(index, pool, taken, bye_teams)
    bundle.byes = bye_gaps(my_players, rules, week, playing)

    if include_trades:
        if my_team is None:
            bundle.notes.append(
                "Trade ideas need to know which team is yours — set ESPN_TEAM_ID "
                "or check your SWID cookie."
            )
        else:
            # Deliberately *not* passing ``protected``: a trade offer is drawn
            # only from surplus (players beyond a position's starting slots),
            # and that surplus is exactly what fills the FLEX. Protecting every
            # lineup slot left nothing tradeable but your 10th-best player,
            # which no rival wants — so no idea ever fired.
            bundle.trades = suggest_trades(teams, index, rules,
                                           max_ideas=max_trades)
            if bundle.trades:
                bundle.notes.append(TRADE_NOTE)
    return bundle


def _bye_teams(players: Sequence[Player], playing: Optional[set[str]]) -> set[str]:
    """NFL teams on bye this week, or an empty set when the schedule is unknown."""
    if not playing:
        return set()
    return {p.team for p in players if p.team and p.team not in playing}


def _rules(provider: LeagueViewProvider) -> LeagueRules:
    try:
        return provider.get_league_rules()
    except Exception as exc:
        print(f"warning: league rules unavailable: {exc}", file=sys.stderr)
        return LeagueRules()


def _teams(provider: LeagueViewProvider) -> list:
    try:
        return provider.get_league_teams()
    except Exception as exc:
        print(f"warning: league teams unavailable (no trade ideas): {exc}",
              file=sys.stderr)
        return []


def _pool(provider: LeagueViewProvider, week: int, limit: int) -> list:
    try:
        return provider.get_free_agents(week, limit=limit)
    except Exception as exc:
        print(f"warning: free-agent pool unavailable: {exc}", file=sys.stderr)
        return []
