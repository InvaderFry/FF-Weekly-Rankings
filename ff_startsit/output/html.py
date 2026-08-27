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
summary.league { font-size: 1.3rem; font-weight: 700; cursor: pointer;
                 margin: 2rem 0 .25rem; padding: .35rem 0; }
details.league { border-top: 1px solid #262a33; }
details.league[open] summary.league { color: #e7e9ee; }
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
.note { color: #9aa0ad; font-size: .85rem; margin: .35rem 0 1rem; }
.start { color: #3fb950; font-weight: 600; }
.flag { color: #d29922; }
footer { color: #6e7481; font-size: .8rem; margin-top: 2rem; }
nav.pages { display: flex; gap: .75rem; margin: 0 0 1rem; font-size: .9rem; }
nav.pages a { color: #9aa0ad; text-decoration: none; padding: .2rem .55rem;
              border: 1px solid #262a33; border-radius: 999px; }
nav.pages a.active { color: #e7e9ee; border-color: #3fb950; }
ul.ideas { margin: .25rem 0 .75rem; padding-left: 1.1rem; }
ul.ideas li { margin: .25rem 0; }
blockquote.quote { margin: .35rem 0 .75rem; padding: .35rem .75rem;
                   border-left: 3px solid #3a4150; color: #c6cad3;
                   font-size: .9rem; }
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


def _dashboard_body(lineup: Sequence[tuple[str, Optional[PlayerScore]]],
                    recs: dict[str, Recommendation],
                    banner: Optional[str] = None,
                    journalists: Optional[JournalistView] = None) -> list[str]:
    """The lineup + rankings + journalists sections (no H1), shared by single and
    multi-league dashboards."""
    sections: list[str] = []
    if banner:
        sections.append(f"<div class='callout'><strong>{escape(banner)}</strong></div>")
    sections += [
        "<h2>Suggested lineup</h2>",
        _lineup_table(lineup),
    ]
    # How the FLEX slot was decided — a pooled cross-position ranking, or the
    # positional fallback whose scores aren't comparable across positions.
    caveat = getattr(lineup, "caveat", None)
    if caveat:
        sections.append(f"<p class='note'>⚠️ {escape(caveat)}</p>")
    sections.append("<h2>Rankings by position</h2>")
    for pos in POSITION_ORDER:
        rec = recs.get(pos)
        if rec is None or not rec.scores:
            continue
        sections.append(_position_section(pos, rec))
    if journalists is not None:
        sections.append(_journalists_section(journalists))
    return sections


#: The pages the site publishes, as (filename, label). Both scheduled workflows
#: emit the whole set, so either page can link to the other and neither run can
#: leave a dangling link by replacing the site with just its own page.
PAGES = (("index.html", "Start/sit"), ("waivers.html", "Waivers & trades"))


def _nav(active: str) -> str:
    """Cross-links between the published pages, marking the current one."""
    links = []
    for href, label in PAGES:
        cls = " class='active'" if href == active else ""
        links.append(f"<a href='{escape(href)}'{cls}>{escape(label)}</a>")
    return "<nav class='pages'>" + "".join(links) + "</nav>"


def _document(title: str, body: str, active: str = "index.html") -> str:
    return (
        "<!doctype html>\n"
        "<html lang='en'>\n<head>\n"
        "<meta charset='utf-8'>\n"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>\n"
        f"<title>{title}</title>\n"
        f"<style>{_STYLE}</style>\n"
        "</head>\n<body>\n"
        f"{_nav(active)}\n"
        f"{body}\n"
        "<footer>Generated by ff-startsit — leans, not guarantees.</footer>\n"
        "</body>\n</html>\n"
    )


def build_dashboard_html(week: int, scoring: str,
                         lineup: Sequence[tuple[str, Optional[PlayerScore]]],
                         recs: dict[str, Recommendation],
                         generated_on: str,
                         banner: Optional[str] = None,
                         journalists: Optional[JournalistView] = None,
                         label: str = "") -> str:
    """Render the full dashboard as a self-contained HTML document.

    ``banner`` (the preseason sample-data warning) renders as a callout at the
    top of the page. ``journalists`` (the preferred-journalists view) adds its
    section after the position rankings; ``None`` omits it. ``label`` (a league
    name) is appended to the page heading when set.
    """
    label_suffix = f" · {escape(label)}" if label else ""
    sections = [
        f"<h1>🏈 Week {escape(str(week))} start/sit — {escape(scoring.upper())}{label_suffix}</h1>",
        f"<div class='meta'>Generated {escape(generated_on)}</div>",
    ]
    sections += _dashboard_body(lineup, recs, banner=banner, journalists=journalists)
    title = "Week " + escape(str(week)) + " start/sit" + (f" · {escape(label)}" if label else "")
    return _document(title, "\n".join(sections))


def build_multi_dashboard_html(week: int, bundles: Sequence["LeagueBundle"],
                               generated_on: str) -> str:
    """Render several leagues into one page, each a collapsible section.

    ``bundles`` are ``report.LeagueBundle`` objects (duck-typed here to avoid an
    import cycle): each contributes a ``<details>`` block with its own lineup and
    rankings, so the whole week is one self-contained file.
    """
    sections = [
        f"<h1>🏈 Week {escape(str(week))} start/sit</h1>",
        f"<div class='meta'>Generated {escape(generated_on)} · "
        f"{len(bundles)} league(s)</div>",
    ]
    for b in bundles:
        sections.append("<details class='league' open>")
        sections.append(
            f"<summary class='league'>{escape(b.label)} — {escape(b.scoring.upper())}</summary>")
        sections += _dashboard_body(b.lineup, b.recs, banner=b.banner,
                                    journalists=b.journalists)
        sections.append("</details>")
    return _document(f"Week {escape(str(week))} start/sit — all leagues",
                     "\n".join(sections))


# --- waiver wire & trades page --------------------------------------------
def _waiver_adds_table(bundle) -> str:
    if not bundle.adds:
        # One shared definition of what an empty section means, so this page and
        # the digest and the Discord embed cannot disagree. None means a banner is
        # standing and already says why there is nothing here.
        reason = bundle.no_adds_reason()
        return f"<p class='note'>{escape(reason)}</p>" if reason else ""
    rows = [
        "<table><thead><tr><th>Add</th><th>Pos</th><th class='num'>Score</th>"
        "<th>Drop for him</th><th>Bid</th><th>Why</th></tr></thead><tbody>"
    ]
    for t in bundle.adds:
        drop = t.drop.player.name if t.drop else "—"
        why = "; ".join(t.reasons[1:]) or "—"
        rows.append(
            f"<tr class='top'><td class='start'>{escape(t.score.player.name)}</td>"
            f"<td>{escape(t.score.player.position)}</td>"
            f"<td class='num'>{t.score.final:.1f}</td>"
            # No margin across positions — the two scores were normalized in
            # separate candidate sets, so their difference isn't a quantity.
            f"<td>{escape(drop)}" + (f" <span class='note'>+{t.margin:.1f}</span>"
                                     if t.margin is not None else "") + "</td>"
            f"<td>{escape(t.bid or '—')}</td>"
            f"<td>{escape(why)}</td></tr>"
        )
    rows.append("</tbody></table>")
    return "\n".join(rows)


def _waiver_drops_table(bundle) -> str:
    if not bundle.drops:
        return ""
    rows = ["<h3>Safe to cut</h3>",
            "<table><thead><tr><th>Player</th><th>Pos</th>"
            "<th class='num'>Score</th><th>Why</th></tr></thead><tbody>"]
    for d in bundle.drops:
        rows.append(
            f"<tr><td>{escape(d.score.player.name)}</td>"
            f"<td>{escape(d.score.player.position)}</td>"
            f"<td class='num'>{d.score.final:.1f}</td>"
            f"<td>{escape(d.reason)}</td></tr>"
        )
    rows.append("</tbody></table>")
    return "\n".join(rows)


def _waiver_trades(bundle) -> str:
    if not bundle.trades:
        return ""
    items = ["<h3>Trade ideas</h3>", "<ul class='ideas'>"]
    for idea in bundle.trades:
        send = ", ".join(s.player.name for s in idea.you_send)
        get = ", ".join(s.player.name for s in idea.you_get)
        items.append(
            f"<li><strong>{escape(idea.partner)}</strong> — send {escape(send)}, "
            f"get {escape(get)} <span class='note'>your lineup "
            f"+{idea.your_gain:.1f}, theirs +{idea.their_gain:.1f}</span>"
            f"<div class='note'>{escape(idea.rationale)}</div></li>"
        )
    items.append("</ul>")
    return "\n".join(items)


def _waiver_lists(bundle) -> str:
    out: list[str] = []
    if bundle.stashes:
        out.append("<h3>Stash watch</h3><ul class='ideas'>")
        for s in bundle.stashes:
            out.append(f"<li>{escape(s.score.player.name)} "
                       f"({escape(s.score.player.position)}) — {escape(s.reason)}</li>")
        out.append("</ul>")
    if bundle.byes:
        out.append("<h3>Bye-week holes ahead</h3><ul class='ideas'>")
        for gap in bundle.byes:
            out.append(f"<li>Week {escape(str(gap.week))}: only {gap.available} "
                       f"healthy {escape(gap.position)} for {gap.needed} slot(s)</li>")
        out.append("</ul>")
    quoted = [(t, m) for t in bundle.adds for m in t.mentions]
    if quoted:
        out.append("<h3>What the writers said</h3>")
        for target, mention in quoted:
            body = mention.snippet or f"named {target.score.player.name} as an add"
            out.append(
                f"<blockquote class='quote'><strong>"
                f"{escape(target.score.player.name)}</strong> — "
                f"<a href='{escape(mention.url)}'>{escape(mention.author)}</a>: "
                f"{escape(body)}</blockquote>"
            )
    return "\n".join(out)


def _waiver_body(bundle) -> list[str]:
    body: list[str] = []
    if bundle.banner:
        body.append(f"<div class='callout'><strong>{escape(bundle.banner)}"
                    f"</strong></div>")
    if bundle.caveat:
        body.append(f"<div class='callout'>⚠️ {escape(bundle.caveat)}</div>")
    body.append(_waiver_adds_table(bundle))
    body.append(_waiver_drops_table(bundle))
    body.append(_waiver_trades(bundle))
    body.append(_waiver_lists(bundle))
    body.append(_waiver_roster(bundle))
    return [b for b in body if b]


def _waiver_roster(bundle) -> str:
    """The drafted roster, shown when a banner would otherwise stand alone."""
    groups = bundle.roster_by_position()
    if not groups:
        return ""
    out = ["<h3>Your team (drafted)</h3><ul class='ideas'>"]
    for position, players in groups:
        names = ", ".join(f"{p.name} ({p.team})" if p.team else p.name
                          for p in players)
        out.append(f"<li><strong>{escape(position)}</strong> — "
                   f"{escape(names)}</li>")
    out.append("</ul>")
    return "".join(out)


def build_waivers_html(week: int, bundles: Sequence, generated_on: str) -> str:
    """Render the waiver/trade report as a self-contained page.

    ``bundles`` are ``waivers.models.WaiverBundle`` objects, duck-typed here for
    the same reason ``LeagueBundle`` is: ``output/`` must not import upward.
    Multi-league renders each as a collapsible block, matching the start/sit
    dashboard so the two pages read as one site.
    """
    sections = [
        f"<h1>🔄 Week {escape(str(week))} waiver wire &amp; trades</h1>",
        f"<div class='meta'>Generated {escape(generated_on)} · "
        f"{len(bundles)} league(s)</div>",
    ]
    if not bundles:
        sections.append("<p class='note'>No configured league could be scored.</p>")

    multi = len(bundles) > 1
    for b in bundles:
        if multi:
            sections.append("<details class='league' open>")
            sections.append(f"<summary class='league'>{escape(b.label)} — "
                            f"{escape(b.scoring.upper())}</summary>")
        sections += _waiver_body(b)
        if multi:
            sections.append("</details>")

    notes: list[str] = []
    for b in bundles:
        for note in b.notes:
            if note not in notes:
                notes.append(note)
    for note in notes:
        sections.append(f"<p class='note'>{escape(note)}</p>")

    sources: list = []
    for b in bundles:
        for source in b.sources:
            if source not in sources:
                sources.append(source)
    if sources:
        links = ", ".join(f"<a href='{escape(u)}'>{escape(a)}</a>" for a, u in sources)
        sections.append(f"<p class='note'>Sources: {links}</p>")

    return _document(f"Week {escape(str(week))} waivers", "\n".join(sections),
                     active="waivers.html")
