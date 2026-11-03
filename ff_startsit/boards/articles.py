"""Finding, fetching and verifying a writer's weekly article.

Shared by every article-backed board (Eisenberg's and Boone's waiver columns,
Richard's trade chart). Three rules, all inherited from the sources that came
before:

* **Discovery needs the week.** A link is a candidate only if it names the
  requested week; an undated link is no evidence of this week's article (the
  rule ``waivers/columns.find_column_url`` already follows).
* **Verification reads the article, not the URL.** The JSON-LD byline must be
  the writer, and the headline must name the week, this season, and the
  article's kind ("waiver wire", "trade chart"). A URL slug is never enough.
* **"Not posted yet" is not "broke".** A missing link this early in the week is
  the ordinary Tuesday shape and is worth a retry run; an unreachable page or an
  unreadable layout is not. ``NOT_POSTED`` marks the first so the retry gate and
  the reader can tell them apart.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

import requests

from ..cache import atomic_write_text, read_json_or_none
from ..net import HEADERS
from ..sources.articles import article_metadata

#: Appears in every reason that may mean "the writer hasn't published it yet".
NOT_POSTED = "not posted yet"
#: Article pages are fixed once published; an hour spans one run's leagues and a retry.
TTL = 3600

_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.I)


def not_posted(reason: Optional[str]) -> bool:
    return bool(reason) and NOT_POSTED in reason


def week_links(index_html: str, base_url: str, week: int,
               must: Sequence[str], never: Sequence[str] = ()) -> list[str]:
    """Article links on an author page that name ``week``, in page order.

    ``must``: every one of these appears in the link; ``never``: none of these
    does (Boone also writes a *college* waiver column, also titled by week).
    """
    week_pat = re.compile(rf"week[-_]?0*{int(week)}(?!\d)", re.I)
    found: list[str] = []
    for href in _HREF_RE.findall(index_html or ""):
        low = href.lower()
        if not all(m in low for m in must) or any(n in low for n in never):
            continue
        if not week_pat.search(low):
            continue
        url = href if href.startswith("http") else base_url.rstrip("/") + "/" + href.lstrip("/")
        url = url.split("#", 1)[0]
        if url not in found:
            found.append(url)
    return found


def verified_headline(body: str, author: str, season: int, week: int,
                      must: Sequence[str], never: Sequence[str] = ()) -> Optional[str]:
    """The article's headline if its byline, week, season and kind all check out.

    ``must`` / ``never`` are lower-case phrases the headline must / must not
    contain, e.g. ``("waiver wire",)`` and ``("college",)``.
    """
    week_pat = re.compile(rf"\bweek[\s_-]*0*{int(week)}(?!\d)", re.I)
    for headline, published in article_metadata(body, author):
        low = headline.lower()
        years = re.findall(r"(?<!\d)(20\d{2})(?!\d)", headline)
        if not week_pat.search(headline):
            continue
        if any(y != str(season) for y in years):
            continue
        if not years and not (published and published.year == season):
            continue
        if not all(m in low for m in must) or any(n in low for n in never):
            continue
        return headline
    return None


def _warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


class ArticleFetcher:
    """Fetches pages once per run (and per hour on disk). Shared by every board."""

    def __init__(self, data_dir: Optional[Path] = None,
                 session: Optional[requests.Session] = None, timeout: int = 20):
        self.data_dir = Path(data_dir) if data_dir else None
        self.session = session or requests.Session()
        self.timeout = timeout
        self._memo: dict[str, Optional[str]] = {}

    def get(self, url: str) -> Optional[str]:
        """The page body, or None (warned) when it can't be read. Never raises."""
        if url in self._memo:
            return self._memo[url]
        path = None
        if self.data_dir:
            digest = hashlib.sha256(url.encode()).hexdigest()[:20]
            path = self.data_dir / f"article-{digest}.json"
            stored = read_json_or_none(path)
            if (isinstance(stored, dict) and stored.get("url") == url
                    and isinstance(stored.get("body"), str)
                    and isinstance(stored.get("fetched"), (int, float))
                    and 0 <= time.time() - stored["fetched"] < TTL):
                self._memo[url] = stored["body"]
                return stored["body"]
        try:
            resp = self.session.get(url, headers=HEADERS, timeout=self.timeout)
            if resp.status_code != 200:
                _warn(f"{url} returned HTTP {resp.status_code}")
                body = None
            else:
                body = resp.text
        except requests.RequestException as exc:
            _warn(f"{url} unreachable: {exc}")
            body = None
        if body is not None and path is not None:
            atomic_write_text(path, json.dumps({"url": url, "fetched": time.time(), "body": body}))
        self._memo[url] = body
        return body

    def find_article(self, index_url: str, base_url: str, author: str, season: int,
                     week: int, kind: str, link_must: Sequence[str],
                     headline_must: Sequence[str], never: Sequence[str] = (),
                     accept=None) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """``(url, body, None)`` for this week's verified article, or
        ``(None, None, reason)``.

        ``accept(body)`` is an optional final check that the page is the kind
        we can read (e.g. it has the trade tables); candidates are tried in page
        order until one passes everything.
        """
        index = self.get(index_url)
        if index is None:
            return None, None, f"{author}'s author page unavailable"
        candidates = week_links(index, base_url, week, link_must, never)
        if not candidates:
            # An author page lists recent pieces only, so a missing link is either
            # "not up yet" (this week, the retry case) or "scrolled off" (an old
            # week asked for by hand). Say both rather than guess which.
            return None, None, (f"{author}'s Week {week} {kind} isn't on his author page "
                                f"({NOT_POSTED}, or too old to be listed)")
        reason = None
        for url in candidates:
            body = self.get(url)
            if body is None:
                reason = f"{author}'s Week {week} {kind} unavailable"
                continue
            if verified_headline(body, author, season, week, headline_must, never) is None:
                reason = f"{author}'s Week {week} {kind}: byline or week could not be verified"
                continue
            if accept is not None and not accept(body):
                reason = f"{author}'s Week {week} {kind}: layout not recognised"
                continue
            return url, body, None
        return None, None, reason
