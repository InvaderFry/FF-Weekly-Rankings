import json
from datetime import datetime, timezone
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


def test_implied_totals_keeps_the_sooner_game():
    """A team appearing twice (next week's line already posted) keeps the first.

    The odds endpoint orders events by kickoff, so first-wins is soonest-wins.
    """
    games = [
        Game(home_team="KC", away_team="BUF", total=48.0, home_spread=-3.0),
        # Same team, a later week, very different line.
        Game(home_team="KC", away_team="SF", total=60.0, home_spread=-10.0),
    ]
    totals = implied_totals_by_team(games)
    assert totals["KC"] == 25.5   # from the first game, not 35.0 from the second
    assert totals["BUF"] == 22.5
    assert totals["SF"] == 25.0


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


# --- week filtering ------------------------------------------------------

def _multiweek():
    return json.loads((FIXTURES / "odds_api_multiweek.json").read_text())


def _week5_index():
    """The two games actually scheduled this week, as the schedule reports them."""
    from ff_startsit.models import GameContext
    from ff_startsit.sources.schedule import by_team
    return by_team([
        GameContext(home_team="KC", away_team="BUF",
                    kickoff=datetime(2025, 10, 5, 17, 0, tzinfo=timezone.utc)),
        GameContext(home_team="CHI", away_team="GB",
                    kickoff=datetime(2025, 10, 5, 20, 5, tzinfo=timezone.utc)),
    ])


def test_parse_odds_reads_commence_time():
    games = parse_odds_response(_multiweek())
    kc = next(g for g in games if g.away_team == "BUF")
    assert kc.kickoff == datetime(2025, 10, 5, 17, 0, tzinfo=timezone.utc)


def test_games_for_week_drops_a_later_weeks_line():
    """KC appears twice; only this week's matchup survives."""
    from ff_startsit.sources.vegas import games_for_week

    games = parse_odds_response(_multiweek())
    assert len(games) == 3                        # both KC games parsed
    kept = games_for_week(games, _week5_index())
    assert len(kept) == 2
    assert {(g.home_team, g.away_team) for g in kept} == {("KC", "BUF"), ("CHI", "GB")}

    totals = implied_totals_by_team(kept)
    assert totals["KC"] == 25.5                   # this week's line, not 35.0
    assert "DEN" not in totals                    # next week's opponent absent


def test_games_for_week_without_a_schedule_keeps_everything():
    """No schedule -> no filtering; the ordering guard is all that is left.

    There is deliberately no kickoff-window middle tier: the only source of a
    window is the same schedule lookup, so it would be empty in exactly the
    case it was meant to cover.
    """
    from ff_startsit.sources.vegas import games_for_week

    games = parse_odds_response(_multiweek())
    assert len(games_for_week(games, {})) == 3
    # First-occurrence-wins still keeps the sooner KC game.
    assert implied_totals_by_team(games)["KC"] == 25.5


def test_signal_filters_to_the_requested_week():
    class _Resp:
        def __init__(self, p): self._p = p
        def raise_for_status(self): pass
        def json(self): return self._p

    class _Sess:
        def __init__(self, p): self._p = p
        def get(self, *a, **kw): return _Resp(self._p)

    class _Sched:
        def for_week(self, week): return _week5_index()
        def kickoff_window(self, week): return None

    sig = VegasSignal(api_key="k", session=_Sess(_multiweek()), schedule=_Sched())
    players = [
        Player(key="1", name="Chief", team="KC", position="RB"),
        Player(key="2", name="Bronco", team="DEN", position="RB"),   # next week only
    ]
    out = sig.fetch(5, players)
    assert out["1"].available and out["1"].raw == 25.5
    assert not out["2"].available          # not playing this week
