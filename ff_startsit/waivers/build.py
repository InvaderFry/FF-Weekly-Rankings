"""Assemble one league's waiver/trade report from its provider.

One pass per league: read the league view once, score every position once, and
hand the renderers a single ``WaiverBundle``. This is the waiver-side analogue of
``report.score_week`` + ``cli._league_bundles``, and it keeps the same promises:
a section whose data source is unreachable is omitted with a warning, and the
league still reports.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from typing import Callable, Optional, Sequence

from ..config import Settings
from ..models import Player, PlayerScore
from ..pipeline import build_signals
from ..season import (REHEARSAL_BANNER, WAIVER_BANNER, is_preseason,
                      is_rehearsal_window)
from ..sources.schedule import ScheduleProvider
from .base import LeagueViewProvider, pool_players
from .columns import ColumnFetcher, index_mentions
from .endorse import Endorsements
from .models import DropCandidate, LeagueRules, PoolPlayer, StreamRow, WaiverBundle
from .season_values import SeasonValueProvider, URLS, protected_rank
from .score import (BYE_HORIZON, MIN_LEAGUE_TEAMS, STREAM_POSITIONS, bye_gaps,
                    dedupe_players, droppable, find_stashes, has_ecr,
                    pick_adds, pick_alternates,
                    score_positions, signal_coverage, stash_candidates,
                    starting_slots, team_players, viable_adds_by_position,
                    _add_candidates)
from .trades import suggest_trades
from .values import build_sheet, superflex_for

#: Scores are min-maxed inside a position's own candidate set, so a 9-point WR
#: gain and a 9-point TE gain are not the same nine points. Adds and drops are
#: therefore ordered by rank against positional starter demand, and a margin is
#: printed only where the subtraction is real — between two players at one
#: position. Said once, in the report, rather than left for the reader to infer.
MARGIN_NOTE = ("Scores are normalized within each position's candidate set, so a "
               "margin is shown only when an add and the player he replaces play "
               "the same position. Everything else is ordered by where a player "
               "ranks against what the league starts at his position.")
TRADE_NOTE = ("Trade ideas require similar overall rest-of-season ranks and "
              "weekly lineup gains for both teams. Rankings are not trade prices "
              "and do not cover keeper or dynasty value. The gain "
              "figures are within-position points, so compare them to each other "
              "only inside one idea. Treat them as conversation starters.")


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


def _lineup_keys(my_scores: Sequence[PlayerScore], rules: LeagueRules) -> set[str]:
    """Keys of players the lineup builder would start — never drop candidates.

    Uses ``report.build_lineup`` rather than a second definition of "starter", so
    the Tuesday report can't propose cutting somebody Sunday's report starts.

    Built from the *league's* slots, not the hardcoded 1QB/2RB/2WR template, and
    that includes its flex slots. ``droppable`` right below counts surplus against
    ``roster_slots`` alone, which has no entry a flex body could occupy — so every
    flex starter is surplus by that count and ``protected`` is the only thing
    standing between him and the drop list. Passing one hardcoded ``FLEX`` instead
    left a SUPER_FLEX league's second quarterback unprotected (an ordinary FLEX
    takes RB/WR/TE only) and a two-flex league's second flex body likewise, which
    is a recommendation to cut a starter.
    """
    from ..report import build_lineup

    slots: list[str] = []
    for pos, count in sorted(starting_slots(rules).items()):
        slots.extend([pos] * count)
    # Flex last, so the position slots claim their own players first.
    for name, count in sorted(rules.flex_slots.items()):
        slots.extend([name] * count)
    if not rules.flex_slots:
        slots.append("FLEX")   # platform told us nothing; one, as before

    by_pos: dict[str, list[PlayerScore]] = {}
    for s in my_scores:
        by_pos.setdefault(s.player.position, []).append(s)
    for scores in by_pos.values():
        scores.sort(key=lambda s: (s.final is not None, s.final or 0), reverse=True)
    return {pick.player.key
            for _, pick in build_lineup(by_pos, slots=slots) if pick is not None}


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


def _rehearsal_banner(index: dict[str, PlayerScore],
                      pool: Sequence[PoolPlayer]) -> str:
    """The rehearsal notice plus what the live signals actually reached.

    The counts are the deliverable, not decoration: without them an empty
    rehearsal reads exactly like a broken one. They ride in the banner rather
    than ``notes`` because ``notes`` reach the digest and the dashboard but not
    the Discord embed, and the Discord message is the thing being rehearsed.
    """
    if not pool:
        return REHEARSAL_BANNER
    counts = signal_coverage(index, pool_players(pool))
    if not counts:
        return REHEARSAL_BANNER
    covered = ", ".join(f"{name} {counts[name]}/{len(pool)}"
                        for name in sorted(counts))
    return f"{REHEARSAL_BANNER} Live coverage: {covered}."


def build_bundle(settings: Settings, label: str, provider: LeagueViewProvider,
                 my_players: Sequence[Player], week: int, *,
                 signals: Optional[Sequence] = None,
                 schedule: Optional[ScheduleProvider] = None,
                 column_fetcher: Optional[ColumnFetcher] = None,
                 season_values: Optional[SeasonValueProvider] = None,
                 limit: int = 150, max_adds: int = 8, max_alternates: int = 5,
                 max_trades: int = 5,
                 include_trades: bool = True,
                 include_columns: bool = True,
                 preseason: Optional[bool] = None,
                 rehearse: Optional[bool] = None,
                 writer_sources: Sequence = (),
                 chart_sources: Sequence = (),
                 weekly_boards: Optional[Callable[[Sequence[Player], int, str],
                                                  list]] = None) -> WaiverBundle:
    """Score one league's waiver wire and build its report.

    Before Week 1 the report is **refused**, not filled. ``pipeline.build_signals``
    swaps in bundled sample values during preseason so a start/sit table has
    something to demonstrate with; here those values would name real players to
    add and real players to cut, and a banner over an invented drop is still an
    invented drop. So preseason returns an empty bundle carrying the warning —
    before any provider call, so the refusal also costs no requests.

    A **dress rehearsal** is the third path: still preseason by the calendar, but
    scored on live rankings, lines and your real free-agent pool rather than the
    sample fill, so it proves the pipeline instead of demonstrating it. It runs on
    request (``--rehearse``) or by itself inside ``is_rehearsal_window``. What it
    can't reach it reports: the banner carries per-signal coverage, because an
    empty rehearsal must not read the same as a broken one.

    ``preseason`` and ``rehearse`` are injectable for tests, mirroring
    ``build_signals``; ``None`` means "detect from today's date".
    """
    if preseason is None:
        preseason = is_preseason()
    if rehearse is None:
        rehearse = is_rehearsal_window()
    rehearsing = preseason and rehearse
    if preseason and not rehearsing:
        # No notes: MARGIN_NOTE explains scores that this bundle doesn't carry.
        # The roster does come along: it was fetched before build_bundle was
        # called, so showing it costs nothing, and it is the one real thing a
        # refusal can say — that the draft landed and this is the team. Still no
        # scores or ranks, because there are none.
        return WaiverBundle(label=label, scoring=settings.scoring, week=week,
                            banner=WAIVER_BANNER, roster=list(my_players),
                            mode="preseason")

    rules = _rules(provider)
    teams = _teams(provider)
    pool = _pool(provider, week, limit)

    # Starter demand per position is ``team_count x starting slots``, and that
    # denominator is what makes a rank at one position comparable to a rank at
    # another. The team list is already here, so neither platform parser has to
    # learn to report it.
    if len(teams) >= MIN_LEAGUE_TEAMS and rules.team_count is None:
        rules = replace(rules, team_count=len(teams))

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

    # preseason=False past the gate: accurate in season, and the point of a
    # rehearsal — the sample fill is exactly what it exists to avoid.
    signals = (list(signals) if signals is not None
               else build_signals(settings, preseason=False))
    recs, index = score_positions(settings, candidates, week, signals=signals)
    try:
        season_ranks = (season_values or SeasonValueProvider()).fetch(candidates, settings.scoring)
    except Exception as exc:
        print(f"warning: rest-of-season rankings unavailable: {exc}", file=sys.stderr)
        season_ranks = {}
    for key, score in index.items():
        score.season_rank = season_ranks.get(key)
    if season_ranks:
        # The scoring belongs in the *label*: the footer dedupes on (label, url),
        # so three leagues at two scorings rendered two links reading exactly
        # "FantasyPros rest-of-season rankings" side by side.
        bundle.sources.append((f"FantasyPros rest-of-season rankings "
                               f"({settings.scoring.upper()})", URLS[settings.scoring]))
        bundle.league_notes.append(f"Season-long ranks cover {len(season_ranks)}/{len(candidates)} "
                            f"players. Top {protected_rank(rules.team_count)} overall players "
                            "and players without a season rank are protected from drops. "
                            "Kickers and defenses are same-position streaming swaps only.")
    else:
        notice = "Rest-of-season rankings unavailable: skill-player drops and trades withheld; only same-position kicker/defense swaps can be evaluated."
        bundle.caveat = f"{bundle.caveat} {notice}" if bundle.caveat else notice

    # Every run, not just the rehearsal. An empty adds list is otherwise
    # indistinguishable from a broken one, and `has_ecr` gates adds and drops
    # both — so an ECR outage renders as "nothing worth adding" in season, which
    # is the one thing a waiver report must never say when it cannot see.
    bundle.pool_size = len(pool)
    bundle.coverage = signal_coverage(index, pool_players(pool))
    bundle.recs = recs

    if rehearsing:
        bundle.banner = _rehearsal_banner(index, pool)
        bundle.mode = "rehearsal"

    my_team = next((t for t in teams if t.is_mine), None)
    # IR/reserve bodies sit outside the roster limit: they start no game, hold
    # no roster spot, and cutting one opens nothing an add could fill. So they
    # are taken out of the lineup, the drop pool and every positional count, and
    # listed on their own as IR-only cuts.
    reserve = my_team.reserve if my_team else frozenset()
    all_my_scores = [index[p.key] for p in my_players if p.key in index]
    my_scores = [s for s in all_my_scores if s.player.key not in reserve]
    active_players = [p for p in my_players if p.key not in reserve]
    rostered: dict[str, int] = {}
    for p in active_players:
        pos = (p.position or "").upper()
        rostered[pos] = rostered.get(pos, 0) + 1

    # The schedule is read before the drops now: a starting K/DEF on bye has no
    # ECR, so without knowing he's on bye he could never be streamed for.
    schedule = schedule or ScheduleProvider(cache_dir=settings.data_dir)
    playing = _weeks_playing(schedule, week)
    bye_teams = _bye_teams(candidates, playing.get(week))

    protected = _lineup_keys(my_scores, rules)
    drops = droppable(my_scores, rules, protected=protected)
    drops = [d for d in drops if d.score.player.position not in STREAM_POSITIONS
             and d.score.season_rank is not None
             and d.score.season_rank > protected_rank(rules.team_count)]
    drops.sort(key=lambda d: d.score.season_rank, reverse=True)
    # A one-for-one streamer replacement preserves the starting requirement;
    # a weekly starter can be replaced at that same position, never for a WR/RB.
    streamers = [DropCandidate(s, "same-position streaming replacement only")
                 for s in my_scores if s.player.position in STREAM_POSITIONS
                 and s.final is not None and has_ecr(s)
                 and s.player.team not in bye_teams]
    streamers += _bye_streamers(my_scores, rules, bye_teams, week)
    rank_floor = protected_rank(rules.team_count)
    bundle.ir_drops = [
        DropCandidate(s, _ir_reason(s), on_reserve=True)
        for s in all_my_scores if s.player.key in reserve
        and (s.season_rank is None or s.season_rank > rank_floor)
    ]

    ranks = journalist_ranks(settings, dedupe_players(pool_players(pool), my_players), week)

    mentions: dict[str, list] = {}
    quoting = [s for s in writer_sources if getattr(s, "kind", "") == "waiver"
               and hasattr(s, "mentions")]
    if include_columns and quoting:
        # The board's own sources already hold the columns: quote from those,
        # rather than fetch the same articles again through ColumnFetcher.
        found = []
        for source in quoting:
            try:
                found += source.mentions(week, pool_players(pool))
            except Exception as exc:     # quotes are decoration; never sink the report
                print(f"warning: {source.label} quotes unavailable: {exc}", file=sys.stderr)
        mentions = index_mentions(found)
    elif include_columns:
        fetcher = column_fetcher or ColumnFetcher()
        mentions = index_mentions(fetcher.fetch(week, pool_players(pool)))
        bundle.sources.extend(fetcher.read)
        for author, reason in getattr(fetcher, "unavailable", {}).items():
            bundle.notes.append(f"{author}: {reason}. Column commentary omitted.")

    faab_left = rules.faab_remaining(my_team.faab_spent if my_team else None)

    # The writers' readings are fetched *before* the pick now, because they can
    # veto an add (``endorse``). They still never create, reorder or re-price one.
    superflex = superflex_for(rules)
    writer_boards = _fetch_boards(writer_sources, pool_players(pool), week,
                                  settings.scoring, superflex)
    chart_boards = _fetch_boards(chart_sources, candidates, week,
                                 settings.scoring, superflex)
    stream_players = [p for p in dedupe_players(my_players, pool_players(pool))
                      if p.position in STREAM_POSITIONS]
    weekly = []
    if weekly_boards is not None and stream_players:
        try:
            weekly = [b for b in weekly_boards(stream_players, week, settings.scoring) if b]
        except Exception as exc:     # an annotation never sinks the report
            print(f"warning: K/DEF expert ranks unavailable: {exc}", file=sys.stderr)
    endorse = None
    if writer_boards or chart_boards or weekly:
        endorse = Endorsements(boards=writer_boards + chart_boards, weekly=weekly,
                               mentioned=frozenset(mentions))
    veto = endorse.veto if endorse is not None else None
    held: list = []

    all_drops = drops + streamers
    bundle.adds = pick_adds(index, pool, all_drops, rules, faab_remaining=faab_left,
                            journalist_ranks=ranks, mentions=mentions,
                            max_adds=max_adds, rostered=rostered,
                            veto=veto, held_back=held)
    # The same candidates against the same drops, minus the pairing: the adds
    # table runs out of roster spots long before it runs out of players worth
    # adding, and an empty row there used to be indistinguishable from a quiet
    # wire. Position caps carry over from ``bundle.adds`` (see
    # ``score.add_position_cap``), so this cannot reintroduce the third
    # quarterback the cap just removed.
    bundle.alternates = pick_alternates(index, pool, all_drops, rules, bundle.adds,
                                        faab_remaining=faab_left,
                                        journalist_ranks=ranks, mentions=mentions,
                                        max_alternates=max_alternates,
                                        rostered=rostered,
                                        veto=veto, held_back=held)
    # Counted from the same gate ``pick_adds`` applies, so "N were compared" and
    # the table it explains can never name different Ns.
    bundle.considered_adds = viable_adds_by_position(index, pool, rules)
    bundle.drops = drops[:max_adds]
    bundle.drop_pool = len(all_drops)
    if endorse is not None:
        for target in list(bundle.adds) + list(bundle.alternates):
            note = endorse.fallback_note(target.score)
            if note:
                target.reasons = (*target.reasons, note)
        bundle.held_back = [f"{s.player.name} ({s.player.position}): {why}" for s, why in held]
    if weekly:
        bundle.stream_columns = [b.short for b in weekly]
        bundle.weekly_boards = list(weekly)
        bundle.stream_rows = _stream_rows(index, pool, my_scores, rules, bundle, weekly,
                                          bye_teams, held)

    if writer_sources:
        _attach_expert_board(bundle, writer_boards, pool_players(pool), season_ranks,
                             mentions)

    taken = {t.score.player.key
             for t in list(bundle.adds) + list(bundle.alternates)}
    bundle.stashes = find_stashes(index, pool, taken, bye_teams)
    bundle.stash_pool = stash_candidates(index, pool, taken, bye_teams)
    bundle.byes = bye_gaps(active_players, rules, week, playing)

    if include_trades:
        if my_team is None:
            bundle.league_notes.append(
                "Trade ideas need to know which team is yours — set ESPN_TEAM_ID "
                "or check your SWID cookie."
            )
        else:
            # The trade builder protects the full lineup on both sides.
            bundle.trades_considered = True
            bundle.trades = suggest_trades(teams, index, rules,
                                           max_ideas=max_trades)
            if bundle.trades:
                bundle.notes.append(TRADE_NOTE)
    if chart_sources:
        # Your roster on each chart (display only), from the boards fetched above.
        bundle.values = build_sheet(bundle, my_players, (), week, superflex=superflex,
                                    boards=chart_boards)
    return bundle


def _fetch_boards(sources: Sequence, players: Sequence[Player], week: int,
                  scoring: str, superflex: bool) -> list:
    """Each source's board. A source never raises for I/O, but one that does
    anyway costs its own column and nothing else."""
    from ..boards.models import ALL_POSITIONS, ExpertBoard
    boards = []
    for source in sources:
        try:
            boards.append(source.fetch(players, week, scoring, superflex=superflex))
        except Exception as exc:
            print(f"warning: {source.label} unavailable: {exc}", file=sys.stderr)
            boards.append(ExpertBoard(source.id, source.short, source.label,
                                      unavailable={ALL_POSITIONS: f"{source.label}: {exc}"}))
    return boards


#: Free agents per position shown in the K/DEF comparison, beside your own.
STREAM_TABLE_ROWS = 5


def _stream_rows(index: dict, pool: Sequence[PoolPlayer], my_scores: Sequence[PlayerScore],
                 rules: LeagueRules, bundle: WaiverBundle, weekly: Sequence,
                 bye_teams: set[str], held: Sequence) -> dict:
    """Your K/DEF and the best free agents there, every expert side by side.

    Free agents are the add candidates at that position (the same gate
    ``pick_adds`` applies), so the table never lists a player the report could
    not have suggested; they are ordered by FantasyPros' rank.
    """
    from ..boards.models import ALL_POSITIONS
    added = {t.score.player.key: t for t in bundle.adds}
    alternates = {t.score.player.key for t in bundle.alternates}
    held_why = {s.player.key: why for s, why in held}
    dropped = {t.drop.player.key: t.score.player.name for t in bundle.adds if t.drop}

    def ranks(player):
        out = {}
        for b in weekly:
            if ALL_POSITIONS in b.unavailable or player.position in b.unavailable:
                out[b.short] = None
                continue
            e = b.entries.get(player.key)
            out[b.short] = e.pos_rank if e is not None else None
        return out

    def ecr(score):
        v = score.raw.get("ecr")
        return v.raw if v is not None and v.available else None

    rows: dict = {}
    for pos in ("K", "DEF"):
        if not starting_slots(rules).get(pos):
            continue
        out = []
        for s in my_scores:
            if s.player.position != pos:
                continue
            on_bye = s.player.team in bye_teams
            if s.player.key in dropped:
                status = (f"yours: on bye, swap for {dropped[s.player.key]}" if on_bye
                          else f"yours: swap for {dropped[s.player.key]}")
            elif on_bye:
                status = "yours: on bye"
            else:
                status = "yours: keep"
            # A player on bye has no ECR, so his blend is an artefact of what's
            # left (Boswell read 100.0): show no score rather than a fake one.
            final = None if on_bye else s.final
            out.append(StreamRow(s.player, True, final, ecr(s), ranks(s.player), status))
        fas = [score for _, score in _add_candidates(index, pool, rules)
               if score.player.position == pos]
        fas.sort(key=lambda s: ecr(s) or 999)
        for s in fas[:STREAM_TABLE_ROWS]:
            key = s.player.key
            if key in added:
                drop = added[key].drop
                status = f"add (drop {drop.player.name})" if drop else "add"
            elif key in held_why:
                status = f"held back: {held_why[key]}"
            elif key in alternates:
                status = "also consider"
            else:
                status = ""
            out.append(StreamRow(s.player, False, s.final, ecr(s), ranks(s.player), status))
        if out:
            rows[pos] = out
    return rows


def _attach_expert_board(bundle: WaiverBundle, boards: list, free_agents: Sequence[Player],
                         season_ranks: dict, mentions: Optional[dict] = None) -> None:
    """Each writer's reading of this league's wire, side by side (display only)."""
    from .board import build_board, fp_ros_board
    boards = list(boards)
    boards.append(fp_ros_board(season_ranks))
    bundle.expert_boards = boards
    bundle.expert_rows = build_board(free_agents, boards, bundle.adds, bundle.alternates,
                                     mentions)
    for board in boards:
        if board.url:
            bundle.sources.append((board.label, board.url))


def _bye_streamers(my_scores: Sequence[PlayerScore], rules: LeagueRules,
                   bye_teams: set[str], week: int) -> list[DropCandidate]:
    """Starting K/DEF on bye this week, when nobody else at his position plays.

    ``has_ecr`` keeps an unranked player off the drop list because "unranked on a
    Tuesday usually means on bye, not bad" — and for a streamer that exact case
    is the one that needs replacing: AndyLOT's Week 9 report printed "only 0
    healthy K for 1 slot(s)" and suggested no kicker. Only when the position
    can't field its starters this week; a second kicker who plays covers it.
    """
    if not bye_teams:
        return []
    slots = starting_slots(rules)
    out: list[DropCandidate] = []
    for pos in sorted(STREAM_POSITIONS):
        mine = [s for s in my_scores if s.player.position == pos]
        playing = [s for s in mine if s.player.team and s.player.team not in bye_teams]
        if len(playing) >= max(1, slots.get(pos, 1)):
            continue
        for s in mine:
            if s.player.team in bye_teams:
                out.append(DropCandidate(
                    s, f"on bye in Week {week}: stream a one-week replacement",
                    bye_stream=True))
    return out


def _ir_reason(score: PlayerScore) -> str:
    rank = ("no rest-of-season rank" if score.season_rank is None
            else f"ROS rank {score.season_rank:g}")
    return f"on IR ({rank}): cutting him frees an IR slot, not a roster spot"


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
