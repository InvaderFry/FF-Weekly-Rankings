"""Production adapter tests: only observed Yahoo/partner responses, offline."""
import gzip
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from ff_startsit.models import Player
from ff_startsit.sources.analysts import (
    AnalystFetcher, SCORING_REASON, VERIFY_REASON,
    _kind, find_ranking_urls, parse_rankings, widget_nodes, widget_sets,
)
from ff_startsit.sources.articles import verified_week

ROOT = Path(__file__).parent / 'fixtures'
DATA = ROOT / 'boone_transport'


def raw(name):
    return gzip.decompress((DATA / name).read_bytes()).decode()


def article(name):
    return gzip.decompress((ROOT / 'yahoo_phase0' / (name + '.html.gz')).read_bytes()).decode()


def node(name='fullppr_flex'):
    return json.loads((DATA / (name + '_node.json')).read_text())


def decoded(name='ppr_flex'):
    s = raw(name + '_ranks.jsonp.gz')
    return json.JSONDecoder().raw_decode(s[len('FPW.rankingsCB('):])[0]


def wrapped(data):
    return 'FPW.rankingsCB(' + json.dumps(data) + ');'


class Session:
    def __init__(self, index=None, hub=None, fail=None):
        self.calls = []
        self.index = article('index') if index is None else index
        self.hub = raw('hub.html.gz') if hub is None else hub
        self.fail = fail

    def get(self, url, **kwargs):
        self.calls.append(url)
        if self.fail:
            raise self.fail
        if '/author/' in url:
            text = self.index
        elif '/fantasy/article/' in url:
            text = self.hub
        else:
            q = parse_qs(urlsplit(url).query)
            key = ('ppr' if q['scoring'] == ['PPR'] else 'half') + '_' + q['position'][0].lower().replace('flx', 'flex')
            text = raw(key + '_ranks.jsonp.gz')
        return SimpleNamespace(status_code=200, text=text)


PLAYERS = [Player('taylor', 'Jonathan Taylor', 'IND', 'RB'),
           Player('achane', "De'Von Achane", 'MIA', 'RB'),
           Player('allen', 'Josh Allen', 'BUF', 'QB')]


def test_observed_hub_exposes_scoring_and_universal_buckets():
    nodes = widget_nodes(raw('hub.html.gz'))
    assert len(nodes) == 2
    sets = {key for n in nodes for key in widget_sets(n, 1, 2026)}
    assert sets == {(k, s) for k in ['RB', 'WR', 'TE', 'FLEX'] for s in ['half', 'ppr']} | {
        ('QB', ''), ('DST', ''), ('K', '')}


def test_observed_index_urls_classify_unambiguously():
    found = find_ranking_urls(article('index'), 'https://sports.yahoo.com', 1, 2026)
    for kind in ['RB', 'WR', 'TE', 'FLEX', 'OVERALL']:
        assert _kind(found[(kind, 'ppr')]) == kind
    assert _kind('https://sports.yahoo.com/fantasy/article/top-running-backs-wr-rankings') is None


@pytest.mark.parametrize('change', [('week-1', 'week-2'), ('week-1', 'week-10'), ('2026-', '2025-')])
def test_discovery_rejects_wrong_week_or_season(change):
    body = article('index').replace(*change)
    found = find_ranking_urls(body, 'https://sports.yahoo.com', 1, 2026)
    assert ('WR', 'ppr') not in found


def test_discovery_drops_unresolved_scoring():
    body = article('index').replace('full-ppr-', '')
    assert ('WR', 'half') not in find_ranking_urls(body, 'https://sports.yahoo.com', 1, 2026)


def test_heading_contradiction_rejected_even_with_embedded_duplicate(capsys):
    body = article('fullppr_flex').replace("Full-PPR Rankings", "Half-PPR Rankings")
    found = find_ranking_urls(body, 'https://sports.yahoo.com', 1, 2026)
    assert ('WR', 'ppr') not in found
    assert 'contradicts' in capsys.readouterr().err


def test_attribution_gate():
    body = article('fullppr_wr')
    assert verified_week(body, 'Justin Boone', 2026, 1)[0]
    assert not verified_week(body, 'Justin Boone', 2026, 2)[0]
    assert not verified_week(body.replace('Justin Boone', 'Other Writer'), 'Justin Boone', 2026, 1)[0]
    assert not verified_week(body.replace('2026', '2025'), 'Justin Boone', 2026, 1)[0]


def test_first_twelve_real_flex_rows():
    rows = parse_rankings(raw('ppr_flex_ranks.jsonp.gz'), node(), 'FLEX', 'ppr', 1, 2026)
    assert [(r.name, r.position, r.value) for r in rows[:12]] == [
        ('Jahmyr Gibbs', 'RB', 1), ("Ja'Marr Chase", 'WR', 2), ('Bijan Robinson', 'RB', 3),
        ('Puka Nacua', 'WR', 4), ('Amon-Ra St. Brown', 'WR', 5), ('Jaxon Smith-Njigba', 'WR', 6),
        ('Christian McCaffrey', 'RB', 7), ('Saquon Barkley', 'RB', 8), ("De'Von Achane", 'RB', 9),
        ('Jonathan Taylor', 'RB', 10), ('Chris Olave', 'WR', 11), ('CeeDee Lamb', 'WR', 12)]


@pytest.mark.parametrize('field,value', [('scoring', 'HALF'), ('week', '2'), ('year', '2025'),
    ('position_id', 'WR'), ('expert_names', {'317': 'Other Writer'}), ('total_experts', 2),
    ('filters', '317:120'), ('sport', 'MLB'), ('ranking_type_name', 'draft')])
def test_partner_response_metadata_must_match(field, value):
    data = decoded(); data[field] = value
    with pytest.raises(ValueError):
        parse_rankings(wrapped(data), node(), 'FLEX', 'ppr', 1, 2026)


@pytest.mark.parametrize('mutation', ['truncated', 'duplicate', 'rank', 'team', 'name', 'missing_expert', 'nan', 'position'])
def test_bad_rows_are_not_rankings(mutation):
    data = decoded()
    if mutation == 'truncated':
        data['players'] = data['players'][:3]; data['count'] = 3
    elif mutation == 'duplicate':
        data['players'][1] = dict(data['players'][0], experts={'317': '2'})
    elif mutation == 'rank':
        data['players'][0]['experts']['317'] = '2'
    elif mutation in {'team', 'name'}:
        for row in data['players']:
            row['player_team_id' if mutation == 'team' else 'player_name'] = 'unknown'
    elif mutation == 'missing_expert':
        data['players'][0]['experts'] = {}
    elif mutation == 'position':
        data['players'][0]['player_position_id'] = 'QB'
    else:
        data['players'][0]['experts']['317'] = 'NaN'
    with pytest.raises((ValueError, TypeError)):
        parse_rankings(wrapped(data), node(), 'FLEX', 'ppr', 1, 2026)


def test_jsonp_trailing_code_is_not_executed():
    with pytest.raises(ValueError):
        parse_rankings(raw('ppr_flex_ranks.jsonp.gz') + 'alert(1)', node(), 'FLEX', 'ppr', 1, 2026)


def test_widget_url_and_metadata_must_agree():
    for bad in [dict(node(), week='2'), dict(node(), proFilters='317:120'),
                dict(node(), url=node()['url'].replace('partners.fantasypros.com', 'evil.example'))]:
        with pytest.raises(ValueError):
            widget_sets(bad, 1, 2026)


def flex_only_hub():
    # Negative mutation of real hub: remove positional buckets to exercise fallback.
    return raw('hub.html.gz').replace('RB%3AWR%3ATE%3AFLX', 'FLX').replace('RB:WR:TE:FLX', 'FLX')


def test_mixed_scoring_uses_full_list_positional_ranks_and_shares_qb():
    session = Session(hub=flex_only_hub())
    fetcher = AnalystFetcher(2026, session=session)
    half = fetcher.fetch(PLAYERS, 1, 'half')
    ppr = fetcher.fetch(PLAYERS, 1, 'ppr')
    assert half.by_position['RB'] == {'taylor': 5, 'achane': 6}
    assert ppr.by_position['RB'] == {'taylor': 6, 'achane': 5}
    assert half.by_position['QB'] == ppr.by_position['QB'] == {'allen': 5}
    assert half.provenance['RB'] == ('FLEX', 'half')
    assert len([url for url in session.calls if 'position=QB' in url]) == 1
    assert len([url for url in session.calls if '/author/' in url]) == 1
    assert not any('position=ALL' in url for url in session.calls)


def test_standard_is_withheld_without_network():
    session = Session(); f = AnalystFetcher(2026, session=session)
    result = f.fetch(PLAYERS, 1, 'std')
    assert not result.by_position and not session.calls
    assert 'no standard-scoring rankings' in result.status(1, 'Dynasty')[1]


def test_disk_cache_spans_processes_and_expires(tmp_path, monkeypatch):
    session = Session(hub=flex_only_hub())
    AnalystFetcher(2026, tmp_path, session).fetch(PLAYERS, 1, 'ppr')
    offline = Session(fail=AssertionError('network should not be used'))
    assert AnalystFetcher(2026, tmp_path, offline).fetch(PLAYERS, 1, 'ppr').by_position['RB']['taylor'] == 6
    assert not offline.calls
    import ff_startsit.sources.analysts as module
    now = module.time.time()
    monkeypatch.setattr(module.time, 'time', lambda: now + 3 * 3600 + 1)
    assert not AnalystFetcher(2026, tmp_path, offline).fetch(PLAYERS, 1, 'ppr').by_position
    assert offline.calls


def test_corrupt_cache_is_a_miss(tmp_path):
    session = Session(hub=flex_only_hub())
    AnalystFetcher(2026, tmp_path, session).fetch(PLAYERS, 1, 'ppr')
    for path in tmp_path.glob('*.json'):
        path.write_text('{broken')
    fresh = Session(hub=flex_only_hub())
    assert AnalystFetcher(2026, tmp_path, fresh).fetch(PLAYERS, 1, 'ppr').by_position
    assert fresh.calls


def test_failure_memoized_and_diagnosed(capsys):
    session = Session(fail=requests.ConnectionError('offline'))
    f = AnalystFetcher(2026, session=session)
    for scoring in ['ppr', 'half']:
        result = f.fetch(PLAYERS, 1, scoring)
        assert not result.by_position
        assert 'author index unreachable' in result.status(1, 'League')[1]
    assert len(session.calls) == 1
    assert 'warning:' in capsys.readouterr().err


def test_missing_link_reason():
    result = AnalystFetcher(2026, session=Session(index='')).fetch(PLAYERS, 1, 'ppr')
    assert 'author index listed no week-1 ranking article' in result.status(1, 'League')[1]


def test_bad_byline_is_distinct():
    result = AnalystFetcher(2026, session=Session(hub=raw('hub.html.gz').replace('Justin Boone', 'Other Writer'))).fetch(PLAYERS, 1, 'ppr')
    assert VERIFY_REASON in result.status(1, 'League')[1]


def test_no_roster_matches_is_not_a_parse_failure():
    result = AnalystFetcher(2026, session=Session()).fetch([Player('x', 'Nobody Here', 'BUF', 'QB')], 1, 'ppr')
    assert result.lists
    assert result.unavailable['QB'] == 'parsed rankings named nobody on this roster'


def test_cache_write_error_never_discards_valid_rankings(tmp_path, monkeypatch, capsys):
    import ff_startsit.sources.analysts as module
    def broken(*args):
        raise OSError('read-only disk')
    monkeypatch.setattr(module, 'atomic_write_text', broken)
    result = AnalystFetcher(2026, tmp_path, Session()).fetch([PLAYERS[-1]], 1, 'ppr')
    assert result.by_position['QB'] == {'allen': 5}
    assert 'disk cache could not be written' in capsys.readouterr().err


def test_article_redirect_is_not_followed():
    class Redirect(Session):
        def get(self, url, **kwargs):
            assert kwargs['allow_redirects'] is False
            return SimpleNamespace(status_code=302, text='redirected')
    result = AnalystFetcher(2026, session=Redirect()).fetch(PLAYERS, 1, 'ppr')
    assert not result.by_position
    assert 'author index unreachable' in result.status(1, 'League')[1]


def test_widget_scoring_unresolved_reason():
    body = raw('hub.html.gz').replace('RB%3AWR%3ATE%3AFLX', '').replace('RB:WR:TE:FLX', '')
    body = body.replace('QB%3ADST%3AK', '').replace('QB:DST:K', '')
    result = AnalystFetcher(2026, session=Session(hub=body)).fetch(PLAYERS, 1, 'ppr')
    assert SCORING_REASON in result.status(1, 'League')[1]


def test_positional_article_does_not_accept_other_position_rows():
    data = decoded('ppr_wr')
    data['players'][0]['player_position_id'] = 'RB'
    with pytest.raises(ValueError, match='rankings table could not be parsed'):
        parse_rankings(wrapped(data), node('fullppr_wr'), 'WR', 'ppr', 1, 2026)


def test_new_week_refreshes_discovery_with_same_fetcher():
    session = Session()
    fetcher = AnalystFetcher(2026, session=session)
    fetcher.fetch([PLAYERS[-1]], 1, 'ppr')
    fetcher.fetch([PLAYERS[-1]], 2, 'ppr')
    assert len([url for url in session.calls if '/author/' in url]) == 2


def test_real_positional_wr_lists_preferred_over_flex():
    session = Session()
    p = Player('chase', "Ja'Marr Chase", 'CIN', 'WR')
    result = AnalystFetcher(2026, session=session).fetch([p], 1, 'ppr')
    assert result.by_position['WR'] == {'chase': 1}
    assert result.provenance['WR'] == ('WR', 'ppr')
    assert not any('position=FLX' in url or 'position=ALL' in url for url in session.calls)


def test_scoring_unpublished_for_one_league_stays_withheld():
    # Remove the actual PPR widget from a real hub, retaining its HALF widget.
    body = raw('hub.html.gz').replace('ppr_positions=RB%3AWR%3ATE%3AFLX', 'ppr_positions=')
    body = body.replace('\\"ppr_positions\\":\\"RB:WR:TE:FLX\\"', '\\"ppr_positions\\":\\"\\"')
    f = AnalystFetcher(2026, session=Session(hub=body))
    result = f.fetch([Player('chase', "Ja'Marr Chase", 'CIN', 'WR')], 1, 'ppr')
    assert not result.by_position
    assert 'no full PPR list published' in result.status(1, 'League')[1] or SCORING_REASON in result.status(1, 'League')[1]


def test_all_positions_and_mixed_scoring_request_budget():
    session = Session()
    f = AnalystFetcher(2026, session=session)
    players = PLAYERS + [Player('chase', "Ja'Marr Chase", 'CIN', 'WR'),
        Player('mcbride', 'Trey McBride', 'ARI', 'TE'),
        Player('aubrey', 'Brandon Aubrey', 'DAL', 'K'),
        Player('chiefs', 'Chiefs D/ST', 'KC', 'DEF')]
    for scoring in ['ppr', 'half']:
        ranks = f.fetch(players, 1, scoring)
        assert set(ranks.by_position) == {'QB', 'RB', 'WR', 'TE', 'K', 'DEF'}
        assert all(ranks.by_position.values())
        assert not ranks.unavailable
        assert ranks.provenance['DEF'] == ('DST', '')
        assert ranks.provenance['RB'] == ('RB', scoring)
    assert len(session.calls) == 11
    for pos in ['QB', 'DST', 'K']:
        assert len([u for u in session.calls if f'position={pos}' in u]) == 1


def test_bad_parse_is_cached_and_distinguished_from_missing_article(tmp_path):
    class Truncated(Session):
        def get(self, url, **kwargs):
            response = super().get(url, **kwargs)
            if 'position=QB' in url:
                data = decoded('half_qb')
                data['players'] = data['players'][:2]; data['count'] = 2
                response.text = wrapped(data)
            return response
    session = Truncated()
    f = AnalystFetcher(2026, tmp_path, session)
    for scoring in ['ppr', 'half']:
        ranks = f.fetch([PLAYERS[-1]], 1, scoring)
        assert not ranks.by_position
        assert 'rankings table could not be parsed' in ranks.status(1, 'League')[1]
    assert len([u for u in session.calls if 'position=QB' in u]) == 1
    second = Truncated()
    AnalystFetcher(2026, tmp_path, second).fetch([PLAYERS[-1]], 1, 'ppr')
    assert not second.calls


def test_overall_article_is_lazy_tabbed_fallback_not_invented_all_list():
    class Fallback(Session):
        def get(self, url, **kwargs):
            response = super().get(url, **kwargs)
            if '/fantasy/article/' in url:
                # The real Overall article is readable; negative byline mutations
                # make earlier candidates unavailable without inventing markup.
                response.text = (raw('overall_article.html.gz') if 'top-players' in url
                                 else self.hub.replace('Justin Boone', 'Other Writer'))
            return response
    session = Fallback()
    result = AnalystFetcher(2026, session=session).fetch(PLAYERS, 1, 'ppr')
    assert result.by_position['RB'] == {'taylor': 6, 'achane': 5}
    assert result.by_position['QB'] == {'allen': 5}
    assert result.provenance['RB'] == ('RB', 'ppr')
    assert all('top-players' in r.url for r in result.lists)
    assert not any('position=ALL' in url for url in session.calls)
