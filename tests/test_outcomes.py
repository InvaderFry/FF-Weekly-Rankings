import json

import pytest

from ff_startsit.calibrate.log_reader import season_from_ts
from ff_startsit.calibrate.outcomes import (
    SleeperStatsClient,
    build_outcome_lookup,
    parse_stats,
    points_field,
)


@pytest.fixture
def load_fixture(fixtures_dir):
    def _load(name):
        return json.loads((fixtures_dir / name).read_text())
    return _load


def test_points_field_maps_scoring():
    assert points_field("ppr") == "pts_ppr"
    assert points_field("half") == "pts_half_ppr"
    assert points_field("std") == "pts_std"
    assert points_field("nonsense") == "pts_ppr"  # safe default


def test_parse_stats_picks_scoring_field_and_skips_nonscorers(load_fixture):
    blob = load_fixture("sleeper_stats.json")
    ppr = parse_stats(blob, "ppr")
    assert ppr["100"] == 22.5
    assert ppr["101"] == 8.1
    # "777" has no points field for any mode -> omitted entirely.
    assert "777" not in ppr

    std = parse_stats(blob, "std")
    assert std["100"] == 16.5  # different value for standard scoring


def test_outcome_lookup_by_sleeper_key(load_fixture):
    blob = load_fixture("sleeper_stats.json")
    meta = load_fixture("sleeper_players.json")
    index = build_outcome_lookup(parse_stats(blob, "ppr"), meta)
    # Sleeper-sourced log: key is the Sleeper id -> direct hit.
    assert index.get("100", "ignored", "RB") == 22.5


def test_outcome_lookup_name_position_fallback_for_espn_manual(load_fixture):
    blob = load_fixture("sleeper_stats.json")
    meta = load_fixture("sleeper_players.json")
    index = build_outcome_lookup(parse_stats(blob, "ppr"), meta)
    # ESPN/manual log: key is NOT a Sleeper id, so resolve by (name, position).
    assert index.get("espn-55", "Patrick Runner", "RB") == 22.5
    # Defense resolves by team name + DEF position.
    assert index.get("manual-KC", "Kansas City", "DEF") == 11.0
    # Unknown player -> None.
    assert index.get("espn-99", "Nobody Here", "WR") is None


def test_season_inferred_from_timestamp():
    assert season_from_ts("2024-10-01T12:00:00+00:00") == "2024"
    # January games belong to the previous NFL season.
    assert season_from_ts("2025-01-05T18:00:00Z") == "2024"
    assert season_from_ts("not-a-date") is None


# --- disk cache: a corrupt file must never brick the command ---------------

class _StubSession:
    """A session that counts requests and returns one canned stats blob."""

    def __init__(self, blob):
        self.blob = blob
        self.calls = 0

    def get(self, url, timeout=None):
        self.calls += 1
        return _StubResponse(self.blob)


class _StubResponse:
    def __init__(self, blob):
        self._blob = blob

    def raise_for_status(self):
        return None

    def json(self):
        return self._blob


def test_weekly_stats_caches_on_disk(tmp_path):
    session = _StubSession({"100": {"pts_ppr": 22.5}})
    client = SleeperStatsClient(data_dir=tmp_path, session=session)

    assert client.weekly_stats("2024", 5) == {"100": {"pts_ppr": 22.5}}
    assert client.weekly_stats("2024", 5) == {"100": {"pts_ppr": 22.5}}
    assert session.calls == 1          # second read came off disk


def test_weekly_stats_refetches_past_a_truncated_cache(tmp_path):
    """A write interrupted mid-flight used to raise JSONDecodeError on every
    later run until the user deleted the file by hand. It is a miss now."""
    session = _StubSession({"100": {"pts_ppr": 22.5}})
    client = SleeperStatsClient(data_dir=tmp_path, session=session)
    path = client._cache_path("2024", 5)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"100": {"pts_pp')          # truncated write

    assert client.weekly_stats("2024", 5) == {"100": {"pts_ppr": 22.5}}
    assert session.calls == 1
    # The refetch also repaired the cache, so the next run reads clean.
    assert client.weekly_stats("2024", 5) == {"100": {"pts_ppr": 22.5}}
    assert session.calls == 1
