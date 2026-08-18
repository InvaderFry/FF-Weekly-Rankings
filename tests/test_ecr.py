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


def test_scrape_warns_when_the_requested_week_cannot_be_served(capsys, monkeypatch):
    """The public page has no week selector, so a --week N scrape isn't week N.

    Silently filing current-week rankings under a historical week is what makes
    backtests dishonest -- they'd score today's consensus against past outcomes.
    """
    import ff_startsit.season as season_mod
    monkeypatch.setattr(season_mod, "date_week", lambda *a, **k: 9)

    html = (FIXTURES / "ecr_scrape_rb.html").read_text()

    class _HtmlResp:
        status_code = 200
        text = html
        def raise_for_status(self): pass

    class _HtmlSession:
        def get(self, url, **kw): return _HtmlResp()

    sig = ECRSignal(api_key="", scoring="ppr", season=2025, session=_HtmlSession())
    sig.fetch(3, [Player(key="1", name="Patrick Runner", team="KC", position="RB")])
    err = capsys.readouterr().err
    assert "not week-3 rankings" in err

    # Current week scrapes are fine and stay quiet.
    sig2 = ECRSignal(api_key="", scoring="ppr", season=2025, session=_HtmlSession())
    sig2.fetch(9, [Player(key="1", name="Patrick Runner", team="KC", position="RB")])
    assert "not week-9 rankings" not in capsys.readouterr().err


def test_week_mismatch_warns_once_not_once_per_position(capsys, monkeypatch):
    import ff_startsit.season as season_mod
    monkeypatch.setattr(season_mod, "date_week", lambda *a, **k: 9)

    html = (FIXTURES / "ecr_scrape_rb.html").read_text()

    class _HtmlResp:
        status_code = 200
        text = html
        def raise_for_status(self): pass

    class _HtmlSession:
        def get(self, url, **kw): return _HtmlResp()

    sig = ECRSignal(api_key="", scoring="ppr", season=2025, session=_HtmlSession())
    sig.fetch(3, [
        Player(key="1", name="Patrick Runner", team="KC", position="RB"),
        Player(key="2", name="Someone", team="KC", position="WR"),
        Player(key="3", name="Another", team="KC", position="TE"),
    ])
    assert capsys.readouterr().err.count("not week-3 rankings") == 1


def test_fetch_joins_def_roster_players_to_dst_ranking_rows():
    """FantasyPros ranks defenses under "DST"; every roster source says "DEF".

    Both halves of the old match key differed — name *and* position — so no
    defense ever received an ECR rank on any roster source, and the DEF slot was
    scored on Vegas/injury/weather alone with ECR's 0.60 weight silently gone.
    """
    payload = {"players": [
        {"player_name": "Kansas City Chiefs", "player_team_id": "KC",
         "player_position_id": "DST", "rank_ecr": 2},
        {"player_name": "San Francisco 49ers", "player_team_id": "SF",
         "player_position_id": "DST", "rank_ecr": 5},
    ]}
    sig = ECRSignal(api_key="testkey", scoring="ppr", season=2025,
                    session=_FakeSession(payload))

    players = [
        Player(key="espn-16", name="Chiefs D/ST", team="KC", position="DEF"),  # ESPN
        Player(key="SF", name="San Francisco", team="SF", position="DEF"),     # Sleeper
    ]
    out = sig.fetch(3, players)
    assert out["espn-16"].available and out["espn-16"].raw == 2.0
    assert out["SF"].available and out["SF"].raw == 5.0


class _FakeScrapeResp:
    def __init__(self, html):
        self.text = html

    def raise_for_status(self):
        pass


class _FakeScrapeSession:
    """Serves the scrape fixture for any GET — the keyless (default) path."""

    def __init__(self, html):
        self._html = html

    def get(self, url, **kwargs):
        return _FakeScrapeResp(self._html)


def _scrape_signal():
    html = (FIXTURES / "ecr_scrape_rb.html").read_text()
    return ECRSignal(api_key="", scoring="ppr", season=2025,
                     session=_FakeScrapeSession(html))


def test_scrape_flags_a_week_it_could_not_have_served(monkeypatch, capsys):
    """The public page has no week selector, so a --week 5 scrape returns
    whatever week is current. The values are still shown, but the run is marked
    so it never reaches the append-only results log."""
    import ff_startsit.season as season_mod

    monkeypatch.setattr(season_mod, "date_week", lambda *a, **k: 3)
    sig = _scrape_signal()
    assert sig.served_wrong_week is False

    sig.fetch(5, [Player(key="1", name="Patrick Runner", team="KC", position="RB")])

    assert sig.served_wrong_week is True
    assert sig.last_source == "scrape"
    assert "not week-5 rankings" in capsys.readouterr().err


def test_scrape_of_the_current_week_is_not_flagged(monkeypatch):
    import ff_startsit.season as season_mod

    monkeypatch.setattr(season_mod, "date_week", lambda *a, **k: 3)
    sig = _scrape_signal()
    sig.fetch(3, [Player(key="1", name="Patrick Runner", team="KC", position="RB")])
    assert sig.served_wrong_week is False


def test_wrong_week_warns_once_per_week_but_stays_flagged(monkeypatch, capsys):
    """A whole-roster pass fetches one list per position, all for the same week.

    The warning is deduplicated per week so the run isn't spammed, but the flag
    has to hold across every position — it is what keeps the run out of the log.
    """
    import ff_startsit.season as season_mod

    monkeypatch.setattr(season_mod, "date_week", lambda *a, **k: 3)
    sig = _scrape_signal()
    sig.fetch(5, [Player(key="1", name="Patrick Runner", team="KC", position="RB"),
                  Player(key="2", name="Some Receiver", team="SF", position="WR"),
                  Player(key="3", name="Some End", team="BUF", position="TE")])

    assert sig.served_wrong_week is True
    assert capsys.readouterr().err.count("not week-5 rankings") == 1
