"""Offline checks on observed transport evidence, not a production fetcher.

These pin the facts the revised analyst plan depends on. Never execute JSONP.
"""
import gzip
import hashlib
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / 'fixtures' / 'boone_transport'


def payload(name):
    text = gzip.decompress((FIXTURES / (name + '_ranks.jsonp.gz')).read_bytes()).decode()
    prefix = 'FPW.rankingsCB('
    assert text.startswith(prefix)
    data, end = json.JSONDecoder().raw_decode(text[len(prefix):])
    assert text[len(prefix) + end:].strip() in {')', ');'}
    return data


def test_evidence_integrity():
    for name, metadata in json.loads((FIXTURES / 'manifest.json').read_text()).items():
        raw = (FIXTURES / name).read_bytes()
        if name.endswith('.gz'):
            raw = gzip.decompress(raw)
        assert len(raw) == metadata['bytes']
        assert hashlib.sha256(raw).hexdigest() == metadata['sha256']


@pytest.mark.parametrize('name,position,scoring,count', [
    ('half_flex', 'FLX', 'HALF', 149),
    ('ppr_flex', 'FLX', 'PPR', 155),
    ('half_qb', 'QB', 'HALF', 32),
    ('half_wr', 'WR', 'HALF', 146),
    ('ppr_wr', 'WR', 'PPR', 75),
])
def test_response_asserts_identity_and_individual_ranks(name, position, scoring, count):
    data = payload(name)
    assert (data['sport'], data['year'], data['week']) == ('NFL', '2026', '1')
    assert data['position_id'] == position
    assert data['scoring'] == scoring
    assert data['expert_names'] == {'317': 'Justin Boone'}
    assert data['total_experts'] == 1
    assert data['filters'] == '317'
    assert len(data['players']) == data['count'] == count
    assert [int(row['experts']['317']) for row in data['players']] == list(range(1, count + 1))


def test_scoring_difference_is_real_in_flex():
    half, ppr = payload('half_flex'), payload('ppr_flex')
    assert [r['player_name'] for r in half['players'][:3]] == [
        'Jahmyr Gibbs', 'Bijan Robinson', "Ja'Marr Chase"]
    assert [r['player_name'] for r in ppr['players'][:3]] == [
        'Jahmyr Gibbs', "Ja'Marr Chase", 'Bijan Robinson']
    # A meaningful positional difference, even after cross-position ranks are removed.
    def rb_order(data):
        return [r['player_name'] for r in data['players'] if r['player_position_id'] == 'RB']
    assert rb_order(half)[4:6] == ['Jonathan Taylor', "De'Von Achane"]
    assert rb_order(ppr)[4:6] == ["De'Von Achane", 'Jonathan Taylor']


def test_wr_pair_does_not_prove_different_order():
    half = {r['player_id']: r['experts']['317'] for r in payload('half_wr')['players']}
    ppr = payload('ppr_wr')['players']
    assert all(half[r['player_id']] == r['experts']['317'] for r in ppr)


def test_invalid_filter_does_not_return_consensus():
    data = payload('invalid_filter')
    assert data['count'] == data['total_experts'] == 0
    assert not data['players']
    assert not data['expert_names']


def test_unpublished_week_has_no_rows():
    data = payload('week2')
    assert data['week'] == '2'
    assert data['count'] == 0
    assert not data['players']


def test_publisher_id_alone_is_not_an_attribution_gate():
    data = payload('invalid_publisher')
    assert data['expert_names'] == {'317': 'Justin Boone'}
    assert data['players']


def test_exact_fullppr_widget_initially_requests_half():
    node = json.loads((FIXTURES / 'fullppr_flex_node.json').read_text())
    browser = json.loads((FIXTURES / 'fullppr_flex_browser.json').read_text())
    assert node['ppr_positions'] == 'FLX'
    assert node['half_positions'] == ''
    assert node['scoring'] == 'HALF'
    assert browser['state'] == {'scoring': 'HALF', 'position': 'FLX', 'filters': '317'}
    assert browser['rows'] == 149
    assert len(browser['requests']) == 1
    assert 'scoring=HALF' in browser['requests'][0]
