import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


def load_fixture(name: str):
    path = FIXTURES / name
    if name.endswith(".json"):
        return json.loads(path.read_text())
    return path.read_text()
