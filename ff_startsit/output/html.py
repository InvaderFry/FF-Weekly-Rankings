"""Static HTML dashboard for the weekly start/sit results.

Renders the same data the markdown digest shows (suggested lineup + rankings by
position, with close-call/injury flags surfaced) into a single self-contained
HTML page — inline CSS, no external assets — so it can be published to GitHub
Pages with zero configuration.

Pure: takes the already-computed lineup and per-position recommendations and
returns a string, so it is unit-testable offline.
"""

from __future__ import annotations

from html import escape
from typing import Optional, Sequence

from ..models import PlayerScore, Recommendation
from ..sources.journalists import JournalistView

# Order positions appear on the dashboard (mirrors report.POSITION_ORDER).
POSITION_ORDER = ["QB", "RB", "WR", "TE", "K", "DEF"]

_STYLE = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { font-family: -apple-system, system-ui, "Segoe UI", Roboto, sans-serif;
       margin: 0; padding: 1.25rem; max-width: 880px; margin-inline: auto;
       background: #0f1115; color: #e7e9ee; }
h1 { font-size: 1.5rem; margin: 0 0 .25rem; }
h2 { font-size: 1.15rem; margin: 1.75rem 0 .5rem; }
h3 { font-size: 1rem; margin: 1.25rem 0 .35rem; color: #c6cad3; }
.meta { color: #9aa0ad; font-size: .85rem; margin-bottom: 1rem; }
table { width: 100%; border-collapse: collapse; margin: .25rem 0 .5rem;
        font-size: .92rem; }
th, td { text-align: left; padding: .4rem .55rem; border-bottom: 1px solid #262a33; }
th { color: #9aa0ad; font-weight: 600; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tr.top td { background: rgba(46, 160, 67, .14); }
tr.flagged td { background: rgba(210, 153, 34, .14); }
.callout { background: rgba(210, 153, 34, .16); border-left: 3px solid #d29922;
           padding: .5rem .75rem; border-radius: 4px; margin: .35rem 0 .75rem; }
.start { color: #3fb950; font-weight: 600; }
.flag { color: #d29922; }
footer { color: #6e7481; font-size: .8rem; margin-top: 2rem; }
"""


def _signal_names(rec: Recommendation) -> list[str]:
    return sorted({name for s in rec.scores for name in s.normalized})


def _lineup_table(lineup: Sequence[tuple[str, Optional[PlayerScore]]]) -> str:
    rows = ["<tr><th>Slot</th><th>Player</th><th>Team</th>"
            "<th class='num'>Score</th></tr>"]
    for slot, pick in lineup:
        if pick is None:
            rows.append(f"<tr><td>{escape(slot)}</td><td><em>(no option)</em></td>"
                        "<td></td><td class='num'></td></tr>")
            continue
        team = escape(pick.player.team or "BYE")
        rows.append(
            f"<tr><td>{escape(slot)}</td><td>{escape(pick.player.name)}</td>"
            f"<td>{team}</td><td class='num'>{pick.final:.1f}</td></tr>"
        )
    return "<table>" + "".join(rows) + "</table>"


def _position_table(rec: Recommendation) -> str:
    signal_names = _signal_names(rec)
    head = ["#", "Player", "Pos", "Team", "Score", *[n.upper() for n in signal_names], "Flags"]
    cells = ["<th class='num'>#</th>", "<th>Player</th>", "<th>Pos</th>", "<th>Team</th>",
             "<th class='num'>Score</th>"]
    cells += [f"<th class='num'>{escape(n)}</th>" for n in [h.upper() for h in signal_names]]
    cells.append("<th>Flags</th>")
    rows = ["<tr>" + "".join(cells) + "</tr>"]

    for i, s in enumerate(rec.scores, start=1):
        verdict = "—" if s.final is None else f"{s.final:.1f}"
        cls = ""
        if s.flags:
            cls = " class='flagged'"
        elif i == 1 and s.final is not None:
            cls = " class='top'"
        tds = [f"<td class='num'>{i}</td>", f"<td>{escape(s.player.name)}</td>",
               f"<td>{escape(s.player.position)}</td>",
               f"<td>{escape(s.player.team or 'BYE')}</td>",
               f"<td class='num'>{escape(verdict)}</td>"]
        for name in signal_names:
            n = s.normalized.get(name)
            tds.append(f"<td class='num'>{'—' if n is None else f'{n:.0f}'}</td>")
        flag_html = escape("; ".join(s.flags))
        tds.append(f"<td class='flag'>{flag_html}</td>")
        rows.append(f"<tr{cls}>" + "".join(tds) + "</tr>")

    return "<table>" + "".join(rows) + "</table>"


def _position_section(pos: str, rec: Recommendation) -> str:
    parts = [f"<h2>{escape(pos)}</h2>"]
    if rec.close_call:
        notes = "".join(f"<div>• {escape(n)}</div>" for n in rec.notes)
        parts.append(f"<div class='callout'>⚠️ <strong>Close call</strong>{notes}</div>")
    elif rec.scores and rec.scores[0].final is not None:
        parts.append(f"<div class='start'>✅ Start: {escape(rec.scores[0].player.name)}</div>")
    parts.append(_position_table(rec))
    return "".join(parts)


def _journalists_section(view: JournalistView) -> str:
    names = ", ".join(e.name for e in view.experts)
    parts = [
        "<h2>Preferred journalists</h2>",
        f"<div class='meta'>Average weekly rank across: {escape(names)}. "
        "Side-by-side view only — not part of the blended score.</div>",
    ]
    for pos in POSITION_ORDER:
        rows_ = view.by_position.get(pos)
        if not rows_:
            continue
        head = ["<th class='num'>#</th>", "<th>Player</th>", "<th>Team</th>",
                "<th class='num'>Avg rank</th>"]
        head += [f"<th class='num'>{escape(e.name)}</th>" for e in view.experts]
        rows = ["<tr>" + "".join(head) + "</tr>"]
        for i, row in enumerate(rows_, start=1):
            tds = [f"<td class='num'>{i}</td>",
                   f"<td>{escape(row.player.name)}</td>",
                   f"<td>{escape(row.player.team or 'BYE')}</td>",
                   f"<td class='num'>{row.avg_rank:.1f}</td>"]
            for e in view.experts:
                rank = row.ranks.get(e.id)
                tds.append(f"<td class='num'>{'—' if rank is None else f'{rank:.0f}'}</td>")
            cls = " class='top'" if i == 1 else ""
            rows.append(f"<tr{cls}>" + "".join(tds) + "</tr>")
        parts.append(f"<h3>{escape(pos)}</h3>")
        parts.append("<table>" + "".join(rows) + "</table>")
    return "".join(parts)


def build_dashboard_html(week: int, scoring: str,
                         lineup: Sequence[tuple[str, Optional[PlayerScore]]],
                         recs: dict[str, Recommendation],
                         generated_on: str,
                         banner: Optional[str] = None,
                         journalists: Optional[JournalistView] = None) -> str:
    """Render the full dashboard as a self-contained HTML document.

    ``banner`` (the preseason sample-data warning) renders as a callout at the
    top of the page. ``journalists`` (the preferred-journalists view) adds its
    section after the position rankings; ``None`` omits it.
    """
    sections = [
        f"<h1>🏈 Week {escape(str(week))} start/sit — {escape(scoring.upper())}</h1>",
        f"<div class='meta'>Generated {escape(generated_on)}</div>",
    ]
    if banner:
        sections.append(f"<div class='callout'><strong>{escape(banner)}</strong></div>")
    sections += [
        "<h2>Suggested lineup</h2>",
        _lineup_table(lineup),
        "<h2>Rankings by position</h2>",
    ]
    for pos in POSITION_ORDER:
        rec = recs.get(pos)
        if rec is None or not rec.scores:
            continue
        sections.append(_position_section(pos, rec))

    if journalists is not None:
        sections.append(_journalists_section(journalists))

    body = "\n".join(sections)
    return (
        "<!doctype html>\n"
        "<html lang='en'>\n<head>\n"
        "<meta charset='utf-8'>\n"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>\n"
        f"<title>Week {escape(str(week))} start/sit</title>\n"
        f"<style>{_STYLE}</style>\n"
        "</head>\n<body>\n"
        f"{body}\n"
        "<footer>Generated by ff-startsit — leans, not guarantees.</footer>\n"
        "</body>\n</html>\n"
    )
