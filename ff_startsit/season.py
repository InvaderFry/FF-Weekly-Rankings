"""Season calendar math — the single source of truth for "has the season started?".

The CLI's week/season fallbacks and the preseason detection both live here so
there is exactly one definition of "the NFL year" and "kickoff". Pure stdlib,
no I/O: every function takes an optional ``today`` so tests stay offline and
deterministic.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from typing import Optional

#: Shown when a preseason run is auto-filled with bundled sample data.
SAMPLE_BANNER = ("⚠️ PRESEASON — the NFL season hasn't started, so real rankings "
                 "aren't available yet. The scores below are SAMPLE data for "
                 "demonstration only. Real data begins with Week 1 in September.")
#: Shown when preseason is detected but the sample fill is disabled.
NODATA_BANNER = ("⚠️ PRESEASON — the NFL season hasn't started, so real rankings "
                 "aren't available yet and no picks can be made. Real data begins "
                 "with Week 1 in September. (Sample fill disabled: FF_PRESEASON_FILL=0)")
#: Shown on the waiver pass, which refuses outright before Week 1.
WAIVER_BANNER = ("⚠️ PRESEASON — the NFL season hasn't started, so there are no "
                 "weekly rankings to score a waiver wire against. No adds, drops "
                 "or trades are suggested until the season kicks off in September.")
#: Shown on a dress rehearsal: preseason by the calendar, but run against live
#: data rather than the sample fill.
REHEARSAL_BANNER = ("🧪 DRESS REHEARSAL — Week 1 hasn't kicked off yet. This ran "
                    "against live rankings, lines and your real free-agent pool "
                    "(no sample data), so read it as an early look at Week 1, not "
                    "as this week's waiver advice.")
#: How long before kickoff the automatic dress rehearsal runs. Exactly 7 days, so
#: precisely one of waivers.yml's weekly Wednesday crons lands inside the window —
#: one rehearsal a season, with no arithmetic to keep in sync with the schedule.
REHEARSAL_DAYS = 7


def season_year(today: Optional[date] = None) -> int:
    """The year the current NFL season is named for (prior year before March)."""
    today = today or date.today()
    return today.year if today.month >= 3 else today.year - 1


#: Env override for a kickoff we got wrong, as ``YYYY-MM-DD``. The escape hatch
#: the table below needs: a year nobody has filled in yet, or a date the league
#: moves, is correctable without a release.
KICKOFF_ENV = "FF_SEASON_KICKOFF"

#: Kickoffs that are *not* the first Thursday of September, which is otherwise a
#: good guess (2024 opened Sept 5, 2025 opened Sept 4 — both first Thursdays).
#: 2026 opens on a **Wednesday**, six days later than the guess, and that gap is
#: not cosmetic: it read Sept 3-8 as regular season with no preseason warning,
#: and put every ``date_week`` from Sept 10 onward a full week ahead.
KNOWN_KICKOFFS: dict[int, date] = {
    2025: date(2025, 9, 4),
    2026: date(2026, 9, 9),
}


def _kickoff_override(year: int) -> Optional[date]:
    """``FF_SEASON_KICKOFF`` as a date, when it names ``year``.

    Fail loud-but-graceful, like ``config._validate_weights``: a malformed value
    warns and falls through to the table rather than crashing a scheduled run or
    silently standing in for a date nobody checked.
    """
    raw = (os.environ.get(KICKOFF_ENV) or "").strip()
    if not raw:
        return None
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        print(f"warning: {KICKOFF_ENV}={raw!r} is not YYYY-MM-DD; ignoring it",
              file=sys.stderr)
        return None
    return parsed if parsed.year == year else None


def first_kickoff(year: int) -> date:
    """Week 1's kickoff: an env override, a known date, then the Thursday guess."""
    override = _kickoff_override(year)
    if override is not None:
        return override
    known = KNOWN_KICKOFFS.get(year)
    if known is not None:
        return known
    sept1 = date(year, 9, 1)
    return sept1 + timedelta(days=(3 - sept1.weekday()) % 7)


def is_preseason(today: Optional[date] = None) -> bool:
    """True between March and the season's Week 1 kickoff.

    January/February resolve to the *prior* season's kickoff (playoffs are in
    season), so this is only True in the spring/summer dead zone.
    """
    today = today or date.today()
    return today < first_kickoff(season_year(today))


def date_week(today: Optional[date] = None) -> int:
    """Rough NFL week from a date — a fallback when Sleeper /state/nfl fails."""
    today = today or date.today()
    kickoff = first_kickoff(season_year(today))
    if today < kickoff:
        return 1
    return max(1, min(18, (today - kickoff).days // 7 + 1))


def date_season(today: Optional[date] = None) -> str:
    return str(season_year(today))


def preseason_banner(settings, today: Optional[date] = None) -> Optional[str]:
    """The warning to stamp on every output, or ``None`` once the season starts.

    ``settings`` is duck-typed (only ``preseason_fill`` is read) so this module
    stays import-cycle-free.
    """
    if not is_preseason(today):
        return None
    return SAMPLE_BANNER if getattr(settings, "preseason_fill", True) else NODATA_BANNER


def is_rehearsal_window(today: Optional[date] = None) -> bool:
    """True in the final week before kickoff — when Week 1 data actually exists.

    The rehearsal is deliberately *not* the first week of preseason: that is late
    July, when there are no weekly rankings to fetch and Week 1 lines are thin, so
    a run then proves the least. A week out, the data is real.
    """
    today = today or date.today()
    delta = first_kickoff(season_year(today)) - today
    return timedelta(0) < delta <= timedelta(days=REHEARSAL_DAYS)


def waiver_banner(rehearse: bool = False,
                  today: Optional[date] = None) -> Optional[str]:
    """The waiver pass's whole-run notice: refusal, rehearsal, or nothing.

    Deliberately does not consult ``preseason_fill``: the sample fill exists so a
    preseason *start/sit table* has something to show, and a labeled-but-invented
    add/drop is still a roster move somebody might make. A rehearsal isn't an
    exception to that — it earns its banner by using live data instead.
    """
    if not is_preseason(today):
        return None
    return (REHEARSAL_BANNER if rehearse or is_rehearsal_window(today)
            else WAIVER_BANNER)
