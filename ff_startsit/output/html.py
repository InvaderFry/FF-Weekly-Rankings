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
from typing import TYPE_CHECKING, Optional, Sequence

from ..models import PlayerScore, Recommendation
from .board import (board_cell, boards_legend, section_boards, waiver_cell,
                    waiver_legend)
from .render import (DEPTH_LEGEND, LINEUP_UNSCORED_NOTE, no_playable_line, unranked_note,
                     flat_signal_note, lineup_unscored_keys, conflict_lines, section_cell)
from ..sources.journalists import JournalistView

if TYPE_CHECKING:                    # the duck-typed bundle, named for the reader
    from ..report import LeagueBundle

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
.callout.analyst { background: rgba(88, 166, 255, .12); border-left-color: #58a6ff; }
.analyst-note { color: #8b949e; margin: .7rem 0; font-size: .9rem; }
th.expert, td.expert { background: rgba(88, 166, 255, .06); }
.callout { background: rgba(210, 153, 34, .16); border-left: 3px solid #d29922;
           padding: .5rem .75rem; border-radius: 4px; margin: .35rem 0 .75rem; }
.note { color: #9aa0ad; font-size: .85rem; margin: .35rem 0 1rem; }
.lone { background: rgba(46, 160, 67, .14); border-radius: 6px; padding: .5rem .7rem; margin: .35rem 0 1rem; }
.start { color: #3fb950; font-weight: 600; }
.flag { color: #d29922; }
footer { color: #6e7481; font-size: .8rem; margin-top: 2rem; }
nav.pages { display: flex; gap: .75rem; margin: 0 0 1rem; font-size: .9rem; }
nav.pages a { color: #9aa0ad; text-decoration: none; padding: .2rem .55rem;
              border: 1px solid #262a33; border-radius: 999px; }
nav.pages a.active { color: #e7e9ee; border-color: #3fb950; }
ul.ideas { margin: .25rem 0 .75rem; padding-left: 1.1rem; }
ul.ideas li { margin: .25rem 0; }
p.status-line { margin: .25rem 0; }
ul.skipped { margin: .35rem 0; padding: .45rem .75rem .45rem 1.6rem;
             background: rgba(248, 81, 73, .14); border-left: 3px solid #f85149;
             border-radius: 4px; }
details.status summary { cursor: pointer; color: #9aa0ad; font-size: .9rem; margin: .35rem 0; }
details.status ul { font-size: .85rem; color: #c6cad3; }
.moves { background: rgba(46, 160, 67, .10); border: 1px solid rgba(46, 160, 67, .45);
         border-radius: 8px; padding: .6rem .9rem; margin: .5rem 0 1.25rem; }
.moves h3 { margin: .1rem 0 .4rem; color: #e7e9ee; }
.moves ol { margin: 0; padding-left: 1.3rem; }
.moves li { margin: .3rem 0; }
/* Waivers: league tabs (radio buttons + sibling selectors; see _league_tabs). */
.leagues { margin-top: 1.25rem; }
.leagues > input.tab { position: absolute; opacity: 0; pointer-events: none; }
.leagues > section.league-panel { display: none; }
.tabs { position: sticky; top: 0; z-index: 2; display: flex; flex-wrap: wrap; gap: .4rem;
        padding: .5rem 0; background: #0f1115; border-bottom: 1px solid #262a33; }
.tabs label { cursor: pointer; padding: .3rem .75rem; border: 1px solid #262a33;
              border-radius: 999px; color: #9aa0ad; font-size: .92rem; }
.tabs label small { color: #6e7481; font-size: .75rem; }
h2.league-title { font-size: 1.3rem; margin: 1.25rem 0 .5rem; }
/* Waivers: the writers' board folds its single-writer rows (board_visible_keys). */
.board > input.more { position: absolute; opacity: 0; pointer-events: none; }
.board .minor { display: none; }
.board > input.more:checked ~ .tablewrap tr.minor { display: table-row; }
.board > input.more:checked ~ blockquote.minor,
.board > input.more:checked ~ h4.minor { display: block; }
label.more-btn { display: inline-block; cursor: pointer; margin: .1rem 0 .4rem;
                 padding: .25rem .7rem; border: 1px solid #3a4150; border-radius: 6px;
                 color: #c6cad3; font-size: .85rem; }
label.more-btn .open { display: none; }
.board > input.more:checked ~ label.more-btn .open { display: inline; }
.board > input.more:checked ~ label.more-btn .closed { display: none; }
.board > input.more:focus-visible ~ label.more-btn { outline: 2px solid #58a6ff; }
/* Tables scroll sideways inside their own box instead of widening the page
   (_scrollable_tables). The gradients are a shadow at whichever edge has more
   to scroll to: the covers scroll with the content, the shadows stay put. */
.tablewrap { overflow-x: auto; -webkit-overflow-scrolling: touch; margin: .25rem 0 .5rem;
  background:
    linear-gradient(to right, #0f1115 30%, rgba(15, 17, 21, 0)),
    linear-gradient(to left, #0f1115 30%, rgba(15, 17, 21, 0)) 100% 0,
    radial-gradient(farthest-side at 0 50%, rgba(255, 255, 255, .22), rgba(255, 255, 255, 0)),
    radial-gradient(farthest-side at 100% 50%, rgba(255, 255, 255, .22), rgba(255, 255, 255, 0)) 100% 0;
  background-repeat: no-repeat;
  background-size: 40px 100%, 40px 100%, 14px 100%, 14px 100%;
  background-attachment: local, local, scroll, scroll; }
.tablewrap > table { margin: 0; }
/* The first column (the player, on every waiver table) stays put while the
   rest scrolls, so a swiped row still says whose it is. Opaque, with the row
   tints redrawn over it, because a sticky cell shows what scrolls beneath. */
.tablewrap th:first-child, .tablewrap td:first-child {
  position: sticky; left: 0; z-index: 1; background-color: #0f1115;
  box-shadow: 1px 0 0 #262a33; }
.tablewrap tr.top td:first-child {
  background-image: linear-gradient(rgba(46, 160, 67, .14), rgba(46, 160, 67, .14)); }
.tablewrap tr.flagged td:first-child {
  background-image: linear-gradient(rgba(210, 153, 34, .14), rgba(210, 153, 34, .14)); }
@media (max-width: 640px) {
  body { padding: .75rem 1rem; }
  table { font-size: .82rem; }
  th, td { padding: .3rem .4rem; }
  .tabs label { font-size: .85rem; padding: .25rem .6rem; }
}
/* Click-to-sort headers (_SORT_SCRIPT). The arrows are drawn here so a page
   with scripting off shows plain headers. */
th[data-sortable] { cursor: pointer; user-select: none; white-space: nowrap; }
th[data-sortable]::after { content: " ↕"; opacity: .3; font-size: .8em; }
th[aria-sort=ascending]::after { content: " ▲"; opacity: .9; }
th[aria-sort=descending]::after { content: " ▼"; opacity: .9; }
th[data-sortable]:focus-visible { outline: 2px solid #58a6ff; outline-offset: -2px; }
button.sort-reset { display: inline-block; cursor: pointer; margin: .1rem 0 .2rem;
                    padding: .2rem .6rem; border: 1px solid #3a4150; border-radius: 6px;
                    background: transparent; color: #c6cad3; font-size: .8rem; }
button.sort-reset[hidden] { display: none; }
blockquote.quote { margin: .35rem 0 .75rem; padding: .35rem .75rem;
                   border-left: 3px solid #3a4150; color: #c6cad3;
                   font-size: .9rem; }
"""


def _signal_names(rec: Recommendation) -> list[str]:
    return sorted({name for s in rec.scores for name in s.normalized})


def _lineup_table(lineup: Sequence[tuple[str, Optional[PlayerScore]]],
                  unscored: frozenset = frozenset()) -> str:
    rows = ["<tr><th>Slot</th><th>Player</th><th>Team</th>"
            "<th class='num' data-desc>Score</th></tr>"]
    any_unscored = False
    for slot, pick in lineup:
        if pick is None:
            rows.append(f"<tr><td>{escape(slot)}</td><td><em>(no option)</em></td>"
                        "<td></td><td class='num'></td></tr>")
            continue
        team = escape(pick.player.team or "BYE")
        if pick.player.key in unscored:
            any_unscored = True
            verdict = "—"
        else:
            verdict = f"{pick.final:.1f}"
        rows.append(
            f"<tr><td>{escape(slot)}</td><td>{escape(pick.player.name)}</td>"
            f"<td>{team}</td><td class='num'>{verdict}</td></tr>"
        )
    table = "<table>" + "".join(rows) + "</table>"
    if any_unscored:
        table += f"<p class='note'>{escape(LINEUP_UNSCORED_NOTE)}</p>"
    return table


def _position_table(rec: Recommendation) -> str:
    if rec.lone_candidate:
        # Same collapse the digest makes, for the same reason and on the same
        # condition — an empty table plus a note explaining it is not a reading.
        s = rec.scores[0]
        team = escape(s.player.team or "BYE")
        if rec.pick_blocker:
            # The verdict above already says he won't play and why; this line
            # only says why the section is one row.
            html = (f"<p class='lone'>{escape(s.player.name)} ({team}) is your only "
                    f"{escape(s.player.position)} this week.</p>")
            if s.flags:
                html += f"<p class='flag'>{escape('; '.join(s.flags))}</p>"
            return html
        html = (f"<p class='lone'>✅ <strong>{escape(s.player.name)}</strong> "
                f"({team}) — your only {escape(s.player.position)} this week, so "
                "there was nothing to rank him against and no signal could be "
                "scored.</p>")
        if s.flags:
            html += f"<p class='flag'>{escape('; '.join(s.flags))}</p>"
        return html
    signal_names = _signal_names(rec)
    boards = section_boards(rec)
    cells = ["<th class='num'>#</th>", "<th>Player</th>", "<th>Pos</th>", "<th>Team</th>",
             "<th class='num' data-desc>Score</th>"]
    cells += [f"<th class='num expert' title='{escape(b.label)}'>{escape(b.header)}</th>"
              for b in boards]
    # Signal columns are 0-100 with higher better, so they sort descending first.
    cells += [f"<th class='num' data-desc>{escape(n)}</th>"
              for n in [h.upper() for h in signal_names]]
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
        tds += [f"<td class='num expert'>{escape(section_cell(rec, b, s))}</td>"
                for b in boards]
        for name in signal_names:
            n = s.normalized.get(name)
            blank = n is None or rec.unranked
            tds.append(f"<td class='num'>{'—' if blank else f'{n:.0f}'}</td>")
        flag_html = escape("; ".join(s.flags))
        tds.append(f"<td class='flag'>{flag_html}</td>")
        rows.append(f"<tr{cls}>" + "".join(tds) + "</tr>")

    table = "<table>" + "".join(rows) + "</table>"
    if boards:
        table += f"<p class='note'>{escape(boards_legend(boards))}</p>"
    flat = flat_signal_note(rec)
    if flat:
        # Same sentence the digest carries: a column can look decisive purely
        # because min-max stretched a tiny raw spread across the full 0-100.
        table += f"<p class='note'>{escape(flat)}</p>"
    if rec.unranked and rec.scores:
        # Same note the digest and the terminal table carry — a lone candidate
        # has no range to normalize against, so the columns are blanked.
        table += f"<p class='note'>{escape(unranked_note(rec))}</p>"
    return table


def _position_section(pos: str, rec: Recommendation) -> str:
    parts = [f"<h2>{escape(pos)}</h2>"]
    if rec.close_call:
        notes = "".join(f"<div>• {escape(n)}</div>" for n in rec.notes)
        parts.append(f"<div class='callout'>⚠️ <strong>Close call</strong>{notes}</div>")
    elif rec.pick_blocker:
        parts.append(f"<div class='callout'>🚫 <strong>"
                     f"{escape(no_playable_line(rec))}</strong></div>")
    elif rec.scores and rec.scores[0].final is not None:
        parts.append(f"<div class='start'>✅ Start: {escape(rec.scores[0].player.name)}</div>")
    parts.append(_position_table(rec))
    for line in conflict_lines(rec):
        parts.append(f"<div class='analyst-note'>{escape(line)}</div>")
    if rec.analyst_note:
        parts.append(f"<div class='analyst-note'>{escape(rec.analyst_note)}</div>")
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


def _hole_callouts(lineup) -> list[str]:
    """One callout per lineup hole, with its streamer suggestions."""
    out: list[str] = []
    for hole in getattr(lineup, "holes", None) or ():
        body = f"🚨 <strong>{escape(hole.headline())}</strong>"
        if hole.options:
            body += ("<br><span class='note'>Pick up one of (fresh ESPN free-agent "
                     "read, ordered by this week's FantasyPros rank):</span><ol>"
                     + "".join(f"<li>{escape(o.describe())}</li>" for o in hole.options)
                     + "</ol>")
        elif hole.options_note:
            body += (f"<br><span class='note'>No pickup suggested: "
                     f"{escape(hole.options_note)}.</span>")
        out.append(f"<div class='callout'>{body}</div>")
    return out


def _dashboard_body(lineup: Sequence[tuple[str, Optional[PlayerScore]]],
                    recs: dict[str, Recommendation],
                    banner: Optional[str] = None,
                    journalists: Optional[JournalistView] = None) -> list[str]:
    """The lineup + rankings + journalists sections (no H1), shared by single and
    multi-league dashboards."""
    sections: list[str] = []
    if banner:
        sections.append(f"<div class='callout'><strong>{escape(banner)}</strong></div>")
    sections += _hole_callouts(lineup)
    sections += [
        "<h2>Suggested lineup</h2>",
        _lineup_table(lineup, unscored=lineup_unscored_keys(recs)),
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
PAGES = (("index.html", "Start/sit"), ("waivers.html", "Waivers & trades"),
         ("values.html", "Trade values"))


def _nav(active: str) -> str:
    """Cross-links between the published pages, marking the current one."""
    links = []
    for href, label in PAGES:
        cls = " class='active'" if href == active else ""
        links.append(f"<a href='{escape(href)}'{cls}>{escape(label)}</a>")
    return "<nav class='pages'>" + "".join(links) + "</nav>"


def _board_th(board) -> str:
    """A board's column header. A trade chart's cell leads with its value
    ("26 · RB30"), where higher is better, so it sorts descending first; every
    other board cell leads with a rank ("WR1 · $9", "#154", "K5")."""
    desc = " data-desc" if board.kind == "ros" and board.source_id != "fantasypros_ros" else ""
    return f"<th title='{escape(board.label)}'{desc}>{escape(board.header)}</th>"


#: Click-to-sort for every table on all three pages. Clicking a header cycles
#: best-first, worst-first, then the original order; a sorted table also shows
#: an "Original order" button. Progressive enhancement: with scripting off the
#: tables are exactly as rendered. Sort keys come from the cell text: a leading
#: number ("47 · RB3" -> 47, "1.42", "#154", "bid ~$15" -> 15) or a positional
#: rank ("WR1 · $9" -> 1, "DST6" -> 6); a column most of whose cells read as
#: numbers sorts numerically, anything else alphabetically. Blanks ("—", "off
#: chart", "not posted yet", "n/a") always sort last, whichever way. Headers
#: marked ``data-desc`` (scores, trade values, bids) go high-to-low first; the
#: rest (ranks, depth, names) low-to-high. Sorting the writers' board opens its
#: "Show all" fold first, since a sort can bring a folded row to the top, and
#: the original order closes it again. Nothing is remembered between visits.
_SORT_SCRIPT = r"""
(function () {
  var BLANK = /^(\u2014|-|\u2013|off chart|not posted yet|n\/a|\(no option\))?$/i;
  var RANKED = /^[A-Z]{1,4}(\d+(?:\.\d+)?)(?![\w.])/;
  var NUMBER = /^[+\-]?\d+(?:\.\d+)?(?![\w\/])/;
  function key(cell) {
    var t = (cell ? cell.textContent : '').replace(/\s+/g, ' ').trim();
    if (BLANK.test(t)) { return null; }
    var s = t.replace(/^bid\s+/i, '').replace(/^[~$#]+/, '').replace(/^\u2212/, '-');
    var m = s.match(RANKED);
    if (m) { return {n: parseFloat(m[1]), t: t.toLowerCase()}; }
    m = s.match(NUMBER);
    if (m) { return {n: parseFloat(m[0]), t: t.toLowerCase()}; }
    return {n: null, t: t.toLowerCase()};
  }
  function setup(table) {
    var head = table.tHead ? table.tHead.rows[0] : null;
    var body = table.tBodies[0];
    if (!body) { return; }
    if (!head) {
      head = body.rows[0];
      if (!head || head.querySelector('td')) { return; }
    }
    var rows = [];
    for (var i = 0; i < body.rows.length; i++) {
      if (body.rows[i] !== head) { rows.push(body.rows[i]); }
    }
    if (rows.length < 2) { return; }
    var original = rows.slice();
    var ths = head.cells;
    var state = {col: -1, dir: null};
    var board = table.closest ? table.closest('.board') : null;
    var more = null;
    if (board) {
      for (var c = 0; c < board.children.length; c++) {
        var el = board.children[c];
        if (el.tagName === 'INPUT' && el.classList.contains('more')) { more = el; }
      }
    }
    var opened = false;
    var reset = document.createElement('button');
    reset.type = 'button';
    reset.className = 'sort-reset';
    reset.textContent = '\u21ba Original order';
    reset.hidden = true;
    var anchor = table.parentNode && table.parentNode.classList &&
                 table.parentNode.classList.contains('tablewrap') ? table.parentNode : table;
    anchor.parentNode.insertBefore(reset, anchor);
    function place(order) {
      for (var i = 0; i < order.length; i++) { body.appendChild(order[i]); }
    }
    function mark() {
      for (var i = 0; i < ths.length; i++) {
        if (i === state.col) {
          ths[i].setAttribute('aria-sort', state.dir === 'asc' ? 'ascending' : 'descending');
        } else {
          ths[i].removeAttribute('aria-sort');
        }
      }
      reset.hidden = state.col < 0;
    }
    function restore() {
      state = {col: -1, dir: null};
      place(original);
      if (opened && more) { more.checked = false; }
      opened = false;
      mark();
    }
    function sortBy(col, dir) {
      var keyed = original.map(function (row, i) {
        return {row: row, i: i, k: key(row.cells[col])};
      });
      var numbers = 0, words = 0;
      keyed.forEach(function (x) {
        if (x.k === null) { return; }
        if (x.k.n !== null) { numbers++; } else { words++; }
      });
      var numeric = numbers > 0 && numbers >= words;
      var sign = dir === 'asc' ? 1 : -1;
      keyed.sort(function (a, b) {
        var va = a.k === null ? null : (numeric ? a.k.n : a.k.t);
        var vb = b.k === null ? null : (numeric ? b.k.n : b.k.t);
        if (va === null || vb === null) {
          if (va === vb) { return a.i - b.i; }
          return va === null ? 1 : -1;
        }
        if (va < vb) { return -sign; }
        if (va > vb) { return sign; }
        return a.i - b.i;
      });
      if (more && !more.checked) { more.checked = true; opened = true; }
      place(keyed.map(function (x) { return x.row; }));
      state = {col: col, dir: dir};
      mark();
    }
    function click(col) {
      var first = ths[col].hasAttribute('data-desc') ? 'desc' : 'asc';
      var second = first === 'asc' ? 'desc' : 'asc';
      if (state.col !== col) { sortBy(col, first); }
      else if (state.dir === first) { sortBy(col, second); }
      else { restore(); }
    }
    for (var j = 0; j < ths.length; j++) {
      (function (col) {
        var th = ths[col];
        th.setAttribute('data-sortable', '');
        th.tabIndex = 0;
        th.title = (th.title ? th.title + ' \u2014 ' : '') + 'Click to sort';
        th.addEventListener('click', function () { click(col); });
        th.addEventListener('keydown', function (e) {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); click(col); }
        });
      })(j);
    }
    reset.addEventListener('click', restore);
  }
  var tables = document.querySelectorAll('table');
  for (var i = 0; i < tables.length; i++) { setup(tables[i]); }
})();
"""


def _scrollable_tables(body: str) -> str:
    """Wrap every ``<table>`` in a ``div.tablewrap`` that scrolls sideways.

    The writers' board and the position tables are wider than a phone, and an
    unwrapped table widened the whole page, so the right-hand columns were cut
    off (or the page scrolled sideways as a whole). Done here, once, so every
    table on all three pages gets it and no renderer can forget. Safe as a text
    substitution: player names and notes are escaped before they get here, so a
    literal ``<table`` can only be markup this module wrote.
    """
    import re
    return re.sub(r"<table\b", "<div class='tablewrap'><table", body).replace(
        "</table>", "</table></div>")


def _document(title: str, body: str, active: str = "index.html") -> str:
    body = _scrollable_tables(body)
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
        f"<script data-sort>{_SORT_SCRIPT}</script>\n"
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
    from ..data_status import single_status
    sections.append(single_status(week, label, recs).html())
    sections += _dashboard_body(lineup, recs, banner=banner, journalists=journalists)
    title = "Week " + escape(str(week)) + " start/sit" + (f" · {escape(label)}" if label else "")
    return _document(title, "\n".join(sections))


def build_multi_dashboard_html(week: int, bundles: Sequence["LeagueBundle"],
                               generated_on: str) -> str:
    """Render several leagues into one page, one tab per league.

    ``bundles`` are ``report.LeagueBundle`` objects (duck-typed here to avoid an
    import cycle): each contributes a tab (``_league_tabs``, shared with the
    waivers page) with its own lineup and rankings, so the whole week is one
    self-contained file.
    """
    sections = [
        f"<h1>🏈 Week {escape(str(week))} start/sit</h1>",
        f"<div class='meta'>Generated {escape(generated_on)} · "
        f"{len(bundles)} league(s)</div>",
    ]
    from ..data_status import bundle_status
    status = bundle_status(bundles)
    if status:
        sections.append(status.html())
    if len(bundles) > 1:
        sections.append(_league_tabs(bundles, lambda b, i: _dashboard_body(
            b.lineup, b.recs, banner=b.banner, journalists=b.journalists)))
    else:
        for b in bundles:
            sections += _dashboard_body(b.lineup, b.recs, banner=b.banner,
                                        journalists=b.journalists)
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
        "<table><thead><tr><th>Add</th><th>Pos</th><th class='num'>Depth</th>"
        "<th>Drop for him</th><th data-desc>Bid</th><th>Why</th></tr></thead><tbody>"
    ]
    for t in bundle.adds:
        drop = t.drop_label
        why = "; ".join(t.reasons[1:]) or "—"
        depth = "—" if t.depth_ratio is None else f"{t.depth_ratio:.2f}"
        rows.append(
            f"<tr class='top'><td class='start'>{escape(t.score.player.name)}</td>"
            f"<td>{escape(t.score.player.position)}</td>"
            f"<td class='num'>{depth}</td>"
            # No margin across positions — the two scores were normalized in
            # separate candidate sets, so their difference isn't a quantity.
            f"<td>{escape(drop)}" + (f" <span class='note'>+{t.margin:.1f}</span>"
                                     if t.margin is not None else "") + "</td>"
            f"<td>{escape(t.bid or '—')}</td>"
            f"<td>{escape(why)}</td></tr>"
        )
    rows.append("</tbody></table>")
    rows.append(f"<p class='note'>{escape(DEPTH_LEGEND)}</p>")
    # Same sentence the digest and the embed carry: a table of streamers alone
    # says nothing about the positions that decide a week.
    gap = bundle.no_adds_at_positions()
    if gap:
        rows.append(f"<p class='note'>{escape(gap)}</p>")
    return "\n".join(rows)


def _waiver_alternates_table(bundle) -> str:
    """Mirrors ``waivers/render._alternates_table`` — see its note on why it is
    a separate table rather than more rows in the adds table above."""
    if not bundle.alternates:
        return ""
    rows = ["<h3>Also consider adding</h3>",
            "<p class='note'>No roster spot open for these — they clear the same "
            "bar as the table above.</p>",
            "<table><thead><tr><th>Add</th><th>Pos</th><th class='num'>Depth</th>"
            "<th data-desc>Bid</th><th>Why</th></tr></thead><tbody>"]
    for t in bundle.alternates:
        why = "; ".join(t.reasons) or "—"  # no drop line to skip here
        depth = "—" if t.depth_ratio is None else f"{t.depth_ratio:.2f}"
        rows.append(
            f"<tr><td class='start'>{escape(t.score.player.name)}</td>"
            f"<td>{escape(t.score.player.position)}</td>"
            f"<td class='num'>{depth}</td>"
            f"<td>{escape(t.bid or '—')}</td>"
            f"<td>{escape(why)}</td></tr>"
        )
    rows.append("</tbody></table>")
    return "\n".join(rows)


def _waiver_held_back(bundle) -> str:
    """Mirrors ``waivers/render._held_back_section``."""
    if not bundle.held_back:
        return ""
    items = "".join(f"<li>{escape(line)}</li>" for line in bundle.held_back)
    return f"<h3>Held back: the experts don't back these</h3><ul class='ideas'>{items}</ul>"


def _waiver_stream_table(bundle) -> str:
    """Mirrors ``waivers/render._stream_section``."""
    from ..waivers.models import STREAM_TABLE_NOTE
    if not bundle.stream_rows:
        return ""
    cols = list(bundle.stream_columns)
    head = "".join(f"<th>{escape(c)}</th>" for c in cols)
    out = ["<h3>Kicker and defense: this week's experts</h3>"]
    for pos, rows in bundle.stream_rows.items():
        out.append(f"<table><thead><tr><th>{escape(pos)}</th><th>Team</th>"
                   f"<th class='num' data-desc>Score</th><th>FP Wk</th>{head}<th>This tool</th>"
                   "</tr></thead><tbody>")
        for row in rows:
            name, team, score, fp, *rest = row.cells(cols)
            experts, status = rest[:-1], rest[-1]
            label = f"<strong>{escape(name)}</strong> (yours)" if row.mine else escape(name)
            cells = "".join(f"<td class='expert'>{escape(c)}</td>" for c in experts)
            out.append(f"<tr><td>{label}</td><td>{escape(team)}</td>"
                       f"<td class='num'>{escape(score)}</td><td class='expert'>{escape(fp)}</td>"
                       f"{cells}<td>{escape(status)}</td></tr>")
        out.append("</tbody></table>")
    out.append(f"<p class='note'>{escape(STREAM_TABLE_NOTE)}</p>")
    return "".join(out)


def _waiver_drops_table(bundle) -> str:
    if not bundle.drops and not bundle.ir_drops:
        return ""
    # Ordered by overall rest-of-season rank, so that is the column shown —
    # see the note in waivers/render.py._drops_table.
    # Trade-chart columns beside the ROS rank: display only, see
    # waivers/render.py._drops_table.
    from ..waivers.values import chart_boards, chart_cell
    charts = chart_boards(bundle.values) if bundle.values is not None else []
    head = "".join(_board_th(b) for b in charts)
    rows = ["<h3>Conditional drop candidates</h3>",
            "<table><thead><tr><th>Player</th><th>Pos</th>"
            f"<th class='num'>ROS rank</th>{head}<th>Why</th></tr></thead><tbody>"]
    for d in list(bundle.drops) + list(bundle.ir_drops):
        ros = "—" if d.score.season_rank is None else f"{d.score.season_rank:g}"
        cells = "".join(f"<td class='expert'>{escape(chart_cell(b, d.score.player))}</td>"
                        for b in charts)
        rows.append(
            f"<tr><td>{escape(d.score.player.name)}</td>"
            f"<td>{escape(d.score.player.position)}</td>"
            f"<td class='num'>{ros}</td>{cells}"
            f"<td>{escape(d.reason)}</td></tr>"
        )
    rows.append("</tbody></table>")
    return "\n".join(rows)


def _waiver_trades(bundle) -> str:
    if not bundle.trades:
        # One shared definition, as with the adds table above; None means a
        # banner or caveat is standing and already says why there is nothing here.
        reason = bundle.no_trades_reason()
        return (f"<h3>Trade ideas</h3><p class='note'>{escape(reason)}</p>"
                if reason else "")
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
    else:
        # Same sentence the digest carries: an empty stash section is a quiet
        # wire or a gate that held, and the two must not render identically.
        stash_reason = bundle.no_stashes_reason()
        if stash_reason:
            out.append("<h3>Stash watch</h3>"
                       f"<p class='note'>{escape(stash_reason)}</p>")
    if bundle.byes:
        out.append("<h3>Bye-week holes ahead</h3><ul class='ideas'>")
        for gap in bundle.byes:
            out.append(f"<li>Week {escape(str(gap.week))}: only {gap.available} "
                       f"healthy {escape(gap.position)} for {gap.needed} slot(s)</li>")
        out.append("</ul>")
    # With the writers' board on, quotes sit under the board instead.
    quoted = [] if getattr(bundle, "expert_boards", None) else \
        [(t, m) for t in bundle.adds for m in t.mentions]
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


def _waiver_expert_board(bundle, uid: str = "0") -> str:
    """The Tuesday board: every free agent a writer named, side by side.

    Rows outside ``board_visible_keys`` (and their quotes) carry ``minor`` and
    are hidden until the "Show all" checkbox is ticked: CSS only, so with no
    stylesheet every row still shows.
    """
    boards = getattr(bundle, "expert_boards", None)
    if not boards:
        return ""
    from ..waivers.board import board_reasons, board_visible_keys, cell_position
    rows = bundle.expert_rows
    visible = board_visible_keys(rows)
    hidden = len(rows) - sum(1 for r in rows if r.player.key in visible)
    out = ["<h3>What the writers say</h3><div class='board'>"]
    if hidden:
        out.append(f"<input type='checkbox' class='more' id='more-{escape(uid)}'>")
    if rows:
        if hidden:
            out.append(f"<p class='note'>Showing {len(rows) - hidden} of {len(rows)}: "
                       "the top of the list, where the writers agree most, plus "
                       "every player this tool acted on.</p>")
        head = "".join(_board_th(b) for b in boards)
        out.append(f"<table><thead><tr><th>Player</th><th>Pos</th><th>Team</th>{head}"
                   "<th>This tool</th></tr></thead><tbody>")
        for row in rows:
            classes = (["top"] if row.listed_by >= 2 else []) + \
                ([] if row.player.key in visible else ["minor"])
            cls = f" class='{' '.join(classes)}'" if classes else ""
            cells = "".join(f"<td class='expert'>"
                            f"{escape(waiver_cell(row.entries.get(b.source_id), cell_position(b, row.player)))}</td>"
                            for b in boards)
            out.append(f"<tr{cls}><td>{escape(row.player.name)}</td>"
                       f"<td>{escape(row.player.position)}</td>"
                       f"<td>{escape(row.player.team or 'FA')}</td>{cells}"
                       f"<td>{escape(row.tool or '—')}</td></tr>")
        out.append("</tbody></table>")
        if hidden:
            out.append(f"<label class='more-btn' for='more-{escape(uid)}'>"
                       f"<span class='closed'>Show all {len(rows)} players</span>"
                       "<span class='open'>Show fewer</span></label>")
    else:
        out.append("<p class='note'>No free agent in this league was named by a writer this week.</p>")
    out.append(f"<p class='note'>{escape(waiver_legend(boards))}</p>")
    for reason in board_reasons(boards) + list(getattr(bundle, "board_pending", [])):
        out.append(f"<p class='note'>{escape(reason)}</p>")
    quoted = [(row, q) for row in rows for q in row.quotes]
    if quoted:
        shown = any(row.player.key in visible for row, _ in quoted)
        out.append("<h4>Why, in their words</h4>" if shown
                   else "<h4 class='minor'>Why, in their words</h4>")
        for row, q in quoted:
            cls = "quote" if row.player.key in visible else "quote minor"
            out.append(f"<blockquote class='{cls}'><strong>{escape(row.player.name)}</strong> — "
                       f"<a href='{escape(q.url)}'>{escape(q.author)}</a>: {escape(q.snippet)}</blockquote>")
    out.append("</div>")
    return "".join(out)


def _waiver_body(bundle, uid: str = "0") -> list[str]:
    body: list[str] = []
    if bundle.banner:
        body.append(f"<div class='callout'><strong>{escape(bundle.banner)}"
                    f"</strong></div>")
    if bundle.caveat:
        body.append(f"<div class='callout'>⚠️ {escape(bundle.caveat)}</div>")
    if bundle.freshness:
        body.append(f"<div class='callout'>⏳ {escape(bundle.freshness)}</div>")
    body.append(_waiver_roster(bundle))
    body.append(_waiver_moves(bundle))
    # Ordered by what a reader acts on: the decisions first (claims, what didn't
    # fit, what was held back, the K/DEF call, drops, trades, stashes), then the
    # research they rest on (the writers' board and the roster's chart values),
    # which used to open the section and push the claims several screens down.
    adds = _waiver_adds_table(bundle)
    if adds:
        body.append("<h3>Claims</h3>" + adds)
    body.append(_waiver_alternates_table(bundle))
    body.append(_waiver_held_back(bundle))
    body.append(_waiver_stream_table(bundle))
    body.append(_waiver_drops_table(bundle))
    body.append(_waiver_trades(bundle))
    body.append(_waiver_lists(bundle))
    body.append(_waiver_expert_board(bundle, uid))
    body.append(_waiver_roster_values(bundle))
    # Inside the league's own <details>, where the numbers they carry can be
    # attributed. Only run-wide notes go to the shared footer.
    for note in bundle.league_notes:
        body.append(f"<p class='note'>{escape(note)}</p>")
    return [b for b in body if b]


def _waiver_moves(bundle) -> str:
    """The "Your moves" card that opens each league: ``WaiverBundle.moves``,
    which restates the sections below in a few sentences rather than deciding
    anything of its own."""
    moves = bundle.moves()
    if not moves:
        return ""
    items = "".join(f"<li><strong>{escape(m.action)}</strong> {escape(m.detail)}</li>"
                    for m in moves)
    return (f"<div class='moves'><h3>Your moves</h3><ol>{items}</ol>"
            "<p class='note'>A summary of the tables below; details and the "
            "reasons for each are there.</p></div>")


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


def league_slugs(labels: Sequence[str]) -> list[str]:
    """URL-hash names for the league tabs (``waivers.html#AndyLOT``): the label
    with anything but letters, digits, ``-`` and ``_`` collapsed to ``-``,
    made unique, and never ``all`` (the "All leagues" tab's own name)."""
    import re
    slugs: list[str] = []
    for i, label in enumerate(labels):
        base = re.sub(r"[^A-Za-z0-9_-]+", "-", label).strip("-") or f"league-{i + 1}"
        slug, n = base, 2
        while slug.lower() == "all" or slug in slugs:
            slug, n = f"{base}-{n}", n + 1
        slugs.append(slug)
    return slugs


#: Picks the tab named in the URL hash, else the last one this browser chose,
#: and records each choice in both. Without it the tabs still work (CSS radio
#: buttons, first league selected); this only adds the deep link and the memory.
_TABS_SCRIPT = """
(function () {
  var tabs = document.querySelectorAll('input.tab');
  function pick(slug) {
    for (var i = 0; i < tabs.length; i++) {
      if (tabs[i].getAttribute('data-slug') === slug) { tabs[i].checked = true; return true; }
    }
    return false;
  }
  var saved = null;
  try { saved = window.localStorage.getItem('ff-league'); } catch (e) {}
  var hash = decodeURIComponent(window.location.hash.slice(1));
  if (!(hash && pick(hash)) && saved) { pick(saved); }
  for (var i = 0; i < tabs.length; i++) {
    tabs[i].addEventListener('change', function () {
      var slug = this.getAttribute('data-slug');
      try { window.localStorage.setItem('ff-league', slug); } catch (e) {}
      try { history.replaceState(null, '', '#' + slug); } catch (e) {}
    });
  }
})();
"""


def _league_tabs(bundles: Sequence, body) -> str:
    """One tab per league plus "All leagues", each showing that league's section.

    Shared by the start/sit dashboard and the waivers page; ``body(bundle, i)``
    returns the league's section as a list of HTML strings.

    Each manager reads one league, and three stacked leagues made the page three
    times as long as anyone needed. The tabs are radio buttons styled by CSS
    sibling selectors, so they work with scripting off; ``_TABS_SCRIPT`` adds the
    ``#league`` deep link and remembers the last choice. Every league's content
    is still in the page (and all of it shows without CSS or under "All").
    """
    slugs = league_slugs([b.label for b in bundles])
    ids = [f"lg-{i}" for i in range(len(bundles))] + ["lg-all"]
    names = slugs + ["all"]
    rules = [f"#{t}:checked ~ #league-{i}" for i, t in enumerate(ids[:-1])]
    rules.append("#lg-all:checked ~ section.league-panel")
    active = [f"#{t}:checked ~ .tabs label[for='{t}']" for t in ids]
    focus = [f"#{t}:focus-visible ~ .tabs label[for='{t}']" for t in ids]
    out = ["<style>", ", ".join(rules), " { display: block; }\n",
           ", ".join(active), " { color: #e7e9ee; border-color: #3fb950; "
           "background: rgba(46, 160, 67, .14); }\n",
           ", ".join(focus), " { outline: 2px solid #58a6ff; outline-offset: 2px; }",
           "</style><div class='leagues'>"]
    for i, (t, slug) in enumerate(zip(ids, names)):
        checked = " checked" if i == 0 else ""
        out.append(f"<input type='radio' name='league' class='tab' id='{t}' "
                   f"data-slug='{escape(slug)}'{checked}>")
    out.append("<div class='tabs' role='tablist'>")
    for t, b in zip(ids, bundles):
        out.append(f"<label for='{t}'>{escape(b.label)} "
                   f"<small>{escape(b.scoring.upper())}</small></label>")
    out.append("<label for='lg-all'>All leagues</label></div>")
    for i, (b, slug) in enumerate(zip(bundles, slugs)):
        out.append(f"<section class='league-panel' id='league-{i}' data-slug='{escape(slug)}'>")
        out.append(f"<h2 class='league-title'>{escape(b.label)} — "
                   f"{escape(b.scoring.upper())}</h2>")
        out += body(b, i)
        out.append("</section>")
    out.append(f"</div><script data-tabs>{_TABS_SCRIPT}</script>")
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

    from ..data_status import bundle_status
    status = bundle_status(bundles)
    if status:
        sections.append(status.html())
    if len(bundles) > 1:
        sections.append(_league_tabs(bundles, lambda b, i: _waiver_body(b, uid=str(i))))
    else:
        for b in bundles:
            sections += _waiver_body(b)

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


# --- the Wednesday values page --------------------------------------------
def _waiver_roster_values(bundle) -> str:
    """"My roster: rest-of-season value" on the waivers page — the values
    sheet's own table, so Tuesday's and Wednesday's pages read identically."""
    sheet = bundle.values
    if sheet is None or not sheet.boards or not sheet.rows:
        return ""
    return ("<h3>My roster: rest-of-season value</h3>"
            "<p class='note'>Trade charts double as rest-of-season rankings.</p>"
            + _values_table_html(sheet))


def _values_table_html(sheet) -> str:
    from ..waivers.board import board_reasons
    from ..waivers.values import sheet_cell
    out = []
    if sheet.rows:
        head = "".join(_board_th(b) for b in sheet.boards)
        out.append(f"<table><thead><tr><th>Player</th><th>Pos</th><th>Team</th>{head}"
                   "<th>Charts</th></tr></thead><tbody>")
        for row in sheet.rows:
            cls = " class='flagged'" if row.split else ""
            cells = "".join(f"<td class='expert'>"
                            f"{escape(sheet_cell(b, row))}</td>"
                            for b in sheet.boards)
            out.append(f"<tr{cls}><td>{escape(row.player.name)}</td><td>{escape(row.player.position)}</td>"
                       f"<td>{escape(row.player.team or 'FA')}</td>{cells}"
                       f"<td>{escape(row.charts_cell)}</td></tr>")
        out.append("</tbody></table>")
    out.append(f"<p class='note'>{escape(waiver_legend(sheet.boards))} Values are each "
               "writer's own scale, so they're never added across charts.</p>")
    for reason in board_reasons(sheet.boards):
        out.append(f"<p class='note'>{escape(reason)}</p>")
    return "".join(out)


def _values_sheet(sheet) -> str:
    from .board import trade_line, verdict_text
    out = []
    if sheet.banner:
        out.append(f"<div class='callout'><strong>{escape(sheet.banner)}</strong></div>")
    if sheet.caveat:
        out.append(f"<div class='callout'>⚠️ {escape(sheet.caveat)}</div>")
    if not sheet.boards:
        return "".join(out)
    out.append(_values_table_html(sheet))
    out.append("<h3>Trade ideas, judged on each chart</h3>")
    if sheet.verdicts:
        out.append("<ul class='ideas'>")
        for v in sheet.verdicts:
            out.append(f"<li>{escape(trade_line(v.idea))} — <strong>{escape(verdict_text(v))}</strong></li>")
        out.append("</ul><p class='note'>Net = value you get minus value you send, on that "
                   "chart. “Off chart” means someone in the deal has no value there.</p>")
    elif sheet.trades_considered:
        out.append("<p class='note'>No trade idea cleared the waiver pass's fairness checks this week.</p>")
    else:
        out.append("<p class='note'>Trade ideas weren't searched for this league.</p>")
    return "".join(out)


def build_values_html(week: int, sheets: Sequence, generated_on: str) -> str:
    """The Wednesday values page (values.html), styled like the other two."""
    sections = [f"<h1>📈 Week {escape(str(week))} trade values</h1>",
                f"<div class='meta'>Generated {escape(generated_on)} · {len(sheets)} league(s)</div>"]
    if not sheets:
        sections.append("<p class='note'>No configured league could be scored.</p>")
    from ..data_status import bundle_status
    status = bundle_status([s.bundle for s in sheets if s.bundle is not None])
    if status:
        sections.append(status.html())
    if len(sheets) > 1:
        sections.append(_league_tabs(sheets, lambda sheet, i: [_values_sheet(sheet)]))
    else:
        sections += [_values_sheet(sheet) for sheet in sheets]
    return _document(f"Week {escape(str(week))} trade values", "\n".join(sections),
                     active="values.html")
