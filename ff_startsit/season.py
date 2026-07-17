"""Season calendar math — the single source of truth for "has the season started?".

The CLI's week/season fallbacks and the preseason detection both live here so
there is exactly one definition of "the NFL year" and "kickoff". Pure stdlib,
no I/O: every function takes an optional ``today`` so tests stay offline and
deterministic.
"""

from __future__ import annotations

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


def season_year(today: Optional[date] = None) -> int:
    """The year the current NFL season is named for (prior year before March)."""
    today = today or date.today()
    return today.year if today.month >= 3 else today.year - 1


def first_kickoff(year: int) -> date:
    """Week 1 kicks off around the first Thursday of September."""
    sept1 = date(year, 9, 1)
    return sept1 + timedelta(days=(3 - sept1.weekday()) % 7)


def is_preseason(today: Optional[date] = None) -> bool:
    """True between March and the season's first Thursday-of-September kickoff.

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
