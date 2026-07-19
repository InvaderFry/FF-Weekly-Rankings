import json
from pathlib import Path

import requests

from ff_startsit.models import Player
from ff_startsit.sources.journalists import (
    Expert,
    JournalistFetcher,
    parse_experts,
)

FIXTURES = Path(__file__).parent / "fixtures"

BOONE = Expert(id="101", name="Justin Boone")
EISENBERG = Expert(id="102", name="Jamey Eisenberg")

PLAYERS = [
    Player(key="100", name="Patrick Runner", team="KC", position="RB"),
    Player(key="101", name="Chicago Back", team="CHI", position="RB"),
    Player(key="102", name="Buffalo Rusher", team="BUF", position="RB"),
    Player(key="103", name="Nobody Ranked", team="NE", position="RB"),
]


def _payload(name):
    return json.loads((FIXTURES / name).read_text())


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeSession:
    """Serves a per-expert fixture keyed by the ``filters`` request param."""

    def __init__(self, payload_by_filter):
        self._by_filter = payload_by_filter
        self.calls = []

    def get(self, url, **kwargs):
        params = kwargs.get("params") or {}
        self.calls.append((url, params))
        filters = params.get("filters")
        payload = self._by_filter.get(filters)
        if payload is None:
            raise requests.ConnectionError(f"no fixture for filters={filters!r}")
        return _FakeResp(payload)


def _fetcher(session, experts=(BOONE, EISENBERG)):
    return JournalistFetcher(experts, api_key="testkey", scoring="ppr",
                             season=2025, session=session)


def test_parse_experts():
    experts = parse_experts("101:Justin Boone, 102:Jamey Eisenberg,103")
    assert experts == [Expert("101", "Justin Boone"),
                       Expert("102", "Jamey Eisenberg"),
                       Expert("103", "Expert 103")]


def test_parse_experts_disabled_and_malformed(capsys):
    assert parse_experts("") == []
    assert parse_experts("0") == []
    assert parse_experts("off") == []
    assert parse_experts("notanid:Someone,101:Real") == [Expert("101", "Real")]
    assert "malformed" in capsys.readouterr().err


def test_build_view_sends_one_filter_per_expert():
    session = _FakeSession({"101": _payload("ecr_api_rb_expert1.json"),
                            "102": _payload("ecr_api_rb_expert2.json")})
    view = _fetcher(session).build_view(PLAYERS, week=3)
    assert view is not None
    assert sorted(p.get("filters") for _, p in session.calls) == ["101", "102"]


def test_build_view_averages_and_sorts():
    session = _FakeSession({"101": _payload("ecr_api_rb_expert1.json"),
                            "102": _payload("ecr_api_rb_expert2.json")})
    view = _fetcher(session).build_view(PLAYERS, week=3)

    rows = view.by_position["RB"]
    # Patrick Runner: (2+4)/2 = 3; Chicago Back: (6+10)/2 = 8;
    # Buffalo Rusher only in expert1 -> average over the one known rank = 12.
    assert [(r.player.name, r.avg_rank) for r in rows] == [
        ("Patrick Runner", 3.0), ("Chicago Back", 8.0), ("Buffalo Rusher", 12.0)]
    assert rows[0].ranks == {"101": 2.0, "102": 4.0}
    assert rows[2].ranks == {"101": 12.0, "102": None}
    # Unranked-everywhere players are omitted from the view entirely.
    assert all(r.player.key != "103" for r in rows)


def test_one_failing_expert_degrades_gracefully(capsys):
    session = _FakeSession({"101": _payload("ecr_api_rb_expert1.json")})  # 102 errors
    view = _fetcher(session).build_view(PLAYERS, week=3)
    assert [e.id for e in view.experts] == ["101"]  # failed expert dropped
    assert view.by_position["RB"][0].avg_rank == 2.0  # expert1's rank alone
    assert "Jamey Eisenberg" in capsys.readouterr().err


def test_all_experts_failing_returns_none():
    view = _fetcher(_FakeSession({})).build_view(PLAYERS, week=3)
    assert view is None


def test_no_experts_configured_returns_none():
    view = _fetcher(_FakeSession({}), experts=()).build_view(PLAYERS, week=3)
    assert view is None


def test_identical_ranks_warns_filter_may_be_ignored(capsys):
    payload = _payload("ecr_api_rb_expert1.json")
    session = _FakeSession({"101": payload, "102": payload})
    view = _fetcher(session).build_view(PLAYERS, week=3)
    assert view is not None
    assert "filter may be ignored" in capsys.readouterr().err


def test_fetch_is_memoized_per_expert_and_position():
    session = _FakeSession({"101": _payload("ecr_api_rb_expert1.json"),
                            "102": _payload("ecr_api_rb_expert2.json")})
    fetcher = _fetcher(session)
    fetcher.build_view(PLAYERS, week=3)
    fetcher.build_view(PLAYERS, week=3)
    assert len(session.calls) == 2  # one call per expert, second pass cached


def test_ecr_signal_still_sends_no_filters():
    from ff_startsit.sources.ecr import ECRSignal

    session = _FakeSession({None: _payload("ecr_api_rb.json")})
    sig = ECRSignal(api_key="testkey", scoring="ppr", season=2025, session=session)
    sig.fetch(3, PLAYERS[:1])
    assert all("filters" not in params for _, params in session.calls)
