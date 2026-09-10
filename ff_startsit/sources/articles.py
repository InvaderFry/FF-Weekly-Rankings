"""Shared article attribution metadata; URL bylines are never sufficient."""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Iterator, Optional


def article_metadata(body: str, author: str) -> Iterator[tuple[str, Optional[date]]]:
    for raw in re.findall(r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                          body, re.S | re.I):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        pending = [data]
        while pending:
            item = pending.pop()
            if isinstance(item, list):
                pending.extend(item)
            elif isinstance(item, dict):
                if '@graph' in item:
                    pending.append(item['@graph'])
                byline = item.get('author')
                if not isinstance(byline, dict) or byline.get('name') != author:
                    continue
                headline = item.get('headline')
                if not isinstance(headline, str):
                    continue
                try:
                    published = datetime.fromisoformat(item['datePublished'].replace('Z', '+00:00')).date()
                except (ValueError, KeyError, TypeError, AttributeError):
                    published = None
                yield headline, published


def verified_week(body: str, author: str, season: int, week: int) -> tuple[bool, Optional[date]]:
    pattern = re.compile(rf'\bweek[\s_-]*0*{week}(?!\d)', re.I)
    for headline, published in article_metadata(body, author):
        years = re.findall(r'(?<!\d)(20\d{2})(?!\d)', headline)
        if (pattern.search(headline) and not any(y != str(season) for y in years)
                and (str(season) in years or (published and published.year == season))):
            return True, published
    return False, None
