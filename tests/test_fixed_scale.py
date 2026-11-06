"""Injury and weather blend on their own absolute 0-100 scale, not min-max.

HailMeowy, Week 9: Keon Coleman (FantasyPros WR53, the lowest of five) ranked
third at 40.0, every point of it from team context. Min-max within the set
turned a 5-point weather gap and one Questionable tag into full 0-to-100
swings, so Buffalo's game outweighed an 18-spot consensus edge.
"""

from __future__ import annotations

import pytest

from ff_startsit.config import Settings
from ff_startsit.engine.normalize import fixed_0_100, to_0_100
from ff_startsit.models import Player, SignalValue
from ff_startsit.pipeline import recommend
from ff_startsit.sources.injury import InjurySignal
from ff_startsit.sources.vegas import VegasSignal
from ff_startsit.sources.weather import WeatherSignal
from ff_startsit.sources.ecr import ECRSignal


class _Fake:
    is_sample = False
    served_wrong_week = False
    source_status: list = []

    def __init__(self, name, values, higher_is_better=True, fixed_scale=False):
        self.name = name
        self.values = values
        self.higher_is_better = higher_is_better
        self.fixed_scale = fixed_scale

    def is_available(self):
        return True

    def fetch(self, week, players):
        return {p.key: SignalValue(self.values[p.key]) for p in players}

    def rules_out(self, value):
        return False


WRS = [Player("collins", "Nico Collins", "HOU", "WR"),
       Player("vele", "Devaughn Vele", "NO", "WR"),
       Player("coleman", "Keon Coleman", "BUF", "WR"),
       Player("cooper", "Omar Cooper Jr.", "NYJ", "WR"),
       Player("wilson", "Garrett Wilson", "NYJ", "WR")]
ECR = {"collins": 13, "vele": 39, "coleman": 53, "cooper": 35, "wilson": 37}
# Implied team totals reproducing the live VEGAS column (81, 77, 100, 0, 0).
VEGAS = {"collins": 24.48, "vele": 24.16, "coleman": 26.0, "cooper": 18.0, "wilson": 18.0}
INJURY = {"collins": 100, "vele": 100, "coleman": 100, "cooper": 100, "wilson": 75}
WEATHER = {"collins": 100, "vele": 100, "coleman": 100, "cooper": 95, "wilson": 95}


def _rank(fixed: bool):
    signals = [_Fake("ecr", ECR, higher_is_better=False),
               _Fake("vegas", VEGAS),
               _Fake("injury", INJURY, fixed_scale=fixed),
               _Fake("weather", WEATHER, fixed_scale=fixed)]
    settings = Settings(weights={"ecr": 0.60, "vegas": 0.18, "injury": 0.12,
                                 "weather": 0.10})
    rec = recommend(settings, WRS, 9, signals=signals, log=False)
    return rec, [s.player.name for s in rec.scores]


def test_the_live_table_as_it_was():
    rec, order = _rank(fixed=False)
    assert order[2] == "Keon Coleman"
    finals = {s.player.key: s.final for s in rec.scores}
    assert finals["coleman"] == pytest.approx(40.0)
    assert finals["cooper"] == pytest.approx(39.0)


def test_team_context_no_longer_outranks_the_consensus():
    rec, order = _rank(fixed=True)
    assert order == ["Nico Collins", "Devaughn Vele", "Omar Cooper Jr.",
                     "Garrett Wilson", "Keon Coleman"]
    by_key = {s.player.key: s for s in rec.scores}
    # Questionable costs a quarter of injury's weight, not all of it.
    assert by_key["wilson"].normalized["injury"] == 75.0
    # A 5-point weather gap stays 5 points.
    assert by_key["cooper"].normalized["weather"] == 95.0
    assert by_key["cooper"].final == pytest.approx(48.5)


def test_no_read_with_care_note_for_an_absolute_column():
    """flat_signals exists because min-max magnifies small gaps; a column on
    its own 0-100 scale shows the gap as it is."""
    rec, _ = _rank(fixed=True)
    rec.raw_gaps = {"weather": 12.0}
    assert rec.flat_signals() == []
    rec_old, _ = _rank(fixed=False)
    rec_old.raw_gaps = {"weather": 12.0}
    assert [name for name, *_ in rec_old.flat_signals()] == ["weather"]


def test_only_the_absolute_signals_declare_it():
    assert InjurySignal.fixed_scale and WeatherSignal.fixed_scale
    assert not ECRSignal.fixed_scale and not VegasSignal.fixed_scale


def test_the_min_max_transform_itself_is_untouched():
    """CLAUDE.md: changing to_0_100 would make logged rows unreplayable."""
    assert to_0_100({"a": 75.0, "b": 100.0}, True) == {"a": 0.0, "b": 100.0}
    assert fixed_0_100({"a": 75.0, "b": 100.0, "c": None, "d": 140.0}) == \
        {"a": 75.0, "b": 100.0, "d": 100.0}
