"""``ffstartsit`` command-line entry point.

Subcommands:
  sync      cache your Sleeper roster + player metadata
  rank      rank your players at a position for the week
  compare   head-to-head between two (or more) players, with close-call flag
  lineup    suggest the best starter at each standard position (stretch)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from .config import Settings, load_settings
from .data.matching import normalize_name
from .models import Player
from .output import render
from .pipeline import recommend
from .roster.sleeper import SleeperClient, SleeperError

ROSTER_CACHE = "roster.json"
# Slots used by `lineup` (a common 1QB/PPR-ish starting set).
LINEUP_SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"]
FLEX_POSITIONS = {"RB", "WR", "TE"}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 1

    settings = load_settings()
    try:
        return args.func(args, settings)
    except SleeperError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ffstartsit", description=__doc__)
    sub = parser.add_subparsers(dest="command")

    p_sync = sub.add_parser("sync", help="cache your Sleeper roster")
    p_sync.set_defaults(func=cmd_sync)

    p_rank = sub.add_parser("rank", help="rank your players at a position")
    p_rank.add_argument("--pos", required=True, help="QB/RB/WR/TE/K/DEF")
    p_rank.add_argument("--week", type=int, default=None)
    p_rank.add_argument("--csv", type=Path, default=None, help="also write a CSV")
    p_rank.add_argument("--json", type=Path, default=None, help="also write JSON")
    p_rank.set_defaults(func=cmd_rank)

    p_cmp = sub.add_parser("compare", help="head-to-head between players")
    p_cmp.add_argument("players", nargs="+", help="player names (quote multi-word)")
    p_cmp.add_argument("--week", type=int, default=None)
    p_cmp.set_defaults(func=cmd_compare)

    p_line = sub.add_parser("lineup", help="suggest best starter per slot")
    p_line.add_argument("--week", type=int, default=None)
    p_line.set_defaults(func=cmd_lineup)

    return parser


# --- commands -------------------------------------------------------------
def cmd_sync(args, settings: Settings) -> int:
    _require_username(settings)
    client = SleeperClient(settings.data_dir)
    players = client.get_roster_players(settings.sleeper_username, settings.sleeper_league_id)
    _save_roster(settings, players)
    print(f"Synced {len(players)} players to {_roster_path(settings)}")
    for p in sorted(players, key=lambda x: (x.position, x.name)):
        print(f"  {p.position:4} {p.name:24} {p.team or 'BYE'}")
    return 0


def cmd_rank(args, settings: Settings) -> int:
    players = _load_or_sync_roster(settings)
    pos = args.pos.upper()
    pos = "DEF" if pos == "DST" else pos
    candidates = [p for p in players if p.position == pos]
    if not candidates:
        print(f"No {pos} players on your roster. Run `ffstartsit sync` first?", file=sys.stderr)
        return 1

    week = _resolve_week(args, settings)
    rec = recommend(settings, candidates, week, command=f"rank --pos {pos}")
    render.render_table(rec, title=f"Week {week} {pos} • {settings.scoring.upper()}")
    if args.csv:
        render.export_csv(rec, args.csv)
        print(f"Wrote {args.csv}")
    if args.json:
        render.export_json(rec, args.json)
        print(f"Wrote {args.json}")
    return 0


def cmd_compare(args, settings: Settings) -> int:
    players = _load_or_sync_roster(settings)
    candidates = _resolve_named(players, args.players)
    if len(candidates) < 2:
        print("Need at least two matching players to compare.", file=sys.stderr)
        return 1

    week = _resolve_week(args, settings)
    rec = recommend(settings, candidates, week, command="compare")
    render.render_table(rec, title=f"Week {week} compare • {settings.scoring.upper()}")
    return 0


def cmd_lineup(args, settings: Settings) -> int:
    players = _load_or_sync_roster(settings)
    week = _resolve_week(args, settings)

    # Score everyone once (per position frame), then greedily fill slots.
    by_pos: dict[str, list] = {}
    for pos in {p.position for p in players}:
        cands = [p for p in players if p.position == pos]
        rec = recommend(settings, cands, week, command="lineup", log=False)
        by_pos[pos] = [s for s in rec.scores if s.final is not None]

    used: set[str] = set()
    print(f"Suggested Week {week} lineup ({settings.scoring.upper()}):")
    for slot in LINEUP_SLOTS:
        pick = _best_for_slot(slot, by_pos, used)
        if pick is None:
            print(f"  {slot:5} (no option)")
            continue
        used.add(pick.player.key)
        print(f"  {slot:5} {pick.player.name:24} {pick.player.team or 'BYE':4} {pick.final:.1f}")
    return 0


# --- helpers --------------------------------------------------------------
def _best_for_slot(slot: str, by_pos: dict, used: set):
    positions = FLEX_POSITIONS if slot == "FLEX" else {slot}
    best = None
    for pos in positions:
        for s in by_pos.get(pos, []):
            if s.player.key in used:
                continue
            if best is None or (s.final or 0) > (best.final or 0):
                best = s
            break  # by_pos[pos] is already sorted; first unused is best for that pos
    return best


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


def _resolve_week(args, settings: Settings) -> int:
    if getattr(args, "week", None):
        return args.week
    try:
        return SleeperClient(settings.data_dir).current_week()
    except Exception:
        return 1


def _require_username(settings: Settings) -> None:
    if not settings.sleeper_username:
        raise SleeperError("SLEEPER_USERNAME is not set (see .env.example).")


def _roster_path(settings: Settings) -> Path:
    return settings.data_dir / ROSTER_CACHE


def _save_roster(settings: Settings, players: Sequence[Player]) -> None:
    path = _roster_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([p.__dict__ for p in players], indent=2))


def _load_or_sync_roster(settings: Settings) -> list[Player]:
    path = _roster_path(settings)
    if path.exists():
        data = json.loads(path.read_text())
        return [Player(**row) for row in data]
    _require_username(settings)
    client = SleeperClient(settings.data_dir)
    players = client.get_roster_players(settings.sleeper_username, settings.sleeper_league_id)
    _save_roster(settings, players)
    return players


if __name__ == "__main__":
    raise SystemExit(main())
