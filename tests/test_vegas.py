import json
from pathlib import Path

from ff_startsit.models import Game, Player
from ff_startsit.sources.vegas import (
    VegasSignal,
    implied_totals_by_team,
    parse_odds_response,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_implied_total_math_sums_to_total():
    g = Game(home_team="KC", away_team="BUF", total=48.0, home_spread=-3.0)
    kc = g.implied_total("KC")
    buf = g.implied_total("BUF")
    assert kc == 25.5
    assert buf == 22.5
    assert kc + buf == 48.0
    assert g.implied_total("SF") is None  # team not in this game


def test_parse_odds_response_averages_books():
    events = json.loads((FIXTURES / "odds_api.json").read_text())
    games = parse_odds_response(events)
    assert len(games) == 2
    kc_game = next(g for g in games if g.home_team == "KC")
    # totals averaged: (47.5 + 48.5) / 2 = 48.0
    assert kc_game.total == 48.0
    assert kc_game.home_spread == -3.0


def test_implied_totals_by_team():
    events = json.loads((FIXTURES / "odds_api.json").read_text())
    totals = implied_totals_by_team(parse_odds_response(events))
    assert totals["KC"] == 25.5
    assert totals["BUF"] == 22.5
    assert totals["CHI"] == 16.0
    assert totals["GB"] == 22.0


def test_assign_marks_bye_and_missing():
    players = [
        Player(key="1", name="A", team="KC", position="RB"),
        Player(key="2", name="B", team=None, position="RB"),   # bye / no team
        Player(key="3", name="C", team="SF", position="RB"),   # team has no line
    ]
    totals = {"KC": 25.5}
    out = VegasSignal.assign(players, totals)
    assert out["1"].available and out["1"].raw == 25.5
    assert not out["2"].available and "bye" in out["2"].note.lower()
    assert not out["3"].available


def test_signal_unavailable_without_key():
    sig = VegasSignal(api_key="")
    assert sig.is_available() is False
    players = [Player(key="1", name="A", team="KC", position="RB")]
    out = sig.fetch(3, players)
    assert not out["1"].available
