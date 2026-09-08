"""Find and check FantasyPros expert ids — a setup helper, not a signal.

``FF_PREFERRED_EXPERTS`` is the variable that carries Justin Boone, Jamey
Eisenberg and Dave Richard into the start/sit report *and* the waiver report, and
getting its ids has been the fiddliest part of setup: open the Pick Experts
modal, deselect everyone, apply, read a colon-separated blob out of the URL bar,
then repeat one analyst at a time to learn which number is whose.

This module is **not** a ``Signal``. It has no blend weight, no entry in the
"four places" rule, and nothing in ``pipeline.build_signals``. It exists to be
run once, by hand, at setup.

Two halves, with very different reliability, and the difference is deliberate:

* **Discovery** maps a *name* to an *id*, from the ranking page's own expert
  directory. That page — the one ``ecr.py`` already fetches — embeds
  ``expertGroupsData.expert_data``, a JSON array of every expert it can serve:
  ``{"id": 317, "name": "Justin Boone", "site": "Yahoo! Sports", ...}``. One
  array, one parse, and the answer comes from the same source that decides which
  ids mean anything. It is still markup-dependent, so it returns ``None`` rather
  than a guess, and falls back to the older per-expert-page scrape if the array
  is gone.

  The directory replaced a name-as-URL-slug scheme that read an id off
  ``/nfl/rankings/<name>-consensus-rankings.php``. That was reasonable while it
  worked, but it went quiet when the markup moved: none of its four patterns
  matched any longer, so every name resolved to ``None`` and the whole command
  could only print manual instructions.
* **Verification** (``verify_experts``) parses no rankings markup. It asks the
  directory whether each configured id exists and whose it is, then re-fetches
  each id through the ranking scrape the app already uses and compares the
  results numerically.

Between them they separate three failures a user cannot otherwise tell apart,
because all three render as one missing section: an id that no longer exists, an
id that exists but belongs to somebody else, and a perfectly good id whose ranks
this transport simply cannot filter to.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Iterable, Optional, Sequence

import requests

from ..data.matching import normalize_name
from .journalists import Expert

#: One page per analyst. The slug is derived from their name. Kept only as the
#: fallback for a directory that cannot be read at all — see ``_ID_PATTERNS``.
EXPERT_PAGE_URL = ("https://www.fantasypros.com/nfl/rankings/"
                   "{slug}-consensus-rankings.php")
#: The standalone experts index. Also a fallback: it carries no ranking ids in
#: its current markup, while the rankings page below carries all of them.
EXPERTS_INDEX_URL = "https://www.fantasypros.com/experts/nfl/"
#: Where the expert directory actually lives: one ordinary rankings page, the
#: same one ``verify_experts`` compares ranks on. Using a *ranking* page is the
#: point — an expert listed here is by construction an expert whose ranks this
#: app can ask for, which an entry in a separate staff directory is not.
RANKINGS_PAGE_URL = "https://www.fantasypros.com/nfl/rankings/ppr-rb.php"

_UA = {"User-Agent": "Mozilla/5.0 (ff-startsit)"}

#: Candidate shapes for an expert id, tried in order. The live markup could not
#: be inspected when this was written (the authoring sandbox blocks
#: fantasypros.com), so this is deliberately a chain rather than one pattern —
#: and it yields ``None`` rather than a guess when none of them match. A wrong
#: id is far worse than a missing one: it returns a real ranking, so the report
#: would label some other analyst's numbers with your journalist's name.
_ID_PATTERNS = (
    re.compile(r"[?&;]filters=(\d+)"),
    re.compile(r"['\"]expert_?id['\"]\s*:\s*['\"]?(\d+)"),
    re.compile(r"data-expert-id=['\"](\d+)['\"]"),
    re.compile(r"name=['\"]experts?(?:\[\])?['\"][^>]*value=['\"](\d+)['\"]"),
)

#: Rows of the *old* HTML expert directory: an id and a name. Fallback only.
_DIRECTORY_RE = re.compile(
    r"data-expert-id=['\"](?P<id>\d+)['\"][^>]*>\s*(?P<name>[^<]{2,60})<",
    re.IGNORECASE,
)

#: The embedded JSON directory's key. Its value is an array of expert objects.
_EXPERT_DATA_KEY = '"expert_data"'


def expert_slug(name: str) -> str:
    """"Justin Boone" -> "justin-boone" for the per-expert page URL.

    Reuses ``data.matching.normalize_name`` rather than inventing a second
    name-normalization convention — it already lowercases, folds "D.J." to "DJ",
    drops generational suffixes and collapses whitespace.
    """
    return "-".join(normalize_name(name).split())


def parse_expert_id(html: str) -> Optional[str]:
    """Pull an expert id out of a per-expert page, or None (pure).

    None means "this page did not contain a number I recognize", which the
    caller reports as a failure with manual instructions. It never means "0".
    """
    for pattern in _ID_PATTERNS:
        match = pattern.search(html or "")
        if match:
            return match.group(1)
    return None


def _json_array_at(html: str, key: str) -> Optional[list]:
    """The JSON array that ``key`` maps to, found by bracket matching (pure).

    A regex cannot do this: the array holds 80-odd objects with nested strings
    containing brackets, and ``.*?`` would stop at the first ``]`` inside a URL.
    Counting depth from the opening bracket is both simpler and exact. Returns
    ``None`` for anything it cannot read, so every caller has one failure shape.
    """
    start = html.find(key)
    if start < 0:
        return None
    start = html.find("[", start)
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(html)):
        char = html[i]
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(html[start:i + 1])
                except ValueError:
                    return None
                return parsed if isinstance(parsed, list) else None
    return None  # unterminated


def parse_expert_directory(html: str) -> list[Expert]:
    """Every ranking expert on the page, as (id, name) (pure).

    Reads the embedded ``expert_data`` array first — that is the live shape and
    the one that carries ids — then falls back to the old ``data-expert-id``
    markup so a page that still uses it keeps working. Rows missing an id or a
    name are skipped rather than guessed at; a wrong id is worse than a missing
    one, since it returns a real ranking under the wrong analyst's name.
    """
    out: list[Expert] = []
    seen: set[str] = set()

    for row in _json_array_at(html or "", _EXPERT_DATA_KEY) or []:
        if not isinstance(row, dict):
            continue
        expert_id = str(row.get("id") or "").strip()
        name = " ".join(str(row.get("name") or "").split())
        if not expert_id.isdigit() or not name or expert_id in seen:
            continue
        seen.add(expert_id)
        out.append(Expert(id=expert_id, name=name))
    if out:
        return out

    for match in _DIRECTORY_RE.finditer(html or ""):
        expert_id = match.group("id")
        name = " ".join(match.group("name").split())
        if not name or expert_id in seen:
            continue
        seen.add(expert_id)
        out.append(Expert(id=expert_id, name=name))
    return out


def format_env_line(experts: Sequence[Expert]) -> str:
    """The paste-ready ``FF_PREFERRED_EXPERTS=`` line — the actual deliverable."""
    return "FF_PREFERRED_EXPERTS=" + ",".join(f"{e.id}:{e.name}" for e in experts)


class ExpertFinder:
    """Resolves analyst names to FantasyPros expert ids. Never raises.

    Same constructor shape as ``JournalistFetcher`` (session injectable so tests
    stay offline), and the same warn-and-degrade contract: a failure costs you
    one id and a printed instruction, not the command.

    The directory is fetched at most once per instance and reused by ``find``,
    ``find_all`` and ``list_all`` — resolving three names is one request, not
    three, and all three answers then come from a single consistent snapshot.
    """

    def __init__(self, session: Optional[requests.Session] = None,
                 timeout: int = 20):
        self.session = session or requests.Session()
        self.timeout = timeout
        self._cache: dict[str, Optional[Expert]] = {}
        #: name -> the page we looked at, so the output can show its work.
        self.pages: dict[str, str] = {}
        #: name -> why it could not be resolved, for display.
        self.notes: dict[str, str] = {}
        #: Names a *readable* directory positively did not contain. Kept apart
        #: from ``notes`` because only this set is a settled answer: no id will
        #: work for these, so offering the manual lookup sends the user hunting
        #: for a number that does not exist. Every other failure — a 404, a
        #: markup change — is "couldn't look", where the manual steps are still
        #: the right advice.
        self.unlisted: set[str] = set()
        self._directory_cache: Optional[list[Expert]] = None
        self._directory_read = False

    def directory(self) -> Optional[list[Expert]]:
        """Every expert the rankings page can serve, or ``None`` if unreadable.

        ``None`` and ``[]`` are deliberately distinct: nothing readable means we
        do not know, while a readable-but-empty directory would mean the page
        serves nobody. Callers must not treat the first as the second.
        """
        if self._directory_read:
            return self._directory_cache
        self._directory_read = True
        html = self._get(RANKINGS_PAGE_URL)
        if html is None:
            return None
        experts = parse_expert_directory(html)
        if not experts:
            print("warning: couldn't read the expert directory from "
                  f"{RANKINGS_PAGE_URL} (FantasyPros markup may have changed).",
                  file=sys.stderr)
            return None
        self._directory_cache = experts
        return experts

    def lookup_id(self, expert_id: str) -> Optional[Expert]:
        """The directory's entry for an id — whose it actually is.

        This is what makes a *mislabel* detectable. A wrong-but-live id returns
        real rankings, so nothing downstream looks broken; only the directory
        can say that the number you filed under one analyst's name belongs to
        another.
        """
        for expert in self.directory() or []:
            if expert.id == str(expert_id):
                return expert
        return None

    def find(self, name: str) -> Optional[Expert]:
        """Resolve one analyst's id, from the directory then the per-expert page."""
        key = normalize_name(name)
        if key in self._cache:
            return self._cache[key]
        self._cache[key] = None  # a failure is cached too, not retried

        directory = self.directory()
        if directory is not None:
            match = next((e for e in directory if normalize_name(e.name) == key), None)
            if match is not None:
                self.pages[name] = RANKINGS_PAGE_URL
                # The directory's spelling wins: it is the name FantasyPros will
                # show beside these ranks, so agreeing with it keeps the report's
                # byline and the source's byline the same string.
                self._cache[key] = Expert(id=match.id, name=match.name)
                return self._cache[key]
            self.unlisted.add(name)
            self.notes[name] = (
                f"not one of the {len(directory)} experts FantasyPros currently "
                "publishes weekly NFL ranks for")
            return None

        # No directory at all — fall back to the per-expert page.
        return self._find_on_expert_page(name, key)

    def _find_on_expert_page(self, name: str, key: str) -> Optional[Expert]:
        url = EXPERT_PAGE_URL.format(slug=expert_slug(name))
        self.pages[name] = url
        html = self._get(url)
        if html is None:
            self.notes[name] = "their FantasyPros page could not be fetched"
            return None
        expert_id = parse_expert_id(html)
        if expert_id is None:
            print(f"warning: found {name}'s page but no expert id on it "
                  f"(FantasyPros markup may have changed).", file=sys.stderr)
            self.notes[name] = "their page carried no expert id"
            return None
        self._cache[key] = Expert(id=expert_id, name=name)
        return self._cache[key]

    def find_all(self, names: Iterable[str]) -> tuple[list[Expert], list[str]]:
        """(resolved, unresolved names) for a whole list."""
        found: list[Expert] = []
        missing: list[str] = []
        for name in names:
            expert = self.find(name)
            (found.append(expert) if expert else missing.append(name))
        return found, missing

    def list_all(self) -> list[Expert]:
        experts = self.directory()
        if experts:
            return experts
        html = self._get(EXPERTS_INDEX_URL)
        if html is None:
            return []
        experts = parse_expert_directory(html)
        if not experts:
            print("warning: couldn't read the expert directory "
                  "(FantasyPros markup may have changed).", file=sys.stderr)
        return experts

    def _get(self, url: str) -> Optional[str]:
        try:
            resp = self.session.get(url, headers=_UA, timeout=self.timeout)
        except requests.RequestException as exc:
            print(f"warning: couldn't reach {url}: {exc}", file=sys.stderr)
            return None
        if resp.status_code != 200:
            print(f"warning: {url} returned HTTP {resp.status_code}.", file=sys.stderr)
            return None
        return resp.text


# --- verification ----------------------------------------------------------
#: What ``verify_experts`` compares rankings on. RB is the deepest scoring-
#: sensitive list, so two analysts almost never agree on it exactly — which is
#: what makes an exact match meaningful evidence rather than a coincidence.
VERIFY_POSITION = "RB"


class ExpertCheck:
    """One configured expert's verification result."""

    def __init__(self, expert: Expert, rows: int = 0, problem: str = "",
                 actual_name: str = "", id_ok: Optional[bool] = None):
        self.expert = expert
        self.rows = rows
        self.problem = problem
        #: Whose id this actually is, when the directory could say and it differs.
        self.actual_name = actual_name
        #: Did the directory vouch for this id? ``True`` yes, ``False`` no such
        #: id, ``None`` no directory to ask. This is what tells the *remedy*
        #: apart from the problem: a valid id that returned nothing is not
        #: something a user can fix by looking the id up again.
        self.id_ok = id_ok

    @property
    def ok(self) -> bool:
        return not self.problem

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ExpertCheck({self.expert.name!r}, rows={self.rows}, problem={self.problem!r})"


def _signature(rows) -> tuple:
    """A ranking's identity, for comparing one fetch against another."""
    return tuple((r.name, r.value) for r in rows)


def verify_experts(experts: Sequence[Expert], *, scoring: str = "ppr",
                   session: Optional[requests.Session] = None,
                   timeout: int = 20, fetch=None,
                   directory: Optional[Sequence[Expert]] = None) -> list[ExpertCheck]:
    """Check configured expert ids, and say which of several things is wrong.

    ``fetch`` is injectable (defaulting to the same ``ecr.fetch_scrape_rows`` the
    app already uses for ECR) so tests run offline, the same seam
    ``cmd_calibrate``'s ``outcome_provider`` provides. ``directory`` is the
    rankings page's expert list, supplied by the caller that already holds an
    ``ExpertFinder``; ``None`` means "no directory information", which downgrades
    the diagnosis rather than failing it.

    Five failures, ordered by how specific the diagnosis is. The first two need
    the directory and are new — without it, all five of these render to a user as
    the same missing section:

    1. **No such expert id** — the directory lists every id whose ranks can be
       asked for, so an id that isn't in it is dead. No amount of retrying, and
       no API key, will make it work; the analyst has to be looked up again or
       dropped.
    2. **The id belongs to someone else** — the dangerous one, and the one
       nothing could catch before. A wrong-but-live id returns real, plausible
       numbers, so the report renders another analyst's ranks under your
       journalist's byline and nothing looks broken.
    3. **No rankings at all.** When the directory vouches for the id, this is not
       the id's fault: the public rankings page filters client-side and serves
       the same consensus whatever ``filters`` asks for, so per-journalist ranks
       need the API key. Saying "the id is wrong" there sends the user to fix the
       one thing that isn't broken.
    4. **Identical to unfiltered consensus** — the id is almost certainly wrong.
    5. **Identical to another configured expert** — the filter was ignored for
       every request.
    """
    if fetch is None:
        from .ecr import fetch_scrape_rows as fetch  # local: avoids a cycle
    session = session or requests.Session()
    known = {e.id: e.name for e in (directory or [])}

    def _fetch(filters: Optional[str]):
        try:
            return fetch(session, scoring, VERIFY_POSITION, timeout=timeout,
                         filters=filters)
        except requests.RequestException as exc:
            print(f"warning: rankings fetch failed: {exc}", file=sys.stderr)
            return []

    consensus = _signature(_fetch(None))
    checks: list[ExpertCheck] = []
    signatures: dict[str, tuple] = {}

    for expert in experts:
        listed = known.get(expert.id)
        if directory and listed is None:
            checks.append(ExpertCheck(
                expert, 0,
                f"no expert {expert.id} in FantasyPros' current list of "
                f"{len(known)} weekly rankers — this id is dead, not misread",
                id_ok=False))
            continue
        mislabeled = (listed is not None
                      and normalize_name(listed) != normalize_name(expert.name))
        if mislabeled:
            checks.append(ExpertCheck(
                expert, 0, f"id {expert.id} is {listed}, not {expert.name} — "
                           "these ranks would publish under the wrong byline",
                actual_name=listed, id_ok=True))
            continue

        rows = _fetch(expert.id)
        sig = _signature(rows)
        if not rows:
            if listed is not None:
                problem = ("no ranks came back, but the id is valid — this "
                           "rankings page filters in the browser and serves the "
                           "same consensus whatever is asked for. Per-journalist "
                           "ranks need FANTASYPROS_API_KEY")
            else:
                problem = ("returned no rankings — the id is wrong or has no "
                           "weekly data yet")
            checks.append(ExpertCheck(expert, 0, problem,
                                      actual_name=listed or "",
                                      id_ok=listed is not None or None))
            continue
        signatures[expert.id] = sig
        problem = ""
        if consensus and sig == consensus:
            problem = ("identical to unfiltered consensus — this id is almost "
                       "certainly not this analyst")
        checks.append(ExpertCheck(expert, len(rows), problem,
                                  actual_name=listed or "",
                                  id_ok=listed is not None or None))

    # Cross-expert duplicates: only meaningful once every fetch is in.
    for check in checks:
        if check.problem or check.expert.id not in signatures:
            continue
        mine = signatures[check.expert.id]
        twins = [e_id for e_id, sig in signatures.items()
                 if e_id != check.expert.id and sig == mine]
        if twins:
            check.problem = (f"identical to expert id {', '.join(sorted(twins))} — "
                             "FantasyPros likely ignored the filter")
    return checks
