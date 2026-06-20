import json

from ff_startsit.config import Settings
from ff_startsit.models import Player, SignalValue
from ff_startsit.pipeline import recommend
from ff_startsit.sources.base import Signal


class FakeECR(Signal):
    name = "ecr"
    higher_is_better = False

    def __init__(self, ranks):
        self.ranks = ranks

    def is_available(self):
        return True

    def fetch(self, week, players):
        return {p.key: SignalValue(self.ranks[p.key]) for p in players}


class FakeVegas(Signal):
    name = "vegas"
    higher_is_better = True

    def is_available(self):
        return False  # exercises the unavailable path

    def fetch(self, week, players):
        raise AssertionError("should not be called when unavailable")


def test_recommend_blends_and_logs(tmp_path):
    settings = Settings(weights={"ecr": 0.75, "vegas": 0.25}, data_dir=tmp_path)
    players = [
        Player(key="1", name="Alpha", team="KC", position="RB"),
        Player(key="2", name="Bravo", team="CHI", position="RB"),
    ]
    signals = [FakeECR({"1": 1.0, "2": 8.0}), FakeVegas()]

    rec = recommend(settings, players, week=3, signals=signals, command="rank")

    assert rec.scores[0].player.key == "1"     # better ECR rank wins
    # Vegas was unavailable -> only ECR contributed.
    assert "vegas" not in rec.scores[0].normalized

    # A row was appended to the results log (the #7 hook).
    log_path = settings.results_log_path
    assert log_path.exists()
    row = json.loads(log_path.read_text().strip().splitlines()[-1])
    assert row["command"] == "rank"
    assert row["pick"] == "Alpha"
    assert row["week"] == 3
