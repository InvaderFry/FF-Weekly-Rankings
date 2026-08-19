"""The three writers' weekly waiver columns — annotation, never a signal.

Justin Boone (Yahoo), Jamey Eisenberg and Dave Richard (CBS) publish a waiver
piece every Tuesday. Their *rankings* already reach this app through
``sources/journalists.py`` (FantasyPros per-expert ranks, which is structured and
reliable); what a ranking can't carry is "he's the back to own if Pacheco sits",
so this module reads the columns themselves for the names they call out.

Why it can never be a blend signal
----------------------------------

A mention is a binary, unranked, sometimes-absent read from a page whose layout
we don't control. Giving it a ``FF_WEIGHT_*`` entry would put it on the same
footing as ECR, drag every existing start/sit blend along with it, and make the
whole ensemble hostage to a CSS change on cbssports.com. So mentions decorate a
recommendation the ranks already made, exactly as ``journalists.py`` does.

Why the extraction is inverted
------------------------------

Rather than parse each article's structure (fragile, and different on all three
sites), this searches the article text for **names already in your league's free
agent pool**. We never have to be right about the HTML — only about whether a
known player's name appears in it. A layout change costs snippets, not
correctness, and a name we invent can't survive because it isn't in the pool.

Every failure path — network error, paywall, 404, a page that parses to nothing —
returns ``[]`` with a warning. The waiver report is fully useful without this
section, which is why it is fetched last and gated behind ``FF_COLUMN_SCRAPE``.
"""

from __future__ import annotations

import html as html_mod
import re
import sys
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

import requests

from ..data.matching import is_defense, normalize_name
from ..models import Player
from .models import ColumnMention

#: A real browser UA. These are public article pages; the request is one per
#: author per week, and the report links back to each piece it quotes.
_UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/124.0 Safari/537.36")}

_TAG_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
# Sentence boundary that survives initials: a terminator only ends a sentence
# when the character before it isn't a lone capital, so "D.J. Moore" stays whole
# while "now. Also" splits. Without this the snippet for every player with
# initials came back empty.
_SENTENCE_SPLIT = re.compile(r"(?<=[a-z0-9)\]\"'])[.!?]+\s+")

#: Longest snippet we quote from any article.
SNIPPET_MAX = 220


@dataclass(frozen=True)
class ColumnSource:
    """One writer's author index and the site it lives on."""

    author: str
    index_url: str
    base_url: str


#: The three writers the user asked for, in the order they're rendered.
SOURCES: tuple[ColumnSource, ...] = (
    ColumnSource("Justin Boone", "https://sports.yahoo.com/author/justin-boone/",
                 "https://sports.yahoo.com"),
    ColumnSource("Jamey Eisenberg", "https://www.cbssports.com/writers/jamey-eisenberg/",
                 "https://www.cbssports.com"),
    ColumnSource("Dave Richard", "https://www.cbssports.com/writers/dave-richard/",
                 "https://www.cbssports.com"),
)


# --- pure helpers ----------------------------------------------------------
def strip_tags(raw: str) -> str:
    """HTML -> readable text. Deliberately crude: we only need names and sentences."""
    text = _TAG_RE.sub(" ", raw or "")
    text = _ANY_TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", html_mod.unescape(text)).strip()


def find_column_url(index_html: str, base_url: str, week: int) -> Optional[str]:
    """Pick this week's waiver column out of an author index page (pure).

    Prefers a link naming the week; falls back to the most recent waiver link,
    since an author page lists newest first and a column that doesn't put the
    week in its slug is still almost certainly the current one.
    """
    week_pat = re.compile(rf"week[-_]?0*{int(week)}(?!\d)", re.IGNORECASE)
    fallback: Optional[str] = None
    for href in _HREF_RE.findall(index_html or ""):
        if "waiver" not in href.lower():
            continue
        url = href if href.startswith("http") else base_url.rstrip("/") + "/" + href.lstrip("/")
        if week_pat.search(href):
            return url
        if fallback is None:
            fallback = url
    return fallback


def extract_mentions(text: str, author: str, url: str,
                     players: Iterable[Player]) -> list[ColumnMention]:
    """Find pool players named in the article body (pure).

    Searches for names we already know rather than guessing at names from
    capitalization: a two-word capitalized phrase matches coaches, teams and
    headline copy, while a pool name match is a fact.
    """
    if not text:
        return []
    # Both sides go through ``normalize_name`` so the article's "D.J. Moore" and
    # the pool's "DJ Moore" are the same string — the same normalization the ECR
    # join already relies on, rather than a second spelling convention.
    haystack = normalize_name(text)
    sentences = _SENTENCE_SPLIT.split(text)

    out: list[ColumnMention] = []
    seen: set[str] = set()
    for player in players:
        if player.key in seen:
            continue
        # Team defenses are named after the team ("Kansas City", "Bears D/ST"),
        # and football prose says those names constantly — every article about
        # the Chiefs would "mention" the Chiefs defense. There is no honest way
        # to tell a defense recommendation from a passing reference, so they are
        # out of this section entirely.
        if is_defense(player.position):
            continue
        # A one-word name can't be distinguished from ordinary prose either.
        if len((player.name or "").split()) < 2:
            continue
        needle = normalize_name(player.name)
        if len(needle.split()) < 2 or f" {needle} " not in f" {haystack} ":
            continue
        seen.add(player.key)
        out.append(ColumnMention(author=author, url=url, player_key=player.key,
                                 snippet=_snippet(sentences, needle)))
    return out


def _snippet(sentences: Sequence[str], needle: str) -> str:
    for sentence in sentences:
        if needle in normalize_name(sentence):
            clipped = sentence.strip()
            if len(clipped) > SNIPPET_MAX:
                clipped = clipped[:SNIPPET_MAX - 1].rstrip() + "…"
            return clipped
    return ""


def index_mentions(mentions: Iterable[ColumnMention]) -> dict[str, list[ColumnMention]]:
    """Group mentions by player key for the renderers."""
    out: dict[str, list[ColumnMention]] = {}
    for m in mentions:
        out.setdefault(m.player_key, []).append(m)
    return out


# --- fetching --------------------------------------------------------------
class ColumnFetcher:
    """Fetches each writer's weekly column. Never raises; warns and degrades."""

    def __init__(self, sources: Sequence[ColumnSource] = SOURCES,
                 session: Optional[requests.Session] = None, timeout: int = 15):
        self.sources = tuple(sources)
        self.session = session or requests.Session()
        self.timeout = timeout
        #: (author, url) for every column we actually read, for the credits line.
        self.read: list[tuple[str, str]] = []
        #: (author, week) -> (url, article text), or None when it couldn't be
        #: read. One fetcher is shared across every league in a run, and the
        #: writers publish one column a week rather than one per league — so the
        #: fetch happens once and only the (pure) name extraction re-runs per
        #: league, against that league's own pool.
        self._articles: dict[tuple[str, int], Optional[tuple[str, str]]] = {}

    def fetch(self, week: int, players: Sequence[Player]) -> list[ColumnMention]:
        mentions: list[ColumnMention] = []
        for source in self.sources:
            try:
                mentions.extend(self._fetch_one(source, week, players))
            except requests.RequestException as exc:
                print(f"warning: couldn't read {source.author}'s column: {exc}",
                      file=sys.stderr)
        return mentions

    def _fetch_one(self, source: ColumnSource, week: int,
                   players: Sequence[Player]) -> list[ColumnMention]:
        article = self._article(source, week)
        if article is None:
            return []
        url, text = article
        found = extract_mentions(text, source.author, url, players)
        if not found:
            # Usually a paywall interstitial: the page loads, the body doesn't.
            # Also the ordinary case for a league whose pool happens to contain
            # none of the players this writer named.
            print(f"warning: {source.author}'s column named no player in this "
                  f"league's free-agent pool (paywall or layout change?).",
                  file=sys.stderr)
            return []
        if (source.author, url) not in self.read:
            self.read.append((source.author, url))
        return found

    def _article(self, source: ColumnSource,
                 week: int) -> Optional[tuple[str, str]]:
        """(url, article text) for one writer's week, fetched at most once."""
        cache_key = (source.author, week)
        if cache_key in self._articles:
            return self._articles[cache_key]
        self._articles[cache_key] = None  # a failure is cached too, not retried

        index = self._get(source.index_url)
        if index is None:
            return None
        url = find_column_url(index, source.base_url, week)
        if not url:
            print(f"warning: no week-{week} waiver column found for {source.author}.",
                  file=sys.stderr)
            return None
        body = self._get(url)
        if body is None:
            return None

        self._articles[cache_key] = (url, strip_tags(body))
        return self._articles[cache_key]

    def _get(self, url: str) -> Optional[str]:
        resp = self.session.get(url, headers=_UA, timeout=self.timeout)
        if resp.status_code != 200:
            print(f"warning: {url} returned HTTP {resp.status_code}.", file=sys.stderr)
            return None
        return resp.text
