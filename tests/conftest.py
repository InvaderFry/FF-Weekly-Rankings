import json
from pathlib import Path

import pytest

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


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


def load_fixture(name: str):
    path = FIXTURES / name
    if name.endswith(".json"):
        return json.loads(path.read_text())
    return path.read_text()
