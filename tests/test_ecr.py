import json
from pathlib import Path

from ff_startsit.models import Player
from ff_startsit.sources.ecr import (
    ECRSignal,
    parse_api_response,
    parse_scrape_html,
    _scrape_slug,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_api_response():
    payload = json.loads((FIXTURES / "ecr_api_rb.json").read_text())
    rows = parse_api_response(payload)
    assert len(rows) == 3
    top = rows[0]
    assert top.name == "Patrick Runner"
    assert top.team == "KC"
    assert top.position == "RB"
    assert top.value == 1.0


def test_parse_scrape_html_extracts_embedded_json():
    html = (FIXTURES / "ecr_scrape_rb.html").read_text()
    rows = parse_scrape_html(html)
    assert [r.name for r in rows] == ["Patrick Runner", "Chicago Back", "Buffalo Rusher"]
    assert rows[2].value == 15.0


def test_parse_scrape_html_missing_blob_returns_empty():
    assert parse_scrape_html("<html>no data here</html>") == []


def test_scrape_slug_by_scoring():
    assert _scrape_slug("RB", "ppr") == "ppr-rb"
    assert _scrape_slug("WR", "half") == "half-point-ppr-wr"
    assert _scrape_slug("RB", "std") == "rb"
    assert _scrape_slug("QB", "ppr") == "qb"      # scoring-agnostic
    assert _scrape_slug("DEF", "ppr") == "dst"


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeSession:
    """Returns the RB API fixture for any GET (enough for matching tests)."""

    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _FakeResp(self._payload)


def test_fetch_matches_rows_to_roster_and_flags_unmatched():
    payload = json.loads((FIXTURES / "ecr_api_rb.json").read_text())
    session = _FakeSession(payload)
    sig = ECRSignal(api_key="testkey", scoring="ppr", season=2025, session=session)

    players = [
        Player(key="100", name="Patrick Runner", team="KC", position="RB"),
        Player(key="102", name="Buffalo Rusher", team="BUF", position="RB"),
        Player(key="103", name="Nobody Ranked", team="NE", position="RB"),
    ]
    out = sig.fetch(3, players)
    assert out["100"].available and out["100"].raw == 1.0
    assert out["102"].available and out["102"].raw == 15.0
    assert not out["103"].available  # not present in ECR -> flagged, not dropped
    assert sig.last_source == "api"
