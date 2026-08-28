"""The on-disk cache primitives: no write races, no crash on a corrupt file."""

import json
import threading

import pytest

from ff_startsit import cache


# --- atomic_write_text ---------------------------------------------------

def test_write_creates_the_parent_directory(tmp_path):
    path = tmp_path / "nested" / "deeper" / "roster.json"
    cache.write_json(path, {"a": 1})
    assert json.loads(path.read_text()) == {"a": 1}


def test_write_leaves_no_temp_file_behind(tmp_path):
    path = tmp_path / "roster.json"
    cache.atomic_write_text(path, "hello")
    assert [p.name for p in tmp_path.iterdir()] == ["roster.json"]


def test_a_failed_write_removes_its_own_temp_file(tmp_path, monkeypatch):
    """The cleanup is what keeps a crashed run from littering the cache dir."""
    path = tmp_path / "roster.json"
    monkeypatch.setattr(cache.os, "replace",
                        lambda *a: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        cache.atomic_write_text(path, "hello")
    assert list(tmp_path.iterdir()) == []


def test_an_overwrite_is_all_or_nothing(tmp_path):
    path = tmp_path / "roster.json"
    cache.write_json(path, {"old": True})
    cache.write_json(path, {"new": True})
    assert json.loads(path.read_text()) == {"new": True}


def test_concurrent_writers_all_succeed(tmp_path):
    """F-01: a shared ``<name>.tmp`` let one writer delete another's temp file
    out from under it, and the loser raised FileNotFoundError from an otherwise
    valid command. A barrier forces the overlap a stress loop only finds
    sometimes."""
    path = tmp_path / "roster.json"
    writers = 12
    barrier = threading.Barrier(writers)
    errors: list[BaseException] = []

    def write(n: int) -> None:
        try:
            barrier.wait(timeout=10)
            cache.write_json(path, {"writer": n})
        except BaseException as exc:      # noqa: BLE001 - the assertion is "none"
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(n,)) for n in range(writers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert errors == []
    # Some writer won, and the file is whole rather than interleaved.
    assert json.loads(path.read_text())["writer"] in range(writers)
    assert [p.name for p in tmp_path.iterdir()] == ["roster.json"]


# --- read_json_or_none ---------------------------------------------------

def test_read_returns_none_for_a_missing_file(tmp_path):
    assert cache.read_json_or_none(tmp_path / "nope.json") is None


def test_read_returns_none_for_a_truncated_file(tmp_path):
    """A write interrupted mid-flight is a miss, not a permanent outage."""
    path = tmp_path / "roster.json"
    path.write_text('{"players": [{"name": "Half a wr')
    assert cache.read_json_or_none(path) is None


def test_read_returns_none_for_a_directory(tmp_path):
    assert cache.read_json_or_none(tmp_path) is None


def test_read_round_trips_what_write_json_wrote(tmp_path):
    path = tmp_path / "roster.json"
    cache.write_json(path, {"players": ["a", "b"]}, indent=2)
    assert cache.read_json_or_none(path) == {"players": ["a", "b"]}
