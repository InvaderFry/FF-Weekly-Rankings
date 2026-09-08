import json
from datetime import datetime, timezone

import pytest

from ff_startsit.models import Player
from ff_startsit.waivers.season_values import (
    SeasonValueProvider, comparable_trade, parse_season_rows, protected_rank,
)

NOW = datetime(2026, 9, 8, tzinfo=timezone.utc)


def _html(**updates):
    data = dict(year="2026", ranking_type_name="ros", position_id="ALL",
                scoring="HALF", last_updated_ts=NOW.timestamp(), players=[
                    dict(player_name="Mike Evans", player_team_id="SF",
                         player_position_id="WR", rank_ecr=35),
                    dict(player_name="Seattle Seahawks", player_team_id="SEA",
                         player_position_id="DST", rank_ecr=180)])
    data.update(updates)
    return "var ecrData = " + json.dumps(data) + ";"


@pytest.mark.parametrize("updates", [
    {"year": "2025"}, {"ranking_type_name": "weekly"}, {"scoring": "PPR"},
    {"position_id": "WR"}, {"last_updated_ts": NOW.timestamp() - 15 * 86400},
    {"last_updated_ts": 0}, {"last_updated_ts": float("nan")}, {"players": []},
])
def test_unusable_rankings_cannot_authorize_moves(updates):
    with pytest.raises(ValueError):
        parse_season_rows(_html(**updates), "half", NOW)


def test_current_overall_ranks_are_parsed_and_matched(monkeypatch):
    monkeypatch.setattr("ff_startsit.waivers.season_values.season_year", lambda today: 2026)

    class Response:
        text = _html(last_updated_ts=datetime.now(timezone.utc).timestamp())

        def raise_for_status(self):
            pass

    class Session:
        def get(self, url, **kwargs):
            assert "ros-half-point-ppr-overall" in url
            return Response()

    players = [Player("evans", "Mike Evans", "SF", "WR"),
               Player("sea", "Seahawks D/ST", "SEA", "DEF"),
               Player("unknown", "Unknown Player", "SF", "WR")]
    values = SeasonValueProvider(session=Session()).fetch(players, "half")
    assert values == {"evans": 35, "sea": 180}


def test_trade_rank_guards_require_both_values_and_a_small_gap():
    assert comparable_trade(40, 48)
    assert not comparable_trade(12, 80)
    assert not comparable_trade(200, 240)
    assert not comparable_trade(None, 40)
    assert protected_rank(8) == 100
    assert protected_rank(14) == 112
