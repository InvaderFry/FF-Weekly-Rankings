"""``ffstartsit`` command-line entry point.

Subcommands:
  sync      pull and cache your roster (ESPN by default; Sleeper or manual too)
  rank      rank your players at a position for the week
  compare   head-to-head between two (or more) players, with close-call flag
  lineup    suggest the best starter at each standard position (stretch)

Roster source defaults to ESPN (FF_ROSTER_SOURCE), overridable per command with
--source {espn,sleeper,manual} plus --league / --team.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, Sequence

from . import report
from .config import Settings, load_settings
from .data.matching import normalize_name
from .models import Player
from .output import render
from .pipeline import recommend
from .roster.base import RosterError, RosterProvider
from .roster.espn import ESPNProvider
from .roster.manual import ManualProvider
from .roster.sleeper import SleeperClient, SleeperError, SleeperProvider


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 1

    settings = load_settings()
    try:
        return args.func(args, settings)
    except (RosterError, SleeperError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ffstartsit", description=__doc__)
    sub = parser.add_subparsers(dest="command")

    # Shared roster-source flags for every command.
    roster_parent = argparse.ArgumentParser(add_help=False)
    roster_parent.add_argument("--source", choices=["espn", "sleeper", "manual"],
                               default=None, help="roster source (default: FF_ROSTER_SOURCE)")
    roster_parent.add_argument("--league", default=None, help="override league id")
    roster_parent.add_argument("--team", default=None, help="override ESPN team id")

    # Ranking-backbone selector, shared by the scoring commands (not sync).
    ranking_parent = argparse.ArgumentParser(add_help=False)
    ranking_parent.add_argument("--ranking", choices=["fantasypros", "journalists"],
                                default=None,
                                help="consensus backbone (default: FF_RANKING_SOURCE)")

    p_sync = sub.add_parser("sync", parents=[roster_parent], help="pull and cache your roster")
    p_sync.set_defaults(func=cmd_sync)

    p_rank = sub.add_parser("rank", parents=[roster_parent, ranking_parent],
                            help="rank your players at a position")
    p_rank.add_argument("--pos", required=True, help="QB/RB/WR/TE/K/DEF")
    p_rank.add_argument("--week", type=int, default=None)
    p_rank.add_argument("--md", action="store_true", help="emit markdown instead of a table")
    p_rank.add_argument("--csv", type=Path, default=None, help="also write a CSV")
    p_rank.add_argument("--json", type=Path, default=None, help="also write JSON")
    p_rank.set_defaults(func=cmd_rank)

    p_cmp = sub.add_parser("compare", parents=[roster_parent, ranking_parent],
                           help="head-to-head between players")
    p_cmp.add_argument("players", nargs="+", help="player names (quote multi-word)")
    p_cmp.add_argument("--week", type=int, default=None)
    p_cmp.add_argument("--md", action="store_true", help="emit markdown instead of a table")
    p_cmp.set_defaults(func=cmd_compare)

    p_line = sub.add_parser("lineup", parents=[roster_parent, ranking_parent],
                            help="suggest best starter per slot")
    p_line.add_argument("--week", type=int, default=None)
    p_line.add_argument("--md", action="store_true", help="emit markdown instead of plain text")
    p_line.set_defaults(func=cmd_lineup)

    p_report = sub.add_parser("report", parents=[roster_parent, ranking_parent],
                              help="whole-roster markdown digest (lineup + all positions)")
    p_report.add_argument("--week", type=int, default=None)
    p_report.add_argument("--out", type=Path, default=None, help="write digest to a file too")
    p_report.set_defaults(func=cmd_report)

    return parser


# --- commands -------------------------------------------------------------
def cmd_sync(args, settings: Settings) -> int:
    provider = build_roster_provider(settings, args.source, args.league, args.team)
    players = provider.get_roster_players()
    path = _save_roster(settings, provider, players)
    print(f"Synced {len(players)} players ({provider.name}) to {path}")
    for p in sorted(players, key=lambda x: (x.position, x.name)):
        print(f"  {p.position:4} {p.name:24} {p.team or 'BYE'}")
    return 0


def cmd_rank(args, settings: Settings) -> int:
    _apply_ranking(args, settings)
    players = _get_roster(args, settings)
    pos = args.pos.upper()
    pos = "DEF" if pos == "DST" else pos
    candidates = [p for p in players if p.position == pos]
    if not candidates:
        print(f"No {pos} players on your roster. Run `ffstartsit sync` first?", file=sys.stderr)
        return 1

    week = _resolve_week(args, settings)
    rec = recommend(settings, candidates, week, command=f"rank --pos {pos}")
    title = f"Week {week} {pos} • {settings.scoring.upper()} • {settings.ranking_source}"
    if args.md:
        print(render.render_markdown(rec, title=title))
    else:
        render.render_table(rec, title=title)
    if args.csv:
        render.export_csv(rec, args.csv)
        print(f"Wrote {args.csv}")
    if args.json:
        render.export_json(rec, args.json)
        print(f"Wrote {args.json}")
    return 0


def cmd_compare(args, settings: Settings) -> int:
    _apply_ranking(args, settings)
    players = _get_roster(args, settings)
    candidates = _resolve_named(players, args.players)
    if len(candidates) < 2:
        print("Need at least two matching players to compare.", file=sys.stderr)
        return 1

    week = _resolve_week(args, settings)
    rec = recommend(settings, candidates, week, command="compare")
    title = f"Week {week} compare • {settings.scoring.upper()} • {settings.ranking_source}"
    if args.md:
        print(render.render_markdown(rec, title=title))
    else:
        render.render_table(rec, title=title)
    return 0


def cmd_lineup(args, settings: Settings) -> int:
    _apply_ranking(args, settings)
    players = _get_roster(args, settings)
    week = _resolve_week(args, settings)

    recs = report.rank_each_position(settings, players, week)
    lineup = report.build_lineup(report.scored(recs))

    if args.md:
        lines = [f"### Suggested Week {week} lineup ({settings.scoring.upper()})",
                 "", "| Slot | Player | Team | Score |", "|---|---|---|---|"]
        for slot, pick in lineup:
            if pick is None:
                lines.append(f"| {slot} | _(no option)_ | | |")
            else:
                lines.append(f"| {slot} | {pick.player.name} | {pick.player.team or 'BYE'} "
                             f"| {pick.final:.1f} |")
        print("\n".join(lines))
        return 0

    print(f"Suggested Week {week} lineup ({settings.scoring.upper()}):")
    for slot, pick in lineup:
        if pick is None:
            print(f"  {slot:5} (no option)")
        else:
            print(f"  {slot:5} {pick.player.name:24} {pick.player.team or 'BYE':4} {pick.final:.1f}")
    return 0


def cmd_report(args, settings: Settings) -> int:
    _apply_ranking(args, settings)
    players = _get_roster(args, settings)
    week = _resolve_week(args, settings)
    digest = report.build_digest(settings, players, week)
    print(digest)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(digest)
    return 0


# --- helpers --------------------------------------------------------------
def _apply_ranking(args, settings: Settings) -> None:
    """Let --ranking override the configured backbone for this invocation."""
    if getattr(args, "ranking", None):
        settings.ranking_source = args.ranking


def _resolve_named(players: Sequence[Player], names: Sequence[str]) -> list[Player]:
    wanted = [normalize_name(n) for n in names]
    out: list[Player] = []
    for w in wanted:
        match = next((p for p in players if normalize_name(p.name) == w), None)
        if match is None:
            match = next((p for p in players if w in normalize_name(p.name)), None)
        if match is None:
            print(f"warning: no roster player matches {w!r}", file=sys.stderr)
        elif match not in out:
            out.append(match)
    return out


# --- roster providers -----------------------------------------------------
def build_roster_provider(settings: Settings, source: Optional[str] = None,
                          league: Optional[str] = None,
                          team: Optional[str] = None) -> RosterProvider:
    """Pick a roster provider: explicit --source > FF_ROSTER_SOURCE > espn."""
    source = (source or settings.roster_source or "espn").lower()
    if source == "espn":
        return ESPNProvider(
            league_id=league or settings.espn_league_id,
            season=_current_season(settings),
            team_id=team or settings.espn_team_id,
            espn_s2=settings.espn_s2,
            swid=settings.espn_swid,
        )
    if source == "sleeper":
        return SleeperProvider(
            username=settings.sleeper_username,
            league_id=league or settings.sleeper_league_id,
            data_dir=settings.data_dir,
        )
    if source == "manual":
        return ManualProvider(settings.manual_roster_file)
    raise RosterError(f"unknown roster source: {source!r}")


def _get_roster(args, settings: Settings) -> list[Player]:
    """Load the roster from cache, fetching (and caching) on a miss."""
    provider = build_roster_provider(settings, args.source, args.league, args.team)
    path = _roster_path(settings, provider)
    if path.exists():
        data = json.loads(path.read_text())
        return [Player(**row) for row in data]
    players = provider.get_roster_players()
    _save_roster(settings, provider, players)
    return players


def _roster_path(settings: Settings, provider: RosterProvider) -> Path:
    return settings.data_dir / f"roster_{provider.cache_tag()}.json"


def _save_roster(settings: Settings, provider: RosterProvider,
                 players: Sequence[Player]) -> Path:
    path = _roster_path(settings, provider)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([p.__dict__ for p in players], indent=2))
    return path


# --- week / season resolution (league-agnostic) ---------------------------
def _resolve_week(args, settings: Settings) -> int:
    if getattr(args, "week", None):
        return args.week
    try:
        # Sleeper's /state/nfl is free, needs no auth, and is league-agnostic.
        return SleeperClient(settings.data_dir).current_week()
    except Exception:
        return _date_week()


def _current_season(settings: Settings) -> str:
    try:
        season = SleeperClient(settings.data_dir).current_season()
        if season:
            return season
    except Exception:
        pass
    return _date_season()


def _date_season() -> str:
    today = date.today()
    return str(today.year if today.month >= 3 else today.year - 1)


def _date_week() -> int:
    """Rough NFL week from today's date — only a fallback when /state/nfl fails."""
    today = date.today()
    year = int(_date_season())
    # Week 1 kicks off around the first Thursday of September.
    sept1 = date(year, 9, 1)
    first_thu = sept1 + timedelta(days=(3 - sept1.weekday()) % 7)
    if today < first_thu:
        return 1
    return max(1, min(18, (today - first_thu).days // 7 + 1))


if __name__ == "__main__":
    raise SystemExit(main())
