"""``ffstartsit`` command-line entry point.

Subcommands:
  sync      pull and cache your roster (ESPN by default; Sleeper or manual too)
  rank      rank your players at a position for the week
  compare   head-to-head between two (or more) players, with close-call flag
  lineup    suggest the best starter at each standard position (stretch)
  report    whole-roster markdown digest (lineup + all positions)
  dashboard build a static HTML dashboard (for GitHub Pages)
  notify    send the week's summary to a Discord webhook
  publish   one scoring pass -> digest + dashboard + Discord (used by the Action)
  calibrate learn blend weights from your logged decisions vs actual outcomes (#7)

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
    p_report.set_defaults(func=cmd_report)

    p_dash = sub.add_parser("dashboard", parents=[roster_parent],
                            help="build a static HTML dashboard (for GitHub Pages)")
    p_dash.add_argument("--week", type=int, default=None)
    p_dash.add_argument("--out", type=Path, default=Path("site/index.html"),
                        help="output path (default: site/index.html)")
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
    p_pub.set_defaults(func=cmd_publish)

    p_cal = sub.add_parser("calibrate",
                           help="learn blend weights from your logged decisions vs actual outcomes (#7)")
    p_cal.add_argument("--season", default=None, help="only use decisions from this season")
    p_cal.add_argument("--week", type=int, default=None, help="only use decisions from this week")
    p_cal.add_argument("--step", type=float, default=0.05, help="weight grid resolution (default 0.05)")
    p_cal.add_argument("--min-pairs", type=int, default=30, dest="min_pairs",
                       help="minimum joined pairs required to trust/write a result (default 30)")
    p_cal.add_argument("--log", type=Path, default=None, help="results log path (default: the cache log)")
    p_cal.add_argument("--write", action="store_true",
                       help="persist the learned weights so future runs auto-apply them")
    p_cal.set_defaults(func=cmd_calibrate)

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
    players = _get_roster(args, settings)
    pos = args.pos.upper()
    pos = "DEF" if pos == "DST" else pos
    candidates = [p for p in players if p.position == pos]
    if not candidates:
        print(f"No {pos} players on your roster. Run `ffstartsit sync` first?", file=sys.stderr)
        return 1

    week = _resolve_week(args, settings)
    rec = recommend(settings, candidates, week, command=f"rank --pos {pos}")
    title = f"Week {week} {pos} • {settings.scoring.upper()}"
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
    players = _get_roster(args, settings)
    candidates = _resolve_named(players, args.players)
    if len(candidates) < 2:
        print("Need at least two matching players to compare.", file=sys.stderr)
        return 1

    week = _resolve_week(args, settings)
    rec = recommend(settings, candidates, week, command="compare")
    title = f"Week {week} compare • {settings.scoring.upper()}"
    if args.md:
        print(render.render_markdown(rec, title=title))
    else:
        render.render_table(rec, title=title)
    return 0


def cmd_lineup(args, settings: Settings) -> int:
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
    players = _get_roster(args, settings)
    week = _resolve_week(args, settings)
    digest = report.build_digest(settings, players, week)
    print(digest)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(digest)
    return 0


def cmd_dashboard(args, settings: Settings) -> int:
    from datetime import date

    from .output.html import build_dashboard_html

    players = _get_roster(args, settings)
    week = _resolve_week(args, settings)
    recs = report.rank_each_position(settings, players, week)
    lineup = report.build_lineup(report.scored(recs))
    html = build_dashboard_html(week, settings.scoring, lineup, recs,
                                generated_on=date.today().isoformat())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html)
    print(f"Wrote dashboard to {args.out}")
    return 0


def cmd_notify(args, settings: Settings) -> int:
    from .output.discord import build_discord_payload, send_discord

    if not settings.discord_webhook_url:
        print("DISCORD_WEBHOOK_URL is not set — nothing to send.", file=sys.stderr)
        return 1

    players = _get_roster(args, settings)
    week = _resolve_week(args, settings)
    recs = report.rank_each_position(settings, players, week)
    lineup = report.build_lineup(report.scored(recs))
    dashboard_url = args.url or settings.dashboard_url or None
    payload = build_discord_payload(week, settings.scoring, lineup, recs, dashboard_url)
    send_discord(settings.discord_webhook_url, payload)
    print("Sent Discord notification.")
    return 0


def cmd_publish(args, settings: Settings) -> int:
    """One scoring pass -> markdown digest + HTML dashboard + Discord, as requested."""
    from datetime import date

    from .output.discord import build_discord_payload, send_discord
    from .output.html import build_dashboard_html

    players = _get_roster(args, settings)
    week = _resolve_week(args, settings)

    # The single scoring pass shared by every output.
    recs = report.rank_each_position(settings, players, week)
    lineup = report.build_lineup(report.scored(recs))

    digest = report.render_digest(week, settings.scoring, recs)
    print(digest)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(digest)

    if args.dashboard:
        html = build_dashboard_html(week, settings.scoring, lineup, recs,
                                    generated_on=date.today().isoformat())
        args.dashboard.parent.mkdir(parents=True, exist_ok=True)
        args.dashboard.write_text(html)
        print(f"Wrote dashboard to {args.dashboard}")

    if args.discord:
        if not settings.discord_webhook_url:
            print("DISCORD_WEBHOOK_URL is not set — skipping Discord.", file=sys.stderr)
        else:
            dashboard_url = args.url or settings.dashboard_url or None
            payload = build_discord_payload(week, settings.scoring, lineup, recs, dashboard_url)
            try:
                send_discord(settings.discord_webhook_url, payload)
                print("Sent Discord notification.")
            except Exception as exc:
                # A Discord hiccup must not sink the digest/dashboard the rest of
                # the workflow depends on — warn and carry on.
                print(f"warning: Discord notification failed: {exc}", file=sys.stderr)
    return 0


def cmd_calibrate(args, settings: Settings, outcome_provider=None) -> int:
    """Join the decision log to actual outcomes and fit better blend weights (#7).

    ``outcome_provider`` is injectable so tests run fully offline; in normal use it
    defaults to the free Sleeper weekly-stats source.
    """
    from .calibrate import calibrate as run_calibrate
    from .calibrate import load_decisions

    log_path = args.log or settings.results_log_path
    decisions = load_decisions(log_path, season=args.season, week=args.week)
    if not decisions:
        print(f"No logged decisions in {log_path}. Run some rank/compare passes first?",
              file=sys.stderr)
        return 1

    provider = outcome_provider or _sleeper_outcome_provider(settings)
    result = run_calibrate(decisions, provider, base_weights=settings.weights,
                           step=args.step, min_pairs=args.min_pairs)
    _print_calibration(result)

    if not result.pairs_used:
        print("Could not join any logged decision to an actual outcome yet — "
              "outcomes post after games are played.", file=sys.stderr)
        return 1

    if args.write:
        if not result.enough_data:
            print(f"Only {result.pairs_used} joined pairs (< --min-pairs "
                  f"{args.min_pairs}); not writing — gather more data first.",
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
    print(f"Calibration over {result.decisions_used} decision(s), "
          f"{result.pairs_used} comparable pair(s); tuning {', '.join(result.signals) or '(none)'}.")
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
