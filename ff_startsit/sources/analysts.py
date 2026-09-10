"""Boone comparisons via Yahoo's published partner widgets. Never a Signal.

The widget's generic scoring default is unreliable. Its position buckets declare
which sets exist; the partner response must independently confirm the requested
set and name the sole contributor before any individual rank is consumed.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional, Sequence
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import requests

from ..cache import atomic_write_text, read_json_or_none
from ..data.matching import ExternalRow, match_rows, is_defense
from ..data.teams import normalize_team
from ..models import Player
from .articles import verified_week

UNIVERSAL_KINDS = frozenset({'QB', 'DST', 'K'})
KINDS = ('QB', 'RB', 'WR', 'TE', 'DST', 'K', 'FLEX', 'OVERALL')
TTL = 3 * 3600
PARTNER = 'https://partners.fantasypros.com/api/v1/consensus-rankings.php'
WIDGET_PATH = '/external/widget/fp-widget.php'
UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'}
VERIFY_REASON = 'byline or week could not be verified in the article'
PARSE_REASON = 'rankings table could not be parsed'
SCORING_REASON = 'scoring of the linked list could not be resolved'
#: Tail of the one ``unavailable`` reason that is position-specific and not a
#: failure: he simply has not posted that list yet. Every other reason is the
#: same for every position, so it belongs in Data status once, not per section
#: — the duplication ``_merge_source_status`` exists to prevent.
NOT_PUBLISHED_TAIL = 'PPR list published for this position'


def not_published_yet(reason: Optional[str]) -> bool:
    """True when ``reason`` means "he has not posted it", not "it broke"."""
    return bool(reason) and reason.endswith(NOT_PUBLISHED_TAIL)


@dataclass(frozen=True)
class AnalystSource:
    name: str = 'Justin Boone'
    index_url: str = 'https://sports.yahoo.com/author/justin-boone/'
    base_url: str = 'https://sports.yahoo.com'


@dataclass(frozen=True)
class RankingList:
    kind: str
    url: str
    scoring: str
    published: Optional[date]
    rows: list[ExternalRow]

    @property
    def universal(self) -> bool:
        return self.kind in UNIVERSAL_KINDS


@dataclass
class AnalystRanks:
    analyst: str
    scoring: str
    lists: list[RankingList] = field(default_factory=list)
    by_position: dict[str, dict[str, float]] = field(default_factory=dict)
    provenance: dict[str, tuple[str, str]] = field(default_factory=dict)
    unavailable: dict[str, str] = field(default_factory=dict)

    def status(self, week: int, label: str) -> tuple[tuple, str]:
        identity = ('analyst', self.analyst, label)
        prefix = f'{label or "This league"}: '
        if self.scoring == 'std':
            return identity, prefix + f'{self.analyst} publishes no standard-scoring rankings — comparison withheld for this league'
        scoring = 'Full-PPR' if self.scoring == 'ppr' else 'Half-PPR'
        if not self.lists:
            reasons = '; '.join(dict.fromkeys(self.unavailable.values())) or 'no published counterpart'
            return identity, prefix + f'no {self.analyst} Week {week} {scoring} rankings ({reasons})'
        sets = ', '.join(sorted({r.kind for r in self.lists if not r.universal}))
        universal = ', '.join(sorted({r.kind for r in self.lists if r.universal}))
        dates = sorted({r.published.isoformat() for r in self.lists if r.published})
        line = prefix + f'{self.analyst} Week {week} {scoring} rankings'
        if dates:
            line += f' (articles published {", ".join(dates)})'
        line += f' → {sets or "none"}'
        if universal:
            line += f'; universal lists → {universal}'
        derived = [f'{pos} from {kind}' for pos, (kind, _) in self.provenance.items()
                   if kind in {'FLEX', 'OVERALL'}]
        if derived:
            line += '; ' + ', '.join(derived)
        if self.unavailable:
            line += '; ' + '; '.join(f'{pos}: {reason}' for pos, reason in self.unavailable.items())
        return identity, line


def _warn(reason: str) -> None:
    print(f'warning: Justin Boone rankings: {reason}', file=sys.stderr)


def _kind(url: str) -> Optional[str]:
    patterns = {
        'QB': r'qb-rankings|quarterback', 'DST': r'\bdst\b|d-st|defense',
        'K': r'kicker', 'RB': r'top-running-backs|rb-rankings',
        'WR': r'top-wide-receivers|wr-rankings', 'TE': r'top-tight-ends|te-rankings',
        'FLEX': r'flex-rankings', 'OVERALL': r'top-players|top-overall-players',
    }
    matches = [kind for kind, pat in patterns.items() if re.search(pat, urlsplit(url).path, re.I)]
    return matches[0] if len(matches) == 1 else None


def _yahoo_url(url: str, base_url: str) -> Optional[str]:
    parsed = urlsplit(urljoin(base_url, url))
    if (parsed.scheme != 'https' or parsed.netloc not in {'sports.yahoo.com', 'ca.sports.yahoo.com'}
            or not parsed.path.startswith('/fantasy/article/')):
        return None
    return base_url + parsed.path


def _current_link(url: str, week: int, season: int) -> bool:
    years = re.findall(r'(?<!\d)(20\d{2})(?!\d)', urlsplit(url).path)
    return bool(re.search(rf'week[-_]?0*{week}(?!\d)', url, re.I)
                and not any(year != str(season) for year in years)
                and 'justin-boone' in url)


class _Links(HTMLParser):
    """Visible scoring headings own following links, until the next heading."""
    def __init__(self):
        super().__init__()
        self.links = []
        self.group = ''
        self.capture = None
        self.text = ''

    def handle_starttag(self, tag, attrs):
        if tag in {'h1', 'h2', 'h3', 'h4', 'p'}:
            self.capture, self.text = tag, ''
            if tag.startswith('h'):
                self.group = ''
        if tag == 'a':
            href = dict(attrs).get('href')
            if href:
                self.links.append((href, self.group))

    def handle_data(self, data):
        if self.capture:
            self.text += data

    def handle_endtag(self, tag):
        if tag == self.capture:
            text = self.text.lower()
            if 'boone' in text and 'rankings' in text and len(text) < 120:
                if 'half-ppr' in text:
                    self.group = 'half'
                elif 'full-ppr' in text or 'ppr' in text:
                    self.group = 'ppr'
            self.capture = None


def _links(body: str, base_url: str) -> list[tuple[str, str]]:
    parser = _Links()
    parser.feed(body)
    links = parser.links + [(u, '') for u in re.findall(
        r'https://(?:ca\.)?sports\.yahoo\.com/fantasy/article/[^"\\<>\s]+', body)]
    return [(u, group) for href, group in links if (u := _yahoo_url(href, base_url))]


def find_ranking_urls(html: str, base_url: str, week: int, season: int) -> dict[tuple[str, str], str]:
    result = {}
    rejected = set()
    for url, group in _links(html, base_url):
        kind = _kind(url)
        if not kind or not _current_link(url, week, season):
            continue
        token = 'ppr' if 'full-ppr' in url else ('half' if 'half-ppr' in url else '')
        if group and token and group != token:
            rejected.add(url)
            _warn('scoring heading contradicts linked article')
            continue
        scoring = '' if kind in UNIVERSAL_KINDS else group or token
        if scoring or kind in UNIVERSAL_KINDS:
            result.setdefault((kind, scoring), url)
    return {key: url for key, url in result.items() if url not in rejected}


def widget_nodes(body: str) -> list[dict]:
    """Decode observed Next.js strings and node objects without executing JS.

    raw_decode is bracket/string aware, like experts._json_array_at; a regex
    ending at the first bracket would truncate URLs and nested content.
    """
    decoder = json.JSONDecoder()
    nodes = {}
    for match in re.finditer(r'self\.__next_f\.push\(', body):
        try:
            chunk, _ = decoder.raw_decode(body[match.end():])
        except ValueError:
            continue
        if not isinstance(chunk, list) or len(chunk) < 2 or not isinstance(chunk[1], str):
            continue
        for start in re.finditer(r'\{"nodeId"', chunk[1]):
            try:
                node, _ = decoder.raw_decode(chunk[1][start.start():])
            except ValueError:
                continue
            if node.get('type') == 'rankingPro':
                nodes[json.dumps(node, sort_keys=True)] = node
    return list(nodes.values())


def widget_sets(node: dict, week: int, season: int) -> dict[tuple[str, str], dict]:
    """Validate URL/config agreement and return only explicitly offered buckets."""
    parsed = urlsplit(node.get('url', ''))
    if parsed.scheme != 'https' or parsed.netloc != 'partners.fantasypros.com' or parsed.path != WIDGET_PATH:
        raise ValueError('unrecognized ranking widget')
    query = parse_qs(parsed.query, keep_blank_values=True)
    fields = {'sport': 'sport', 'wtype': 'wtype', 'year': 'year', 'week': 'week',
              'filters': 'proFilters', 'expert': 'expert', 'positions': 'positions',
              'half_positions': 'half_positions', 'ppr_positions': 'ppr_positions'}
    for param, field_name in fields.items():
        if query.get(param) != [node.get(field_name)]:
            raise ValueError('ranking widget metadata contradicts URL')
    if any(node.get(k) != v for k, v in {'sport': 'NFL', 'wtype': 'ST', 'year': str(season), 'week': str(week)}.items()):
        raise ValueError('ranking widget served another season or week')
    if not str(node.get('proFilters', '')).isdigit() or not str(node.get('expert', '')).isdigit():
        raise ValueError('ranking widget must name one contributor')
    result = {}
    for field_name, scoring in [('positions', ''), ('half_positions', 'half'), ('ppr_positions', 'ppr')]:
        for pos in node[field_name].split(':'):
            kind = {'FLX': 'FLEX', 'ALL': 'OVERALL'}.get(pos, pos)
            if kind not in KINDS or (not scoring and kind not in UNIVERSAL_KINDS):
                continue
            key = (kind, '' if kind in UNIVERSAL_KINDS else scoring)
            result[key] = node
    return result


def parse_rankings(text: str, node: dict, kind: str, scoring: str, week: int, season: int) -> list[ExternalRow]:
    prefix = 'FPW.rankingsCB('
    if not text.startswith(prefix):
        raise ValueError(PARSE_REASON)
    data, end = json.JSONDecoder().raw_decode(text[len(prefix):])
    if text[len(prefix) + end:].strip() not in {')', ');'} or not isinstance(data, dict):
        raise ValueError(PARSE_REASON)
    contributor = node['proFilters']
    expected = {'sport': 'NFL', 'ranking_type_name': 'weekly', 'year': str(season),
                'week': str(week), 'position_id': {'FLEX': 'FLX', 'OVERALL': 'ALL'}.get(kind, kind),
                'scoring': 'PPR' if scoring == 'ppr' else 'HALF', 'filters': contributor}
    if any(data.get(k) != v for k, v in expected.items()):
        raise ValueError('ranking response season, week, position or scoring mismatch')
    if data.get('expert_names') != {contributor: 'Justin Boone'} or data.get('total_experts') != 1:
        raise ValueError('ranking response did not name Justin Boone as its sole contributor')
    players = data.get('players')
    if not isinstance(players, list) or data.get('count') != len(players):
        raise ValueError(PARSE_REASON)
    rows = []
    for row in players:
        value = row.get('experts', {}).get(contributor)
        # No aggregate fallback, including for a player unranked by Boone.
        if isinstance(value, bool):
            raise ValueError(PARSE_REASON)
        rank = float(value)
        rows.append(ExternalRow(row['player_name'], row.get('player_team_id'),
                                row['player_position_id'], rank))
    validate_rows(rows, kind)
    return rows


def validate_rows(rows: list[ExternalRow], kind: str) -> None:
    minimum = 20 if kind in {'FLEX', 'OVERALL'} else 10
    if len(rows) < minimum or rows[0].value != 1:
        raise ValueError(PARSE_REASON)
    last = 0
    seen = set()
    good = 0
    for row in rows:
        if (not math.isfinite(row.value) or row.value <= last or row.value != int(row.value)
                or row.key() in seen or not isinstance(row.name, str)):
            raise ValueError(PARSE_REASON)
        last = row.value
        seen.add(row.key())
        good += bool(normalize_team(row.team) and len(row.name.split()) >= 2)
        if kind not in {'FLEX', 'OVERALL'} and row.position != kind:
            raise ValueError(PARSE_REASON)
        if kind == 'FLEX' and row.position not in {'RB', 'WR', 'TE'}:
            raise ValueError(PARSE_REASON)
    if good / len(rows) < .6:
        raise ValueError(PARSE_REASON)


class AnalystFetcher:
    """Shared per run; caches include failures and are independent of rosters."""
    def __init__(self, season: int, data_dir: Optional[Path] = None,
                 session: Optional[requests.Session] = None, timeout: int = 20):
        self.source = AnalystSource()
        self.season = season
        self.data_dir = data_dir
        self.session = session or requests.Session()
        self.timeout = timeout
        self._pages = {}
        self._catalogs = {}
        self._lists = {}
        self._overall_urls = {}
        self.unavailable = {}

    def _cached(self, key, build):
        path = self.data_dir / f'analyst-boone-widget-v1-{self.season}-{key}.json' if self.data_dir else None
        if path:
            stored = read_json_or_none(path)
            if isinstance(stored, dict):
                stamp = stored.get('fetched')
                if (isinstance(stamp, (int, float)) and 0 <= time.time() - stamp < TTL
                        and isinstance(stored.get('value'), dict)
                        and any(isinstance(stored['value'].get(k), str)
                                for k in ('body', 'text', 'reason'))):
                    return stored['value']
        value = build()
        if path:
            try:
                atomic_write_text(path, json.dumps({'fetched': time.time(), 'value': value}))
            except OSError:
                _warn('disk cache could not be written; continuing without it')
        return value

    def _get(self, url: str) -> str:
        # Do not follow unverified redirects into another host or article.
        response = self.session.get(url, headers=UA, timeout=self.timeout, allow_redirects=False)
        if response.status_code != 200:
            raise ValueError('HTTP response unavailable')
        return response.text

    def _page(self, url: str, week: int) -> dict:
        page_key = (week, url)
        if page_key not in self._pages:
            key = f'{week}-page-{hashlib.sha256(url.encode()).hexdigest()[:20]}'
            def build():
                try:
                    return {'body': self._get(url)}
                except Exception:
                    return {'reason': 'author index unreachable' if url == self.source.index_url else 'article unreachable'}
            self._pages[page_key] = self._cached(key, build)
        return self._pages[page_key]

    def _catalog(self, week: int):
        if week in self._catalogs:
            return self._catalogs[week]
        page = self._page(self.source.index_url, week)
        catalog, reasons = {}, []
        if not isinstance(page.get('body'), str):
            reasons.append(page.get('reason', 'author index unreachable'))
        else:
            links = _links(page['body'], self.source.base_url)
            rejected = set()
            groups = {}
            for url, group in links:
                token = 'ppr' if 'full-ppr' in url else ('half' if 'half-ppr' in url else '')
                if group:
                    groups.setdefault(url, set()).add(group)
                    if token and group != token:
                        rejected.add(url)
            rejected.update(url for url, values in groups.items() if len(values) > 1
                            and _kind(url) not in UNIVERSAL_KINDS)
            if rejected:
                reasons.append('scoring heading contradicts linked article')
                _warn(reasons[-1])
            links = [(url, group) for url, group in links if url not in rejected]
            hubs = list(dict.fromkeys(url for url, _ in links if _current_link(url, week, self.season)
                         and re.search(r'/justin-boones-fantasy-football-rankings-for-week-', url)))
            # Verified weekly hub publishes several position buckets. Reuse its
            # nodes; individual articles fill only positions it did not publish.
            candidates = hubs[:1]
            candidates += list(dict.fromkeys(url for url, _ in links
                if _current_link(url, week, self.season) and _kind(url)))
            for url in candidates:
                # Individual OVERALL is lazy; all other article bodies are only
                # needed if the hub did not already offer their kind/scoring.
                kind = _kind(url)
                token = 'ppr' if 'full-ppr' in url else ('half' if 'half-ppr' in url else '')
                if kind:
                    key = (kind, '' if kind in UNIVERSAL_KINDS else token)
                    if key in catalog or kind == 'OVERALL':
                        continue
                    if not token and all((kind, s) in catalog for s in ('half', 'ppr')):
                        continue
                result = self._article(url, week)
                if 'reason' in result:
                    reasons.append(result['reason'])
                else:
                    for key, node in result['sets'].items():
                        if key[0] not in UNIVERSAL_KINDS and url in groups and key[1] not in groups[url]:
                            reasons.append(SCORING_REASON)
                            continue
                        catalog.setdefault(key, (url, result['published'], node))
            self._overall_urls[week] = list(dict.fromkeys(url for url, _ in links
                if _current_link(url, week, self.season) and _kind(url) == 'OVERALL'))
        if not catalog and not reasons:
            reasons.append(f'author index listed no week-{week} ranking article')
        self._catalogs[week] = catalog, reasons
        return catalog, reasons

    def _article(self, url: str, week: int) -> dict:
        page = self._page(url, week)
        if not isinstance(page.get('body'), str):
            return {'reason': page.get('reason', 'article unreachable')}
        verified, published = verified_week(page['body'], self.source.name, self.season, week)
        if not verified:
            return {'reason': VERIFY_REASON}
        try:
            sets, conflicts = {}, set()
            for node in widget_nodes(page['body']):
                for key, value in widget_sets(node, week, self.season).items():
                    if key in sets and sets[key] != value:
                        conflicts.add(key)
                    sets[key] = value
            for key in conflicts:
                sets.pop(key)
            kind = _kind(url)
            if kind and kind != 'OVERALL':
                sets = {key: node for key, node in sets.items() if key[0] == kind}
            token = 'ppr' if 'full-ppr' in url else ('half' if 'half-ppr' in url else '')
            if token and any(key[1] != token for key in sets if key[0] not in UNIVERSAL_KINDS):
                return {'reason': SCORING_REASON}
            return {'sets': sets, 'published': published} if sets else {'reason': SCORING_REASON}
        except Exception:
            return {'reason': SCORING_REASON}

    def _ranking(self, kind: str, scoring: str, week: int) -> tuple[Optional[RankingList], str]:
        scoring = '' if kind in UNIVERSAL_KINDS else scoring
        key = (week, kind, scoring)
        if key in self._lists:
            return self._lists[key]
        catalog, reasons = self._catalog(week)
        if kind == 'OVERALL' and (kind, scoring) not in catalog:
            for url in self._overall_urls.get(week, []):
                result = self._article(url, week)
                for pair, node in result.get('sets', {}).items():
                    if pair not in catalog:
                        catalog[pair] = (url, result['published'], node)
                        self._lists.pop((week, pair[0], pair[1]), None)
        entry = catalog.get((kind, scoring))
        if entry is None:
            reason = '; '.join(dict.fromkeys(reasons)) or f'no {"full" if scoring == "ppr" else "half"} PPR list published for this position'
            self._lists[key] = None, reason
        else:
            url, published, node = entry
            params = dict(callback='FPW.rankingsCB', sport='NFL', year=self.season, week=week,
                          position={'FLEX': 'FLX', 'OVERALL': 'ALL'}.get(kind, kind), experts='show',
                          id=node['expert'], type='ST', scoring='PPR' if scoring == 'ppr' else 'HALF',
                          filters=node['proFilters'], widget='ST')
            def build():
                try:
                    text = self._get(PARTNER + '?' + urlencode(params))
                    parse_rankings(text, node, kind, scoring, week, self.season)
                    return {'text': text}
                except (ValueError, TypeError, KeyError, AttributeError) as exc:
                    return {'reason': str(exc) if isinstance(exc, ValueError) else PARSE_REASON}
                except Exception:
                    return {'reason': 'ranking data unreachable'}
            data = self._cached(f'{week}-{kind}-{scoring or "universal"}', build)
            try:
                if not isinstance(data.get('text'), str):
                    raise ValueError(data.get('reason', PARSE_REASON))
                rows = parse_rankings(data['text'], node, kind, scoring, week, self.season)
                self._lists[key] = RankingList(kind, url, scoring, published, rows), ''
            except Exception as exc:
                self._lists[key] = None, str(exc) if isinstance(exc, ValueError) else PARSE_REASON
        return self._lists[key]

    def fetch(self, players: Sequence[Player], week: int, scoring: str) -> AnalystRanks:
        result = AnalystRanks(self.source.name, scoring)
        if scoring not in {'half', 'ppr'}:
            result.unavailable['league'] = f'league scoring ({scoring}) has no published counterpart'
            self.unavailable[self.source.name] = result.unavailable['league']
            _warn(result.unavailable['league'])
            return result
        try:
            for pos in sorted({p.position for p in players}):
                kind = 'DST' if is_defense(pos) else pos
                ranking, reason = self._ranking(kind, scoring, week)
                derived = False
                if ranking is None:
                    fallbacks = ['FLEX', 'OVERALL'] if kind in {'RB', 'WR', 'TE'} else ['OVERALL']
                    for fallback in fallbacks:
                        candidate, _ = self._ranking(fallback, scoring, week)
                        if fallback == 'OVERALL' and candidate is None:
                            # The observed Overall article is a tabbed widget,
                            # offering positional lists rather than an ALL list.
                            # Its lazy discovery can resolve the original kind.
                            direct, _ = self._ranking(kind, scoring, week)
                            if direct:
                                ranking = direct
                                break
                            if kind in {'RB', 'WR', 'TE'}:
                                candidate, _ = self._ranking('FLEX', scoring, week)
                        if candidate and any(row.position == kind for row in candidate.rows):
                            ranking, derived = candidate, True
                            break
                if ranking is None:
                    result.unavailable[pos] = reason
                    continue
                if ranking not in result.lists:
                    result.lists.append(ranking)
                rows = [r for r in ranking.rows if r.position == kind]
                if derived:
                    rows = [replace(row, value=float(i)) for i, row in enumerate(rows, 1)]
                result.by_position[pos] = {key: row.value for key, row in
                    match_rows([p for p in players if p.position == pos], rows).matched.items()}
                result.provenance[pos] = ranking.kind, ranking.scoring
                if not result.by_position[pos]:
                    result.unavailable[pos] = 'parsed rankings named nobody on this roster'
        except Exception:
            result.unavailable['source'] = 'ranking source could not be read'
        reason = '; '.join(dict.fromkeys(result.unavailable.values()))
        self.unavailable[self.source.name] = reason
        if reason:
            _warn(reason)
        return result
