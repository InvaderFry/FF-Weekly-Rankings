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

* **Discovery** maps a *name* to an *id*. FantasyPros publishes a per-expert page
  whose slug is the analyst's name, so we never have to parse the expert picker
  to know whose page we are on — the name is the address. All that is left is
  finding a number on a page already known to be that person's. Still markup-
  dependent, so it tries several shapes and returns ``None`` rather than a wrong
  number.
* **Verification** (``verify_experts``) parses no markup at all. It re-fetches
  each configured id through the ranking scrape that the app already uses and
  compares the results numerically. That makes it the half that can be trusted
  when a page layout changes.
"""

from __future__ import annotations

import re
import sys
from typing import Iterable, Optional, Sequence

import requests

from ..data.matching import normalize_name
from .journalists import Expert

#: One page per analyst. The slug is derived from their name, which is what lets
#: discovery skip the expert picker entirely.
EXPERT_PAGE_URL = ("https://www.fantasypros.com/nfl/rankings/"
                   "{slug}-consensus-rankings.php")
#: The directory of every ranking expert, for ``--list``.
EXPERTS_INDEX_URL = "https://www.fantasypros.com/experts/nfl/"

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

#: Rows of the expert directory: an id and a name, in either order.
_DIRECTORY_RE = re.compile(
    r"data-expert-id=['\"](?P<id>\d+)['\"][^>]*>\s*(?P<name>[^<]{2,60})<",
    re.IGNORECASE,
)


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


def parse_expert_directory(html: str) -> list[Expert]:
    """Best-effort list of (id, name) from the experts index page (pure)."""
    seen: set[str] = set()
    out: list[Expert] = []
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
    """

    def __init__(self, session: Optional[requests.Session] = None,
                 timeout: int = 20):
        self.session = session or requests.Session()
        self.timeout = timeout
        self._cache: dict[str, Optional[Expert]] = {}
        #: name -> the page we looked at, so the output can show its work.
        self.pages: dict[str, str] = {}

    def find(self, name: str) -> Optional[Expert]:
        """Resolve one analyst's id from their per-expert page."""
        key = normalize_name(name)
        if key in self._cache:
            return self._cache[key]
        self._cache[key] = None  # a failure is cached too, not retried

        url = EXPERT_PAGE_URL.format(slug=expert_slug(name))
        self.pages[name] = url
        html = self._get(url)
        if html is None:
            return None
        expert_id = parse_expert_id(html)
        if expert_id is None:
            print(f"warning: found {name}'s page but no expert id on it "
                  f"(FantasyPros markup may have changed).", file=sys.stderr)
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

    def __init__(self, expert: Expert, rows: int = 0, problem: str = ""):
        self.expert = expert
        self.rows = rows
        self.problem = problem

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
                   timeout: int = 20, fetch=None) -> list[ExpertCheck]:
    """Check configured expert ids by comparing the rankings they return.

    This half parses no markup, which is why it stays trustworthy when
    FantasyPros changes a page. ``fetch`` is injectable (defaulting to the same
    ``ecr.fetch_scrape_rows`` the app already uses for ECR) so tests run offline,
    the same seam ``cmd_calibrate``'s ``outcome_provider`` provides.

    Three failures, in order of how specific the diagnosis is:

    1. **No rankings at all** — a dead or malformed id.
    2. **Identical to unfiltered consensus** — the id is almost certainly wrong.
       This is the one the app could not catch before, and the dangerous one: a
       wrong-but-valid id returns real numbers, so the report renders somebody
       else's ranks under your journalist's name and nothing looks broken.
    3. **Identical to another configured expert** — FantasyPros ignored the
       ``filters`` parameter and served consensus to every request.

    What this cannot prove: that an id belongs to the *person you named*. Only
    the per-expert page (``ExpertFinder.find``) ties a number to a name.
    """
    if fetch is None:
        from .ecr import fetch_scrape_rows as fetch  # local: avoids a cycle
    session = session or requests.Session()

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
        rows = _fetch(expert.id)
        sig = _signature(rows)
        if not rows:
            checks.append(ExpertCheck(expert, 0, "returned no rankings — "
                                      "the id is wrong or has no weekly data yet"))
            continue
        signatures[expert.id] = sig
        problem = ""
        if consensus and sig == consensus:
            problem = ("identical to unfiltered consensus — this id is almost "
                       "certainly not this analyst")
        checks.append(ExpertCheck(expert, len(rows), problem))

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
