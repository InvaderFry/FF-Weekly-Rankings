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


def test_scrape_slug_for_flex_pool():
    """FantasyPros' cross-position list follows the same slug convention."""
    assert _scrape_slug("FLX", "ppr") == "ppr-flex"
    assert _scrape_slug("FLX", "half") == "half-point-ppr-flex"
    assert _scrape_slug("FLX", "std") == "flex"
    assert _scrape_slug("FLEX", "ppr") == "ppr-flex"   # spelled-out alias


def test_pooled_fetch_uses_one_cross_position_list():
    """Pooled rows carry each player's real position, so the join is unchanged.

    Only the rank value changes meaning: it is now comparable across positions,
    which is the whole point -- a per-position rank of 1 means "RB1" and "WR1"
    indistinguishably.
    """
    payload = json.loads((FIXTURES / "ecr_api_flex.json").read_text())
    session = _FakeSession(payload)
    sig = ECRSignal(api_key="testkey", scoring="ppr", season=2025,
                    session=session).pooled()

    players = [
        Player(key="1", name="Patrick Runner", team="KC", position="RB"),
        Player(key="2", name="Elite Wideout", team="CIN", position="WR"),
        Player(key="3", name="Star Tight End", team="SF", position="TE"),
    ]
    out = sig.fetch(3, players)
    # One request for the whole pool, not one per position.
    assert len(session.calls) == 1
    assert out["2"].raw == 1.0 and out["1"].raw == 2.0 and out["3"].raw == 3.0


def test_pooled_cache_does_not_shadow_per_position_cache():
    """The pseudo-position keeps the two paths' cache keys disjoint."""
    payload = json.loads((FIXTURES / "ecr_api_flex.json").read_text())
    session = _FakeSession(payload)
    base = ECRSignal(api_key="testkey", scoring="ppr", season=2025, session=session)
    pooled = base.pooled()

    players = [Player(key="1", name="Patrick Runner", team="KC", position="RB")]
    base.fetch(3, players)      # caches ("RB", 3)
    pooled.fetch(3, players)    # caches ("FLX", 3) on the shared cache
    assert set(base._rows_cache) == {("RB", 3), ("FLX", 3)}

    # Both are served from cache on a second call -- no extra HTTP.
    calls = len(session.calls)
    base.fetch(3, players)
    pooled.fetch(3, players)
    assert len(session.calls) == calls


def test_pooled_scrape_path_parses_flex_page():
    html = (FIXTURES / "ecr_scrape_flex.html").read_text()
    rows = parse_scrape_html(html)
    assert [r.position for r in rows][:3] == ["WR", "RB", "TE"]
    assert [r.value for r in rows][:3] == [1.0, 2.0, 3.0]
