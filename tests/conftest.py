import json
from pathlib import Path

import pytest

from ff_startsit import cli as _cli

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolate_settings(monkeypatch, tmp_path):
    """Keep ``load_settings`` off the developer's real config.

    ``load_settings`` calls ``load_dotenv()`` unconditionally, and python-dotenv
    walks up to the repo root to find a file — so a developer who followed
    docs/SETUP.md and created a .env would have it injected into every test that
    only clears *process* env vars. Same story for ``.cache/learned_weights.json``,
    which feeds the weight precedence chain via the default data dir.

    CI never has either file, so without this the suite passes there and fails
    locally. Tests that want their own values still win: they set env vars
    inside the test body, which runs after this fixture.
    """
    monkeypatch.setattr("ff_startsit.config.load_dotenv", lambda *a, **k: False)
    monkeypatch.setenv("FF_DATA_DIR", str(tmp_path / "cache"))


#: The unstubbed helper, for the tests that exercise it with a fake provider.
REAL_AVAILABILITY_VIEW = _cli._availability_view


@pytest.fixture(autouse=True)
def offline_availability(monkeypatch):
    """Keep the start/sit availability check off the network.

    ``cli._score_league`` builds the league's real roster provider to read
    ESPN's weekly projections and, when a lineup has a hole, the free-agent
    wire, and reads the NFL schedule for byes. Every CLI test that scores a
    week would otherwise send those requests. Tests of the check itself call ``report.check_availability`` and
    ``availability`` directly with fakes, or re-patch this.
    """
    monkeypatch.setattr("ff_startsit.cli._availability_view",
                        lambda *a, **k: (None, None, False))


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


def load_fixture(name: str):
    path = FIXTURES / name
    if name.endswith(".json"):
        return json.loads(path.read_text())
    return path.read_text()
