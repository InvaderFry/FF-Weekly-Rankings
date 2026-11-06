"""Will he actually play? — the start/sit pass's availability check.

Two gaps this closes, both found on a live Week 9 roster (Kyler Murray, listed
**Questionable**, while ESPN had already projected him for 0.0 on the news that
he would sit):

* **"Likely out."** The injury signal reads the official designation, and a
  Questionable player scores 75 there and starts. ESPN's own weekly projection
  reacts to the news days before the designation changes, so a doubtful-tagged
  player projected at (near) zero on a week his team *plays* is read as ruled
  out: he leaves the candidate set exactly as an ``Out`` would, which makes the
  lineup start his backup when there is one. A bye week also projects 0.0 — the
  live feed shows healthy starters at 0.0 on their bye — so with no schedule to
  tell the two apart, no claim is made at all.

* **The lineup hole.** When *nobody* playable can fill a slot, the blend's
  all-ruled-out guard still scores the position and the lineup still names a
  starter, so the report used to say "Start: Kyler Murray" with nothing louder
  than a tag beside the name. ``lineup_holes`` names each such slot, and
  ``stream_options`` suggests free agents from a *fresh* ESPN read (the Tuesday
  waiver report is days stale by Thursday), ordered by this week's FantasyPros
  rank at the position.

Display and availability only: no blend weight is added, nothing here is a
``Signal``, and ``weighted_final`` is untouched. The likely-out reading reaches
the blend only through ``InjurySignal``'s existing 0-means-ruled-out contract.
Every function is pure except the fetch the caller injects, so the whole module
is tested offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping, Optional, Sequence

from .models import Player, PlayerScore

#: A projection at or under this many points on a week the team plays reads as
#: "not expected to play". ESPN projects a real starter well above it at every
#: position (a kicker or defense included), and an out player at exactly 0.0.
LIKELY_OUT_MAX_PROJECTION = 0.5

#: Designations that leave a player's status open. Only these can be upgraded to
#: "likely out": a healthy player projected at zero is usually a backup, which
#: is a depth-chart question rather than an injury, and an OUT/IR player is
#: already ruled out by the injury signal.
OPEN_STATUSES = frozenset({"QUESTIONABLE", "DOUBTFUL"})

#: ESPN (and Sleeper) designations that mean "not this week". A free agent
#: carrying one is never suggested as a streamer.
RULED_OUT_STATUSES = frozenset({
    "OUT", "INJURY_RESERVE", "IR", "SUSPENSION", "SUS", "PUP", "NA", "DNR", "COV",
})

#: Slots of the fixed start/sit template a league may simply not have.
OPTIONAL_SLOTS = frozenset({"K", "DEF"})

#: How many free agents to suggest per hole.
STREAM_OPTIONS = 3

#: Positions each lineup slot accepts. Mirrors ``report.SLOT_POSITIONS``; kept
#: here as a lookup so this module doesn't import the report layer.
_FLEX = ("RB", "WR", "TE")


@dataclass(frozen=True)
class Outlook:
    """ESPN's read on one player for one week."""

    projection: Optional[float]   # None = ESPN gave no weekly projection
    status: str = ""              # ESPN injuryStatus, upper-cased ("" = none)


@dataclass(frozen=True)
class StreamOption:
    """One free agent who could fill a lineup hole this week."""

    player: Player
    rank: Optional[float] = None          # FantasyPros weekly rank at the position
    projection: Optional[float] = None    # ESPN projected points this week
    status: str = ""
    percent_owned: Optional[float] = None

    @property
    def rank_label(self) -> str:
        pos = "DST" if self.player.position == "DEF" else self.player.position
        return f"{pos}{self.rank:.0f}" if self.rank is not None else "unranked"

    def describe(self) -> str:
        """``Name (TEAM) — QB14, 15.2 proj, Questionable, 12% rostered``."""
        parts = [self.rank_label]
        if self.projection is not None:
            parts.append(f"{self.projection:.1f} proj")
        if self.status and self.status != "ACTIVE":
            parts.append(self.status.replace("_", " ").title())
        if self.percent_owned is not None:
            parts.append(f"{self.percent_owned:g}% rostered")
        return f"{self.player.name} ({self.player.team or 'FA'}) — " + ", ".join(parts)


@dataclass
class LineupHole:
    """A starting slot nobody playable can fill this week."""

    slot: str
    position: str                     # what to stream: QB, RB, ..., or FLEX
    starter: Optional[str] = None     # the name the lineup put there anyway
    reason: str = ""
    options: list[StreamOption] = field(default_factory=list)
    #: Why ``options`` is empty, when it is — "free agents not checked" and
    #: "nobody playable on the wire" are different things to do about it.
    options_note: Optional[str] = None

    def headline(self) -> str:
        what = "FLEX-eligible player" if self.position == "FLEX" else self.position
        line = f"No healthy {what} for your {self.slot} slot"
        if self.starter:
            line += f": {self.starter}"
            if self.reason:
                line += f" ({self.reason})"
        elif self.reason:
            line += f" ({self.reason})"
        return line + "."


# --- reading ESPN ----------------------------------------------------------

def week_projection(info: Mapping, week: int,
                    season: Optional[int] = None) -> Optional[float]:
    """ESPN's projected points for ``week`` from a player blob, or None.

    Projections are the ``stats`` entries with ``statSourceId`` 1 (0 is actual
    points) and ``statSplitTypeId`` 1 (one week; 0 and 2 are season totals),
    keyed by ``scoringPeriodId``. The blob carries every week of two seasons, so
    the season is checked whenever it is known.
    """
    for stat in info.get("stats") or ():
        if not isinstance(stat, Mapping):
            continue
        if (stat.get("statSourceId") != 1 or stat.get("statSplitTypeId") != 1
                or stat.get("scoringPeriodId") != week):
            continue
        if season is not None and stat.get("seasonId") not in (None, season):
            continue
        total = stat.get("appliedTotal")
        if isinstance(total, (int, float)):
            return float(total)
    return None


def parse_outlook(payload: Mapping, week: int,
                  season: Optional[int] = None) -> dict[str, Outlook]:
    """``{espn-<id>: Outlook}`` from a ``kona_player_info`` response (pure)."""
    out: dict[str, Outlook] = {}
    for entry in payload.get("players") or ():
        info = (entry.get("player") or (entry.get("playerPoolEntry") or {}).get("player")
                or {})
        pid = info.get("id", entry.get("id"))
        if pid is None:
            continue
        out[f"espn-{pid}"] = Outlook(
            projection=week_projection(info, week, season),
            status=str(info.get("injuryStatus") or "").upper())
    return out


# --- likely out --------------------------------------------------------------

def _pretty(status: str) -> str:
    return status.replace("_", " ").title()


def likely_out(players: Iterable[Player], outlook: Mapping[str, Outlook],
               playing: Optional[set[str]]) -> dict[str, str]:
    """``{player key: note}`` for each player ESPN says won't play.

    Two ways in. **ESPN's own ruled-out designation** (OUT, injury reserve,
    suspension) counts on its own, needing neither a projection nor a
    schedule: the injury signal reads Sleeper's player blob, which caches for
    24 hours, so a player ESPN has already moved from Questionable to OUT can
    still read Questionable there — and without this he started again the
    moment ESPN confirmed what the projection had been saying.

    Otherwise all three of: an open designation (Questionable/Doubtful), a
    weekly projection at or under ``LIKELY_OUT_MAX_PROJECTION``, and a
    schedule showing his team plays. ``playing`` of ``None`` means the schedule
    couldn't be read, and then no projection-based claim is made: a bye
    projects 0.0 too, and a false "likely out" benches a healthy starter.
    """
    out: dict[str, str] = {}
    for p in players:
        o = outlook.get(p.key)
        if o is None:
            continue
        if o.status in RULED_OUT_STATUSES:
            out[p.key] = f"{_pretty(o.status)} (ESPN)"
            continue
        if not playing or o.projection is None or o.status not in OPEN_STATUSES:
            continue
        if o.projection > LIKELY_OUT_MAX_PROJECTION or p.team not in playing:
            continue
        out[p.key] = (f"Likely out: ESPN projects {o.projection:.1f} pts despite "
                      f"{_pretty(o.status)}")
    return out


# --- lineup holes ------------------------------------------------------------

def unplayable_reason(pick: PlayerScore, playing: Optional[set[str]]) -> Optional[str]:
    """Why ``pick`` won't score this week, or None if he's expected to play.

    Reads the injury signal's availability score, whose 0.0 is the same
    "ruled out" ``InjurySignal.rules_out`` uses (OUT/IR/... and likely-out),
    and the schedule for a bye.
    """
    if playing and (pick.player.team is None or pick.player.team not in playing):
        return "on bye" if pick.player.team else "no NFL team"
    sv = pick.raw.get("injury")
    if sv is not None and sv.available and sv.raw == 0.0:
        return sv.note or "ruled out"
    return None


def lineup_holes(lineup: Iterable[tuple[str, Optional[PlayerScore]]],
                 playing: Optional[set[str]],
                 roster: Iterable[PlayerScore] = ()) -> list[LineupHole]:
    """Every slot the lineup could only fill with someone who won't play.

    The builder already starts a playable backup over an injured starter (a
    ruled-out player carries no score), so an injury hole means the roster has
    nobody. A bye is not ruled out upstream, so for a bye the slot is only a
    hole when ``roster`` (every scored player) holds no playable, unused
    alternative for it — otherwise the fix is on the bench, not on the wire.
    """
    lineup = list(lineup)
    used = {pick.player.key for _, pick in lineup if pick is not None}
    spare = [s for s in roster
             if s.player.key not in used and unplayable_reason(s, playing) is None]
    holes: list[LineupHole] = []
    for slot, pick in lineup:
        position = _stream_position(slot)
        if pick is None:
            # An empty K/DEF slot is left to the "(no option)" row: the lineup
            # template has one of each, and plenty of leagues play without
            # kickers, where this would raise the alarm every single week.
            if slot not in OPTIONAL_SLOTS:
                holes.append(LineupHole(slot=slot, position=position,
                                        reason="nobody on your roster can fill it"))
            continue
        reason = unplayable_reason(pick, playing)
        if reason is None:
            continue
        accepts = _slot_accepts(slot)
        if any(s.player.position in accepts for s in spare):
            continue
        holes.append(LineupHole(slot=slot, position=position,
                                starter=pick.player.name, reason=reason))
    return holes


def _slot_accepts(slot: str) -> tuple[str, ...]:
    if slot == "FLEX":
        return _FLEX
    if slot == "SUPER_FLEX":
        return ("QB",) + _FLEX
    return (slot,)


def _stream_position(slot: str) -> str:
    # A superflex hole is streamed with a quarterback: that is what the slot is
    # for, and FantasyPros' pooled list (the FLEX case) doesn't rank QBs.
    return "QB" if slot == "SUPER_FLEX" else slot


def slot_positions(position: str) -> tuple[str, ...]:
    return _FLEX if position == "FLEX" else (position,)


# --- streamers ---------------------------------------------------------------

#: ``rank(players) -> {key: rank or None}``: this week's FantasyPros rank,
#: positional for a single position and the pooled FLEX list for "FLEX".
RankFn = Callable[[str, Sequence[Player]], Mapping[str, Optional[float]]]


def stream_options(position: str, pool: Sequence, rank: Optional[RankFn],
                   playing: Optional[set[str]], week: int,
                   limit: int = STREAM_OPTIONS) -> list[StreamOption]:
    """The best ``limit`` playable free agents for a hole at ``position``.

    ``pool`` is ``waivers.models.PoolPlayer`` objects. A candidate must play
    this week (a known schedule, not a bye), carry no ruled-out designation,
    and not be projected out himself. Ordered by FantasyPros rank; a player
    FantasyPros doesn't rank sorts after every ranked one, by ESPN projection.
    """
    allowed = slot_positions(position)
    cands = []
    for pp in pool:
        p = pp.player
        if p.position not in allowed:
            continue
        if playing and p.team not in playing:
            continue
        status = (pp.injury_status or "").upper()
        if status in RULED_OUT_STATUSES:
            continue
        proj = getattr(pp, "projection", None)
        if proj is not None and proj <= LIKELY_OUT_MAX_PROJECTION:
            continue
        cands.append(pp)
    ranks: Mapping[str, Optional[float]] = {}
    if rank is not None and cands:
        try:
            ranks = rank(position, [pp.player for pp in cands])
        except Exception:  # a rank lookup must never sink the report
            ranks = {}

    def key(pp):
        r = ranks.get(pp.player.key)
        proj = getattr(pp, "projection", None)
        return (r is None, r if r is not None else 0.0,
                -(proj if proj is not None else -1.0), pp.player.name)

    out = []
    for pp in sorted(cands, key=key)[:limit]:
        out.append(StreamOption(
            player=pp.player, rank=ranks.get(pp.player.key),
            projection=getattr(pp, "projection", None),
            status=(pp.injury_status or "").upper(),
            percent_owned=pp.percent_owned))
    return out


#: ``free_agents(positions) -> [PoolPlayer]``: a *fresh* read of the wire.
FreeAgentFn = Callable[[tuple[str, ...]], Sequence]


def fill_options(holes: Sequence[LineupHole], free_agents: Optional[FreeAgentFn],
                 rank: Optional[RankFn], playing: Optional[set[str]],
                 week: int) -> None:
    """Attach streamer suggestions to each hole, in place. Never raises.

    One free-agent request covers every hole: the positions are gathered first.
    """
    if not holes:
        return
    if free_agents is None:
        for h in holes:
            h.options_note = "free agents weren't checked for this league"
        return
    wanted: tuple[str, ...] = tuple(sorted({pos for h in holes
                                            for pos in slot_positions(h.position)}))
    try:
        pool = list(free_agents(wanted))
    except Exception as exc:  # the warning must still render without options
        for h in holes:
            h.options_note = f"couldn't read free agents ({exc})"
        return
    if not pool:
        for h in holes:
            h.options_note = "the free-agent list came back empty"
        return
    for h in holes:
        h.options = stream_options(h.position, pool, rank, playing, week)
        if not h.options:
            h.options_note = "no playable free agent at this position this week"
