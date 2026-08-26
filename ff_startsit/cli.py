"""``ffstartsit`` command-line entry point.

Subcommands:
  sync      pull and cache your roster (ESPN by default; Sleeper or manual too)
  rank      rank your players at a position for the week
  compare   head-to-head between two (or more) players, with close-call flag
  lineup    suggest the best starter at each standard position (stretch)
  report    whole-roster markdown digest (lineup + all positions)
  journalists  your preferred journalists' ranks + average (display-only view)
  experts   find/check FantasyPros expert ids for FF_PREFERRED_EXPERTS
  dashboard build a static HTML dashboard (for GitHub Pages)
  notify    send the week's summary to a Discord webhook
  publish   one scoring pass -> digest + dashboard + Discord (used by the Action)
  calibrate learn blend weights from your logged decisions vs actual outcomes (#7)
  waivers   waiver-wire adds/drops + trade ideas (the Tuesday pass)

Roster source defaults to ESPN (FF_ROSTER_SOURCE), overridable per command with
--source {espn,sleeper,manual} plus --league / --team.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Iterable, Optional, Sequence

import requests

from . import report, season
from .config import LeagueProfile, Settings, load_settings
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
    except requests.RequestException as exc:
        # `sync` is the one command that bypasses `_get_roster`, so it has no
        # stale-cache fallback and would otherwise surface a raw traceback.
        print(f"error: network request failed: {exc}", file=sys.stderr)
        return 2


def _positive_int(value: str) -> int:
    """argparse type for the evidence floors: a count of at least 1."""
    try:
        count = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a whole number")
    if count < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1 (got {count})")
    return count


def _grid_step(value: str) -> float:
    """argparse type for --step: a grid resolution in (0, 1].

    Validated at parse time because the failure is otherwise both late and
    opaque — ``simplex`` computes ``1.0 / step`` only after the outcome joins
    have already gone to the network, so 0 surfaces as a ZeroDivisionError at
    the end of a slow run, and a negative step silently collapses the grid to
    the one-hot corners instead of erroring.
    """
    try:
        step = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number")
    if not 0 < step <= 1:
        raise argparse.ArgumentTypeError(
            f"step must be greater than 0 and at most 1 (got {step})")
    return step


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ffstartsit", description=__doc__)
    sub = parser.add_subparsers(dest="command")

    # Shared roster-source flags for every command.
    roster_parent = argparse.ArgumentParser(add_help=False)
    roster_parent.add_argument("--source", choices=["espn", "sleeper", "manual"],
                               default=None, help="roster source (default: FF_ROSTER_SOURCE)")
    roster_parent.add_argument("--league", default=None, help="override league id")
    roster_parent.add_argument("--team", default=None, help="override ESPN team id")
    roster_parent.add_argument("--league-name", dest="league_name", default=None,
                               help="select a configured league by name (see FF_LEAGUES)")
    roster_parent.add_argument("--refresh", action="store_true",
                               help="ignore the cached roster and re-fetch it")
    roster_parent.add_argument("--offline", action="store_true",
                               help="never fetch; use the cached roster at any age")

    p_sync = sub.add_parser("sync", parents=[roster_parent], help="pull and cache your roster")
    p_sync.set_defaults(func=cmd_sync)

    p_rank = sub.add_parser("rank", parents=[roster_parent], help="rank your players at a position")
    p_rank.add_argument("--pos", required=True, help="QB/RB/WR/TE/K/DEF")
    p_rank.add_argument("--week", type=int, default=None)
    p_rank.add_argument("--md", action="store_true", help="emit markdown instead of a table")
    p_rank.add_argument("--csv", type=Path, default=None, help="also write a CSV")
    p_rank.add_argument("--json", type=Path, default=None, help="also write JSON")
    p_rank.set_defaults(func=cmd_rank)

    p_cmp = sub.add_parser("compare", parents=[roster_parent], help="head-to-head between players")
    p_cmp.add_argument("players", nargs="+", help="player names (quote multi-word)")
    p_cmp.add_argument("--week", type=int, default=None)
    p_cmp.add_argument("--md", action="store_true", help="emit markdown instead of a table")
    p_cmp.set_defaults(func=cmd_compare)

    p_line = sub.add_parser("lineup", parents=[roster_parent], help="suggest best starter per slot")
    p_line.add_argument("--week", type=int, default=None)
    p_line.add_argument("--md", action="store_true", help="emit markdown instead of plain text")
    p_line.set_defaults(func=cmd_lineup)

    p_report = sub.add_parser("report", parents=[roster_parent],
                              help="whole-roster markdown digest (lineup + all positions)")
    p_report.add_argument("--week", type=int, default=None)
    p_report.add_argument("--out", type=Path, default=None, help="write digest to a file too")
    p_report.add_argument("--log", action="store_true",
                          help="append these decisions to the results log (feeds `calibrate`)")
    p_report.set_defaults(func=cmd_report)

    p_jour = sub.add_parser("journalists", parents=[roster_parent],
                            help="preferred journalists' ranks + average "
                                 "(FF_PREFERRED_EXPERTS; display-only)")
    p_jour.add_argument("--week", type=int, default=None)
    p_jour.set_defaults(func=cmd_journalists)

    # No roster_parent: this touches no league, only FantasyPros (same as
    # calibrate/backtest).
    p_exp = sub.add_parser("experts",
                           help="find/check FantasyPros expert ids for FF_PREFERRED_EXPERTS")
    p_exp.add_argument("names", nargs="*",
                       help='analyst names, e.g. "Justin Boone" "Dave Richard"')
    p_exp.add_argument("--list", action="store_true", dest="list_all",
                       help="dump every expert id the FantasyPros directory yields")
    p_exp.add_argument("--verify", action="store_true",
                       help="check the ids already in FF_PREFERRED_EXPERTS")
    p_exp.set_defaults(func=cmd_experts)

    p_dash = sub.add_parser("dashboard", parents=[roster_parent],
                            help="build a static HTML dashboard (for GitHub Pages)")
    p_dash.add_argument("--week", type=int, default=None)
    p_dash.add_argument("--out", type=Path, default=Path("site/index.html"),
                        help="output path (default: site/index.html)")
    p_dash.add_argument("--all-leagues", action="store_true", dest="all_leagues",
                        help="render every configured league into one dashboard")
    p_dash.set_defaults(func=cmd_dashboard)

    p_notify = sub.add_parser("notify", parents=[roster_parent],
                              help="send the week's summary to a Discord webhook")
    p_notify.add_argument("--week", type=int, default=None)
    p_notify.add_argument("--url", default=None, help="dashboard URL to link (or FF_DASHBOARD_URL)")
    p_notify.set_defaults(func=cmd_notify)

    p_pub = sub.add_parser("publish", parents=[roster_parent],
                           help="one scoring pass -> digest + dashboard + Discord")
    p_pub.add_argument("--week", type=int, default=None)
    p_pub.add_argument("--report", type=Path, default=None, help="write the markdown digest here")
    p_pub.add_argument("--dashboard", type=Path, default=None, help="write the HTML dashboard here")
    p_pub.add_argument("--discord", action="store_true", help="also send the Discord notification")
    p_pub.add_argument("--url", default=None, help="dashboard URL to link (or FF_DASHBOARD_URL)")
    p_pub.add_argument("--log", action="store_true",
                       help="append these decisions to the results log (feeds `calibrate`)")
    p_pub.add_argument("--all-leagues", action="store_true", dest="all_leagues",
                       help="one pass per configured league -> combined digest/dashboard/Discord")
    p_pub.set_defaults(func=cmd_publish)

    p_wai = sub.add_parser("waivers", parents=[roster_parent],
                           help="Tuesday waiver-wire adds/drops + trade ideas")
    p_wai.add_argument("--week", type=int, default=None)
    p_wai.add_argument("--report", type=Path, default=None,
                       help="write the markdown digest here")
    p_wai.add_argument("--dashboard", type=Path, default=None,
                       help="write the HTML waivers page here")
    p_wai.add_argument("--discord", action="store_true",
                       help="also send the Discord notification")
    p_wai.add_argument("--url", default=None,
                       help="dashboard URL to link (or FF_DASHBOARD_URL)")
    p_wai.add_argument("--all-leagues", action="store_true", dest="all_leagues",
                       help="one pass per configured league -> combined outputs")
    p_wai.add_argument("--limit", type=int, default=None,
                       help="free agents to consider per league (or FF_WAIVER_LIMIT)")
    p_wai.add_argument("--no-trades", action="store_true", dest="no_trades",
                       help="skip the trade-ideas section")
    p_wai.add_argument("--no-columns", action="store_true", dest="no_columns",
                       help="skip the CBS/Yahoo waiver-column quotes")
    p_wai.add_argument("--rehearse", action="store_true",
                       help="preseason dress rehearsal: score live data instead "
                            "of refusing (no-op once the season starts)")
    # Deliberately no --log: see cmd_waivers.
    p_wai.set_defaults(func=cmd_waivers)

    p_cal = sub.add_parser("calibrate",
                           help="learn blend weights from your logged decisions vs actual outcomes (#7)")
    p_cal.add_argument("--season", default=None, help="only use decisions from this season")
    p_cal.add_argument("--week", type=int, default=None, help="only use decisions from this week")
    p_cal.add_argument("--step", type=_grid_step, default=0.05,
                       help="weight grid resolution, in (0, 1] (default 0.05)")
    p_cal.add_argument("--min-pairs", type=_positive_int, default=30, dest="min_pairs",
                       help="minimum joined pairs required to trust/write a result (default 30)")
    p_cal.add_argument("--min-decisions", type=_positive_int, default=5, dest="min_decisions",
                       help="minimum joined decisions required to write (default 5)")
    p_cal.add_argument("--min-weeks", type=_positive_int, default=3, dest="min_weeks",
                       help="minimum distinct weeks required to write (default 3). Weights "
                            "fitted to one week are fitted to that week's slate, not to your "
                            "leagues")
    p_cal.add_argument("--log", type=Path, default=None, help="results log path (default: the cache log)")
    p_cal.add_argument("--write", action="store_true",
                       help="persist the learned weights so future runs auto-apply them")
    p_cal.set_defaults(func=cmd_calibrate)

    p_bt = sub.add_parser("backtest",
                          help="report how your logged picks actually did + close-call honesty (#7)")
    p_bt.add_argument("--season", default=None, help="only use decisions from this season")
    p_bt.add_argument("--week", type=int, default=None, help="only use decisions from this week")
    p_bt.add_argument("--log", type=Path, default=None, help="results log path (default: the cache log)")
    p_bt.set_defaults(func=cmd_backtest)

    return parser


# --- commands -------------------------------------------------------------
def cmd_sync(args, settings: Settings) -> int:
    profile = resolve_league(settings, getattr(args, "league_name", None))
    provider = build_roster_provider(settings, args.source, args.league, args.team,
                                     profile=profile)
    players = provider.get_roster_players()
    path = _save_roster(settings, provider, players)
    print(f"Synced {len(players)} players ({provider.name}) to {path}")
    for p in sorted(players, key=lambda x: (x.position, x.name)):
        print(f"  {p.position:4} {p.name:24} {p.team or 'BYE'}")
    return 0


def cmd_rank(args, settings: Settings) -> int:
    settings, profile = _league_context(args, settings)
    players = _get_roster(args, settings, profile)
    pos = args.pos.upper()
    pos = "DEF" if pos == "DST" else pos
    candidates = [p for p in players if p.position == pos]
    if not candidates:
        print(f"No {pos} players on your roster. Run `ffstartsit sync` first?", file=sys.stderr)
        return 1

    week = _resolve_week(args, settings)
    _print_preseason_banner(settings, md=args.md)
    rec = recommend(settings, candidates, week, command=f"rank --pos {pos}")
    title = _titled(f"Week {week} {pos} • {settings.scoring.upper()}", profile)
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


def _pos_list(positions: Iterable[str]) -> str:
    """Positions as a stable, readable list for error messages."""
    return "/".join(sorted(positions))


def cmd_compare(args, settings: Settings) -> int:
    settings, profile = _league_context(args, settings)
    players = _get_roster(args, settings, profile)
    candidates = _resolve_named(players, args.players)
    if len(candidates) < 2:
        print("Need at least two matching players to compare.", file=sys.stderr)
        return 1

    week = _resolve_week(args, settings)
    _print_preseason_banner(settings, md=args.md)

    positions = {p.position for p in candidates}
    note = None
    if len(positions) == 1:
        rec = recommend(settings, candidates, week, command="compare")
    elif positions <= set(report.FLEX_POSITIONS):
        # Per-position ECR ranks are not comparable — an RB1 and a WR1 both
        # normalize to 100 — so a mixed flex-eligible set has to be scored
        # against FantasyPros' cross-position FLEX list, exactly as the FLEX
        # slot is. Refuse rather than fall back: the positional blend would
        # answer the question with a number that does not mean what it says.
        from .pipeline import build_signals

        signals = build_signals(settings)
        rec, reason = report.rank_pooled(settings, candidates, week, signals,
                                         "compare:pooled")
        if rec is None:
            print(f"Can't compare {_pos_list(positions)} on this data: {reason}. "
                  "Per-position ranks aren't comparable across positions, so "
                  "there is no honest ranking to give here.", file=sys.stderr)
            return 1
        note = report.POOLED_COMPARE_NOTE
    else:
        print(f"Can't compare across {_pos_list(positions)}: a rank of 1 means "
              "something different in each position group, so the scores would "
              "not be comparable. Compare within one position, or among "
              "flex-eligible players (RB/WR/TE).", file=sys.stderr)
        return 1

    title = _titled(f"Week {week} compare • {settings.scoring.upper()}", profile)
    if args.md:
        print(render.render_markdown(rec, title=title))
        if note:
            print(f"\n_{note}_")
    else:
        render.render_table(rec, title=title)
        if note:
            print(f"note: {note}", file=sys.stderr)
    return 0


def cmd_lineup(args, settings: Settings) -> int:
    settings, profile = _league_context(args, settings)
    players = _get_roster(args, settings, profile)
    week = _resolve_week(args, settings)
    _print_preseason_banner(settings, md=args.md)

    ws = report.score_week(settings, players, week)
    recs = ws.recs
    lineup = report.lineup_from(ws)

    label = _league_label(profile)
    suffix = f" · {label}" if label else ""
    if args.md:
        lines = [f"### Suggested Week {week} lineup ({settings.scoring.upper()}){suffix}",
                 "", "| Slot | Player | Team | Score |", "|---|---|---|---|"]
        for slot, pick in lineup:
            if pick is None:
                lines.append(f"| {slot} | _(no option)_ | | |")
            else:
                lines.append(f"| {render.md_cell(slot)} "
                             f"| {render.md_cell(pick.player.name)} "
                             f"| {render.md_cell(pick.player.team or 'BYE')} "
                             f"| {pick.final:.1f} |")
        if lineup.caveat:
            lines += ["", f"> ⚠️ {lineup.caveat}"]
        print("\n".join(lines))
        return 0

    print(f"Suggested Week {week} lineup ({settings.scoring.upper()}){suffix}:")
    for slot, pick in lineup:
        if pick is None:
            print(f"  {slot:5} (no option)")
        else:
            print(f"  {slot:5} {pick.player.name:24} {pick.player.team or 'BYE':4} {pick.final:.1f}")
    if lineup.caveat:
        print(f"\n  ⚠️ {lineup.caveat}")
    return 0


def cmd_report(args, settings: Settings) -> int:
    settings, profile = _league_context(args, settings)
    players = _get_roster(args, settings, profile)
    week = _resolve_week(args, settings)
    digest = report.build_digest(settings, players, week, label=_league_label(profile),
                                 log=getattr(args, "log", False))
    print(digest)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(digest)
    return 0


def cmd_journalists(args, settings: Settings) -> int:
    """Print the preferred-journalists section on its own (quick id sanity check)."""
    if not settings.preferred_experts:
        print("FF_PREFERRED_EXPERTS is not set — see .env.example for the "
              "id:Name format and how to find FantasyPros expert ids.",
              file=sys.stderr)
        return 1
    settings, profile = _league_context(args, settings)
    players = _get_roster(args, settings, profile)
    week = _resolve_week(args, settings)
    view = report.build_journalist_view(settings, players, week)
    if view is None:
        print("No preferred-journalist rankings available (bad expert ids, "
              "offline, or no data for this week).", file=sys.stderr)
        return 1
    print(report.render_journalists_markdown(view))
    return 0


#: Shown whenever discovery can't resolve a name. The browser method works
#: regardless of markup changes, so it is always the fallback we point at.
_MANUAL_STEPS = """
Find it by hand (one analyst at a time — that's what makes the id unambiguous):
  1. Open https://www.fantasypros.com/nfl/rankings/ppr-rb.php
  2. Click "Pick Experts", deselect everyone, select ONLY that analyst, Apply.
  3. The URL now ends with &filters=NNNN — NNNN is their id.
  4. Repeat per analyst, then set:
     FF_PREFERRED_EXPERTS=1234:Justin Boone,120:Jamey Eisenberg,125:Dave Richard
"""


def cmd_experts(args, settings: Settings, finder=None, verifier=None) -> int:
    """Find FantasyPros expert ids by name, list them, or verify configured ones.

    A setup helper — it reads no roster, touches no blend weight, and writes
    nothing to the results log. ``finder``/``verifier`` are injectable so the
    tests stay offline, the same seam ``cmd_calibrate`` uses.
    """
    from .sources.experts import (ExpertFinder, format_env_line, verify_experts)
    from .sources.journalists import parse_experts

    if getattr(args, "verify", False):
        experts = parse_experts(settings.preferred_experts)
        if not experts:
            print("FF_PREFERRED_EXPERTS is not set — nothing to verify. Run "
                  "`ffstartsit experts \"Justin Boone\" ...` to find ids first.",
                  file=sys.stderr)
            return 1
        checks = (verifier or verify_experts)(experts, scoring=settings.scoring)
        return _print_expert_checks(checks)

    finder = finder or ExpertFinder()

    if getattr(args, "list_all", False):
        experts = finder.list_all()
        if not experts:
            print("Couldn't read the expert directory." + _MANUAL_STEPS,
                  file=sys.stderr)
            return 1
        _print_expert_table(experts, finder)
        print(f"\n{len(experts)} experts. Pick yours and set FF_PREFERRED_EXPERTS "
              "to the id:Name pairs.")
        return 0

    names = getattr(args, "names", None) or []
    if not names:
        print('Give at least one name, e.g.\n'
              '  ffstartsit experts "Justin Boone" "Jamey Eisenberg" "Dave Richard"\n'
              "or --list to dump them all, or --verify to check what you have.",
              file=sys.stderr)
        return 1

    found, missing = finder.find_all(names)
    if found:
        _print_expert_table(found, finder)
        print("\nPaste this into .env (and set the same value as the "
              "FF_PREFERRED_EXPERTS repo *variable* for the scheduled runs):\n")
        print(format_env_line(found))
        print("\nThen check them: ffstartsit experts --verify")
    if missing:
        print(f"\nCouldn't resolve: {', '.join(missing)}", file=sys.stderr)
        print(_MANUAL_STEPS, file=sys.stderr)
    # Any unresolved name means the printed line is incomplete — say so with the
    # exit code, but still print what was found: a partial answer beats none.
    return 0 if found and not missing else 1


def _print_expert_table(experts, finder=None) -> None:
    from rich.console import Console
    from rich.table import Table

    table = Table(title="FantasyPros experts")
    table.add_column("id", justify="right")
    table.add_column("name")
    if finder is not None and getattr(finder, "pages", None):
        table.add_column("found on")
    for e in experts:
        row = [e.id, e.name]
        if finder is not None and getattr(finder, "pages", None):
            row.append(finder.pages.get(e.name, ""))
        table.add_row(*row)
    Console().print(table)


def _print_expert_checks(checks) -> int:
    """Render verification results; non-zero exit if anything looks wrong."""
    from rich.console import Console
    from rich.table import Table

    table = Table(title="FF_PREFERRED_EXPERTS check")
    table.add_column("id", justify="right")
    table.add_column("name")
    table.add_column("rows", justify="right")
    table.add_column("verdict")
    for c in checks:
        verdict = "[green]ok[/green]" if c.ok else f"[red]{c.problem}[/red]"
        table.add_row(c.expert.id, c.expert.name, str(c.rows), verdict)
    Console().print(table)

    bad = [c for c in checks if not c.ok]
    if bad:
        print("\nAt least one id looks wrong.", file=sys.stderr)
        print(_MANUAL_STEPS, file=sys.stderr)
        return 1
    # Be precise about what this proved, and what it did not.
    print("\nAll ids return distinct rankings, so the filter is working and none "
          "of them is silently serving consensus.")
    print("Note this checks that each id is live and distinct — not that it "
          "belongs to the analyst you named it after. Only the per-expert page "
          "(the lookup above) ties a number to a name.")
    return 0


def cmd_dashboard(args, settings: Settings) -> int:
    from datetime import date

    from .output.html import build_dashboard_html, build_multi_dashboard_html

    if getattr(args, "all_leagues", False):
        week = _resolve_week(args, settings)
        bundles = _league_bundles(args, settings, week)
        if not bundles:
            print("No configured league could be scored.", file=sys.stderr)
            return 1
        html = build_multi_dashboard_html(week, bundles,
                                          generated_on=date.today().isoformat())
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(html)
        print(f"Wrote dashboard ({len(bundles)} leagues) to {args.out}")
        return 0

    settings, profile = _league_context(args, settings)
    players = _get_roster(args, settings, profile)
    week = _resolve_week(args, settings)
    ws = report.score_week(settings, players, week)
    recs = ws.recs
    lineup = report.lineup_from(ws)
    html = build_dashboard_html(week, settings.scoring, lineup, recs,
                                generated_on=date.today().isoformat(),
                                banner=season.preseason_banner(settings),
                                journalists=report.build_journalist_view(
                                    settings, players, week),
                                label=_league_label(profile))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html)
    print(f"Wrote dashboard to {args.out}")
    return 0


def cmd_notify(args, settings: Settings) -> int:
    from .output.discord import build_discord_payload, send_discord

    if not settings.discord_webhook_url:
        print("DISCORD_WEBHOOK_URL is not set — nothing to send.", file=sys.stderr)
        return 1

    settings, profile = _league_context(args, settings)
    players = _get_roster(args, settings, profile)
    week = _resolve_week(args, settings)
    ws = report.score_week(settings, players, week)
    recs = ws.recs
    lineup = report.lineup_from(ws)
    dashboard_url = args.url or settings.dashboard_url or None
    payload = build_discord_payload(week, settings.scoring, lineup, recs, dashboard_url,
                                    banner=season.preseason_banner(settings),
                                    commands_url=_commands_url(settings),
                                    label=_league_label(profile))
    try:
        send_discord(settings.discord_webhook_url, payload)
    except Exception as exc:
        # Same contract as `publish --discord`: a Discord hiccup is warned about,
        # never a traceback.
        print(f"warning: Discord notification failed: {exc}", file=sys.stderr)
        return 1
    print("Sent Discord notification.")
    return 0


def cmd_publish(args, settings: Settings) -> int:
    """One scoring pass -> markdown digest + HTML dashboard + Discord, as requested."""
    if getattr(args, "all_leagues", False):
        return _cmd_publish_all(args, settings)

    from datetime import date

    from .output.discord import build_discord_payload, send_discord
    from .output.html import build_dashboard_html

    settings, profile = _league_context(args, settings)
    label = _league_label(profile)
    players = _get_roster(args, settings, profile)
    week = _resolve_week(args, settings)
    banner = season.preseason_banner(settings)
    if banner:
        # Make the Action log say it too, not just the rendered outputs.
        print(f"warning: {banner}", file=sys.stderr)

    # The single scoring pass shared by every output.
    ws = report.score_week(settings, players, week,
                           log=getattr(args, "log", False))
    recs = ws.recs
    lineup = report.lineup_from(ws)
    # One journalist pass too, shared by digest and dashboard (display-only).
    journalists = report.build_journalist_view(settings, players, week)

    digest = report.render_digest(week, settings.scoring, recs, banner=banner,
                                  journalists=journalists, label=label,
                                  lineup=lineup)
    print(digest)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(digest)

    if args.dashboard:
        html = build_dashboard_html(week, settings.scoring, lineup, recs,
                                    generated_on=date.today().isoformat(),
                                    banner=banner,
                                    journalists=journalists, label=label)
        args.dashboard.parent.mkdir(parents=True, exist_ok=True)
        args.dashboard.write_text(html)
        print(f"Wrote dashboard to {args.dashboard}")

    if args.discord:
        if not settings.discord_webhook_url:
            print("DISCORD_WEBHOOK_URL is not set — skipping Discord.", file=sys.stderr)
        else:
            dashboard_url = args.url or settings.dashboard_url or None
            payload = build_discord_payload(week, settings.scoring, lineup, recs, dashboard_url,
                                            banner=banner,
                                            commands_url=_commands_url(settings), label=label)
            try:
                send_discord(settings.discord_webhook_url, payload)
                print("Sent Discord notification.")
            except Exception as exc:
                # A Discord hiccup must not sink the digest/dashboard the rest of
                # the workflow depends on — warn and carry on.
                print(f"warning: Discord notification failed: {exc}", file=sys.stderr)
    return 0


def _league_bundles(args, settings: Settings, week: int) -> list:
    """Score every configured league for the week, one bundle each.

    A league that fails (bad auth, unreachable) is skipped with a warning so the
    others still publish — graceful degradation, never a crash. Each league's
    own scoring is honored via a per-league Settings copy.
    """
    from dataclasses import replace

    from .report import LeagueBundle

    bundles: list = []
    for profile in settings.leagues:
        lsettings = settings
        if profile.scoring and profile.scoring != settings.scoring:
            lsettings = replace(settings, scoring=profile.scoring)
        try:
            players = _get_roster(args, lsettings, profile)
            ws = report.score_week(lsettings, players, week,
                                   log=getattr(args, "log", False))
            recs = ws.recs
            lineup = report.lineup_from(ws)
            journalists = report.build_journalist_view(lsettings, players, week)
        except (RosterError, SleeperError) as exc:
            print(f"warning: skipping league {profile.name!r}: {exc}", file=sys.stderr)
            continue
        bundles.append(LeagueBundle(
            label=profile.name, scoring=lsettings.scoring, recs=recs, lineup=lineup,
            banner=season.preseason_banner(lsettings), journalists=journalists))
    return bundles


def _cmd_publish_all(args, settings: Settings) -> int:
    """Publish every configured league into one combined digest/dashboard/Discord."""
    from datetime import date

    from .output.discord import build_multi_discord_payload, send_discord
    from .output.html import build_multi_dashboard_html

    week = _resolve_week(args, settings)
    bundles = _league_bundles(args, settings, week)
    if not bundles:
        print("No configured league could be scored.", file=sys.stderr)
        return 1

    digest = report.render_multi_digest(week, bundles)
    print(digest)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(digest)

    if args.dashboard:
        html = build_multi_dashboard_html(week, bundles,
                                          generated_on=date.today().isoformat())
        args.dashboard.parent.mkdir(parents=True, exist_ok=True)
        args.dashboard.write_text(html)
        print(f"Wrote dashboard ({len(bundles)} leagues) to {args.dashboard}")

    if args.discord:
        if not settings.discord_webhook_url:
            print("DISCORD_WEBHOOK_URL is not set — skipping Discord.", file=sys.stderr)
        else:
            dashboard_url = args.url or settings.dashboard_url or None
            payload = build_multi_discord_payload(
                week, bundles, dashboard_url=dashboard_url,
                commands_url=_commands_url(settings))
            try:
                send_discord(settings.discord_webhook_url, payload)
                print("Sent Discord notification.")
            except Exception as exc:
                print(f"warning: Discord notification failed: {exc}", file=sys.stderr)
    return 0


def _waiver_bundles(args, settings: Settings, week: int) -> list:
    """Build one WaiverBundle per configured league.

    Modeled on ``_league_bundles``: a league that fails is skipped with a
    warning so the others still report. The extra skip here is a roster source
    with no league behind it — a manual CSV has no free agents and no trade
    partners, and saying so beats an empty section.
    """
    from dataclasses import replace

    from .waivers.base import LeagueViewProvider
    from .waivers.build import build_bundle
    from .waivers.columns import ColumnFetcher

    profiles = (settings.leagues if getattr(args, "all_leagues", False)
                else [resolve_league(settings, getattr(args, "league_name", None))])

    limit = getattr(args, "limit", None) or settings.waiver_limit
    include_trades = settings.trade_suggestions and not getattr(args, "no_trades", False)
    include_columns = settings.column_scrape and not getattr(args, "no_columns", False)
    # One fetcher across every league: the writers publish one column a week, not
    # one per league, so re-fetching per league would be three needless requests
    # each and three copies of the same credits line.
    fetcher = ColumnFetcher() if include_columns else None

    bundles: list = []
    for profile in profiles:
        lsettings = settings
        if profile.scoring and profile.scoring != settings.scoring:
            lsettings = replace(settings, scoring=profile.scoring)
        try:
            provider = build_roster_provider(lsettings, args.source, args.league,
                                             args.team, profile=profile)
            if not isinstance(provider, LeagueViewProvider):
                print(f"warning: skipping league {profile.name!r}: the "
                      f"{provider.name!r} source can't see a free-agent pool or "
                      f"other teams.", file=sys.stderr)
                continue
            players = _get_roster(args, lsettings, profile)
            bundles.append(build_bundle(
                lsettings, _league_label(profile) or profile.name, provider,
                players, week, limit=limit,
                max_adds=lsettings.waiver_max_adds,
                max_trades=lsettings.max_trade_ideas,
                include_trades=include_trades,
                include_columns=include_columns,
                column_fetcher=fetcher,
                # None, not False, so an unset flag still lets build_bundle
                # detect the pre-kickoff window on its own.
                rehearse=getattr(args, "rehearse", False) or None,
            ))
        except (RosterError, SleeperError) as exc:
            print(f"warning: skipping league {profile.name!r}: {exc}", file=sys.stderr)
            continue
    return bundles


def cmd_waivers(args, settings: Settings) -> int:
    """Waiver-wire adds/drops + trade ideas -> digest, dashboard page, Discord.

    There is deliberately **no** ``--log`` flag. The #7 results log holds
    start/sit decisions the user acted on; a waiver row would pack a hundred
    players — most of them on other people's teams — into a single "decision"
    whose pairwise concordance means nothing, and would count toward the
    ``calibrate --write`` floors with evidence nobody acted on. The log is
    append-only, so there is no undoing it.
    """
    from datetime import date

    from .output.discord import build_waiver_payload, send_discord
    from .output.html import build_waivers_html
    from .waivers.render import render_waiver_digest

    week = _resolve_week(args, settings)
    banner = season.waiver_banner(rehearse=getattr(args, "rehearse", False))
    if banner:
        # Make the Action log say it too, not just the rendered outputs.
        print(f"warning: {banner}", file=sys.stderr)
    bundles = _waiver_bundles(args, settings, week)
    if not bundles:
        print("No configured league could be scored for waivers.", file=sys.stderr)
        return 1

    digest = render_waiver_digest(week, bundles)
    print(digest)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(digest)

    if args.dashboard:
        html = build_waivers_html(week, bundles,
                                  generated_on=date.today().isoformat())
        args.dashboard.parent.mkdir(parents=True, exist_ok=True)
        args.dashboard.write_text(html)
        print(f"Wrote waivers page ({len(bundles)} league(s)) to {args.dashboard}")

    if args.discord:
        if not settings.discord_webhook_url:
            print("DISCORD_WEBHOOK_URL is not set — skipping Discord.", file=sys.stderr)
        else:
            dashboard_url = args.url or settings.dashboard_url or None
            payload = build_waiver_payload(week, bundles,
                                           dashboard_url=dashboard_url,
                                           commands_url=_commands_url(settings))
            try:
                send_discord(settings.discord_webhook_url, payload)
                print("Sent Discord notification.")
            except Exception as exc:
                # Same contract as publish: a Discord hiccup never sinks the
                # digest and dashboard the rest of the workflow depends on.
                print(f"warning: Discord notification failed: {exc}", file=sys.stderr)
    return 0


def cmd_calibrate(args, settings: Settings, outcome_provider=None) -> int:
    """Join the decision log to actual outcomes and fit better blend weights (#7).

    ``outcome_provider`` is injectable so tests run fully offline; in normal use it
    defaults to the free Sleeper weekly-stats source.
    """
    from .calibrate import calibrate as run_calibrate
    from .calibrate import dedupe_decisions, load_decisions

    log_path = args.log or settings.results_log_path
    decisions = load_decisions(log_path, season=args.season, week=args.week)
    if not decisions:
        print(f"No logged decisions in {log_path}. Run some rank/compare passes first?",
              file=sys.stderr)
        return 1
    decisions = dedupe_decisions(decisions)

    provider = outcome_provider or _sleeper_outcome_provider(settings)
    result = run_calibrate(decisions, provider, base_weights=settings.weights,
                           step=args.step, min_pairs=args.min_pairs,
                           min_decisions=getattr(args, "min_decisions", 5),
                           min_weeks=getattr(args, "min_weeks", 3))
    _print_calibration(result)

    if not result.pairs_used:
        print("Could not join any logged decision to an actual outcome yet — "
              "outcomes post after games are played.", file=sys.stderr)
        return 1

    if args.write:
        if not result.enough_data:
            print("Not writing — the corpus is too thin to fit weights on: "
                  + "; ".join(result.shortfalls) + ".", file=sys.stderr)
            print("Weights fitted to one slate describe that slate, not your "
                  "leagues. Keep logging and try again in a week or two.",
                  file=sys.stderr)
            return 1
        if result.best_concordance <= result.current_concordance:
            print("Your current weights already match the best found — nothing to write.")
            return 0
        from .config import _validate_weights
        weights = _validate_weights(dict(result.best_weights), settings.weights)
        path = settings.learned_weights_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(weights, indent=2))
        print(f"\nWrote learned weights to {path} — future runs apply them automatically.")
    return 0


def _fmt_weights(weights, order: Sequence[str]) -> str:
    return "  ".join(f"{s}={weights.get(s, 0.0):.2f}" for s in order)


def _print_calibration(result) -> None:
    print(f"Calibration over {result.decisions_used} decision(s) across "
          f"{result.weeks_used} week(s), {result.pairs_used} comparable pair(s); "
          f"tuning {', '.join(result.signals) or '(none)'}.")
    if not result.pairs_used:
        return
    print(f"  current  weights: {_fmt_weights(result.current_weights, result.signals)}")
    print(f"           concordance {result.current_concordance:.3f}  "
          f"top-pick hit-rate {result.current_hit_rate:.3f}")
    print(f"  learned  weights: {_fmt_weights(result.best_weights, result.signals)}")
    print(f"           concordance {result.best_concordance:.3f}  "
          f"top-pick hit-rate {result.best_hit_rate:.3f}")
    gain = result.best_concordance - result.current_concordance
    if not result.enough_data:
        print(f"  note: thin sample — {result.pairs_used} pairs; treat as directional only.")
    elif gain <= 0:
        print("  note: current weights are already as good as anything on the grid.")
    else:
        print(f"  note: +{gain:.3f} concordance available. Re-run with --write to apply.")


def _sleeper_outcome_provider(settings: Settings):
    """Build the default (Sleeper) outcome lookup factory: (season, week, scoring)->fn."""
    from .calibrate.outcomes import SleeperStatsClient, build_outcome_lookup

    stats_client = SleeperStatsClient(settings.data_dir)
    meta_cache: dict[str, dict] = {}

    def provider(season: str, week: int, scoring: str):
        try:
            stats = stats_client.weekly_points(season, week, scoring)
        except Exception:
            return None
        if not stats:
            return None
        if "meta" not in meta_cache:
            try:
                meta_cache["meta"] = SleeperClient(settings.data_dir).load_player_metadata()
            except Exception:
                meta_cache["meta"] = {}
        return build_outcome_lookup(stats, meta_cache["meta"]).get

    return provider


def cmd_backtest(args, settings: Settings, outcome_provider=None) -> int:
    """Report how logged picks actually did, and whether close-call flags are honest.

    Unlike ``calibrate`` (which searches for better weights), this replays each
    decision under the weights it actually used. ``outcome_provider`` is injectable
    so tests run offline; it defaults to the free Sleeper weekly-stats source.
    """
    from .calibrate import backtest as run_backtest
    from .calibrate import dedupe_decisions, load_decisions

    log_path = args.log or settings.results_log_path
    decisions = load_decisions(log_path, season=args.season, week=args.week)
    if not decisions:
        print(f"No logged decisions in {log_path}. Run some rank/compare passes first?",
              file=sys.stderr)
        return 1
    decisions = dedupe_decisions(decisions)

    provider = outcome_provider or _sleeper_outcome_provider(settings)
    result = run_backtest(decisions, provider, base_weights=settings.weights)

    if not result.decisions_used:
        print("Could not join any logged decision to an actual outcome yet — "
              "outcomes post after games are played.", file=sys.stderr)
        return 1

    _print_backtest(result)
    return 0


def _print_backtest(result) -> None:
    print(f"Backtest over {result.decisions_used} evaluatable decision(s) "
          f"({result.candidates_joined} candidates joined to outcomes).")
    print(f"  top-pick hit-rate {result.hit_rate:.3f} "
          f"({result.hits}/{result.decisions_used})")
    print(f"  avg points left on bench per decision: {result.avg_points_lost:.2f}")
    print("  close-call honesty:")
    if result.confident_n:
        print(f"    confident picks : hit-rate {result.confident_hit_rate:.3f} "
              f"({result.confident_hits}/{result.confident_n})")
    else:
        print("    confident picks : (none)")
    if result.close_call_n:
        print(f"    close-call picks: hit-rate {result.close_call_hit_rate:.3f} "
              f"({result.close_call_hits}/{result.close_call_n})")
    else:
        print("    close-call picks: (none flagged)")
    if result.confident_n and result.close_call_n:
        gap = result.confident_hit_rate - result.close_call_hit_rate
        if gap > 0:
            print(f"    -> confident picks hit {gap:.3f} more often — the flag is "
                  "surfacing the genuinely close calls.")
        else:
            print("    -> flagged picks did not fare worse than confident ones on this "
                  "sample; treat as directional (thin data).")
    if len(result.weeks) > 1:
        print("  by week:")
        for wk in result.weeks:
            print(f"    {wk.season} wk{wk.week}: hit-rate {wk.hit_rate:.3f} "
                  f"({wk.hits}/{wk.decisions})")


# --- helpers --------------------------------------------------------------
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
                          team: Optional[str] = None,
                          profile: Optional[LeagueProfile] = None) -> RosterProvider:
    """Pick a roster provider.

    Precedence: explicit ``--source``/``--league``/``--team`` flags win, then the
    selected league ``profile`` (from ``--league-name``/config), then the flat
    ``FF_ROSTER_SOURCE``/``ESPN_*`` settings.
    """
    if profile is not None:
        source = source or profile.source
        league = league or profile.league_id or None
        team = team or profile.team_id or None
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


def resolve_league(settings: Settings, name: Optional[str] = None) -> LeagueProfile:
    """Resolve the league profile to act on.

    With ``name`` -> that configured league (error if unknown). Without a name ->
    ``settings.default_league`` if it exists, else the first configured league.
    ``settings.leagues`` is always non-empty (a synthesized "default" backs the
    flat-env single-league setup), so this never returns None.
    """
    from .config import _synthesized_default

    leagues = settings.leagues
    if not leagues:
        # Settings built directly (not via load_settings) — synthesize the same
        # single "default" profile load_settings would have from the flat env.
        leagues = [_synthesized_default(settings.roster_source, settings.espn_league_id,
                                        settings.espn_team_id, settings.sleeper_league_id)]
    if name:
        for p in leagues:
            if p.name.lower() == name.lower():
                return p
        known = ", ".join(p.name for p in leagues) or "(none)"
        raise RosterError(f"No configured league named {name!r}. Known leagues: {known}.")
    if settings.default_league:
        for p in leagues:
            if p.name.lower() == settings.default_league.lower():
                return p
    return leagues[0]


def _league_label(profile: LeagueProfile) -> str:
    """Output label for a league — empty for the synthesized single-league default
    so legacy setups render exactly as before."""
    return "" if profile.name == "default" else profile.name


def _titled(base: str, profile: LeagueProfile) -> str:
    """Append the league label to a table/section title when there is one."""
    label = _league_label(profile)
    return f"{base} · {label}" if label else base


def _league_context(args, settings: Settings) -> tuple[Settings, LeagueProfile]:
    """Resolve the selected league and apply its per-league scoring (if any).

    Returns a (possibly scoring-adjusted) Settings plus the profile, so the whole
    downstream pipeline — which reads ``settings.scoring`` — honors a league's
    scoring without threading it through every call site.
    """
    from dataclasses import replace

    profile = resolve_league(settings, getattr(args, "league_name", None))
    if profile.scoring and profile.scoring != settings.scoring:
        settings = replace(settings, scoring=profile.scoring)
    return settings, profile


def _read_roster_cache(path: Path) -> Optional[list[Player]]:
    """The cached roster whatever its age, or None (a miss, never an error).

    Age is ``_cache_is_fresh``'s job: the caller needs the contents even when
    stale, to fall back on if a fetch fails. A malformed file is treated as a
    miss rather than crashing every command until the user finds and deletes it.
    """
    if not path.exists():
        return None
    try:
        return [Player(**row) for row in json.loads(path.read_text())]
    except (ValueError, TypeError):
        print(f"warning: ignoring unreadable roster cache at {path}.", file=sys.stderr)
        return None


def _cache_is_fresh(path: Path, ttl: float) -> bool:
    """Whether the cache file is within its TTL. ``ttl <= 0`` disables expiry."""
    if ttl <= 0:
        return True
    try:
        return (time.time() - path.stat().st_mtime) < ttl
    except OSError:
        return False


def _get_roster(args, settings: Settings,
                profile: Optional[LeagueProfile] = None) -> list[Player]:
    """Load the roster from cache, fetching (and caching) on a miss or expiry.

    The TTL decides when to *prefer* a fetch, never when to fail. A stale cache
    still beats no lineup at all, so an expired entry is kept as a fallback and
    used (with a warning) if the fetch fails — expired ESPN cookies or a flaky
    connection shouldn't turn every command into an error when last night's
    roster is sitting on disk.
    """
    if profile is None:
        profile = resolve_league(settings, getattr(args, "league_name", None))
    provider = build_roster_provider(settings, args.source, args.league, args.team,
                                     profile=profile)
    path = _roster_path(settings, provider)
    refresh = getattr(args, "refresh", False)
    offline = getattr(args, "offline", False)
    if refresh and offline:
        raise RosterError("--refresh and --offline cannot be used together.")

    cached = _read_roster_cache(path)   # whatever is on disk, at any age
    if not refresh:
        if cached is not None and _cache_is_fresh(path, settings.roster_ttl):
            return cached
        if offline:
            # --offline means "don't go to the network", not "insist on fresh".
            if cached is not None:
                print(f"warning: using a stale cached roster from {path} "
                      "(--offline).", file=sys.stderr)
                return cached
            raise RosterError(
                f"No cached roster at {path} and --offline was given. "
                "Run `ffstartsit sync` (online) to populate it."
            )

    try:
        players = provider.get_roster_players()
    except (RosterError, SleeperError, requests.RequestException) as exc:
        if cached is None:
            raise
        print(f"warning: roster fetch failed ({exc}); falling back to the "
              f"cached roster at {path}.", file=sys.stderr)
        return cached
    _save_roster(settings, provider, players)
    return players


def _roster_path(settings: Settings, provider: RosterProvider) -> Path:
    return settings.data_dir / f"roster_{provider.cache_tag()}.json"


def _save_roster(settings: Settings, provider: RosterProvider,
                 players: Sequence[Player]) -> Path:
    """Write the roster cache atomically, so an interrupted run can't corrupt it."""
    path = _roster_path(settings, provider)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps([p.__dict__ for p in players], indent=2))
    os.replace(tmp, path)
    return path


def _print_preseason_banner(settings: Settings, md: bool = False) -> None:
    """Lead interactive/markdown output with the preseason warning, if any."""
    banner = season.preseason_banner(settings)
    if not banner:
        return
    if md:
        print(f"> {banner}\n")
    else:
        print(f"{banner}\n")


def _commands_url(settings: Settings) -> Optional[str]:
    """Where Discord readers go to actually use /lineup-style commands."""
    return f"{settings.repo_url}/issues" if settings.repo_url else None


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
    return season.date_season()


def _date_week() -> int:
    """Rough NFL week from today's date — only a fallback when /state/nfl fails."""
    return season.date_week()


if __name__ == "__main__":
    raise SystemExit(main())
