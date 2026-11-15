"""Start/sit builds the league's real lineup, not the 1QB/2RB/2WR template.

``cli._score_league`` never passed ``slots`` to ``report.score_week`` and
``report.lineup_from`` always built ``LINEUP_SLOTS``, so a superflex league
benched its second quarterback, a two-flex league left a starter on the bench,
and a league without kickers was told to start one. The waiver pass already
read the league's slots; both now share ``report.league_slots``.
"""
import argparse
import json

import pytest

from ff_startsit import cli, report
from ff_startsit.config import Settings
from ff_startsit.models import Player, SignalValue
from ff_startsit.roster.base import RosterProvider
from ff_startsit.sources.base import Signal
from ff_startsit.waivers.base import LeagueViewProvider
from ff_startsit.waivers.models import LeagueRules

from .conftest import FIXTURES, REAL_AVAILABILITY_VIEW

TEMPLATE_RULES = LeagueRules(
    roster_slots={"QB": 1, "RB": 2, "WR": 2, "TE": 1, "K": 1, "DEF": 1},
    flex_slots={"FLEX": 1})


# --- the shared slot list ------------------------------------------------------
def test_template_shaped_rules_give_the_template_in_its_order():
    assert report.league_slots(TEMPLATE_RULES) == report.LINEUP_SLOTS


def test_no_rules_give_the_template():
    assert report.league_slots(LeagueRules()) == report.LINEUP_SLOTS


def test_a_league_that_reports_slots_but_no_flex_gets_no_flex():
    """Both platforms read flex slots from the same data as position slots,
    so known position slots with no flex mean the league has no flex. The
    fallback FLEX is only for a platform that reported nothing at all."""
    rules = LeagueRules(roster_slots={"QB": 1, "RB": 2, "WR": 2, "TE": 1, "K": 1, "DEF": 1})
    assert "FLEX" not in report.league_slots(rules)


def test_reported_flex_with_no_position_slots_is_kept():
    rules = LeagueRules(flex_slots={"SUPER_FLEX": 1})
    slots = report.league_slots(rules)
    assert slots.count("SUPER_FLEX") == 1 and "FLEX" not in slots


def test_flex_slots_follow_every_position_they_can_take():
    rules = LeagueRules(roster_slots={"QB": 1, "RB": 2, "WR": 3, "TE": 1, "DEF": 1},
                        flex_slots={"SUPER_FLEX": 1, "FLEX": 2})
    slots = report.league_slots(rules)
    assert slots == ["QB", "RB", "RB", "WR", "WR", "WR", "TE",
                     "FLEX", "FLEX", "SUPER_FLEX", "DEF"]


def test_waiver_drop_protection_reads_the_same_slots(monkeypatch):
    """`_lineup_keys` is the other caller; it must build from the same list."""
    from ff_startsit.waivers import build

    seen = []
    real = report.build_lineup

    def spy(by_pos, **kwargs):
        seen.append(kwargs.get("slots"))
        return real(by_pos, **kwargs)
    monkeypatch.setattr(report, "build_lineup", spy)
    rules = LeagueRules(roster_slots={"QB": 1, "RB": 2}, flex_slots={"SUPER_FLEX": 1})
    build._lineup_keys([], rules)
    assert seen == [report.league_slots(rules)]


# --- through _score_league -----------------------------------------------------
class _Ranks(Signal):
    """ECR-like: a lower rank is better, one rank per player key."""
    name = "ecr"
    higher_is_better = False

    def __init__(self, ranks):
        self.ranks = ranks

    def is_available(self):
        return True

    def fetch(self, week, players):
        return {p.key: SignalValue(self.ranks.get(p.key)) for p in players}


class _LeagueProvider(RosterProvider, LeagueViewProvider):
    """A league source with no ESPN outlook (Sleeper-shaped)."""
    name = "sleeper"

    def __init__(self, rules, fail=False):
        self.rules, self.fail, self.rule_reads = rules, fail, 0

    def cache_tag(self):
        return "fake"

    def get_roster_players(self):
        return []

    def get_league_teams(self):
        return []

    def get_free_agents(self, week, limit=150, positions=None):
        return []

    def get_league_rules(self):
        self.rule_reads += 1
        if self.fail:
            raise RuntimeError("settings endpoint exploded")
        return self.rules


class _ManualProvider(RosterProvider):
    name = "manual"

    def cache_tag(self):
        return "manual"

    def get_roster_players(self):
        return []


def _roster():
    spec = [("qb1", "QB"), ("qb2", "QB"), ("rb1", "RB"), ("rb2", "RB"), ("rb3", "RB"),
            ("wr1", "WR"), ("wr2", "WR"), ("wr3", "WR"), ("te1", "TE"),
            ("k1", "K"), ("def1", "DEF")]
    return [Player(key, key.upper(), "KC", pos) for key, pos in spec]


def _score(monkeypatch, tmp_path, provider, offline=False, roster=None):
    roster = roster if roster is not None else _roster()
    ranks = {p.key: float(i + 1) for i, p in enumerate(roster)}
    monkeypatch.setattr(cli, "_availability_view", REAL_AVAILABILITY_VIEW)
    monkeypatch.setattr(cli, "build_roster_provider", lambda *a, **k: provider)
    monkeypatch.setattr(cli.season, "preseason_banner", lambda settings: None)
    monkeypatch.setattr(report, "build_signals", lambda settings: [_Ranks(ranks)])
    settings = Settings(data_dir=tmp_path, weights={"ecr": 1.0})
    args = argparse.Namespace(source=None, league=None, team=None, offline=offline)
    return cli._score_league(args, settings, None, roster, 9)


def _starters(lineup):
    return [(slot, pick.player.key if pick else None) for slot, pick in lineup]


def test_superflex_league_starts_the_second_quarterback(monkeypatch, tmp_path):
    rules = LeagueRules(roster_slots={"QB": 1, "RB": 2, "WR": 2, "TE": 1, "K": 1, "DEF": 1},
                        flex_slots={"FLEX": 1, "SUPER_FLEX": 1})
    # No spare WR: SUPER_FLEX compares per-position scores (by design), and a
    # third receiver would tie the second QB there. Here QB2 is the one left.
    roster = [p for p in _roster() if p.key != "wr3"]
    ws, lineup = _score(monkeypatch, tmp_path, _LeagueProvider(rules), roster=roster)
    assert ("SUPER_FLEX", "qb2") in _starters(lineup)
    # The stored slots are what the boundary flag saw and what renders.
    assert ws.slots == report.league_slots(rules)
    assert [slot for slot, _ in lineup] == ws.slots


def test_two_flex_league_fills_both_flex_slots(monkeypatch, tmp_path):
    rules = LeagueRules(roster_slots={"QB": 1, "RB": 2, "WR": 2, "TE": 1, "K": 1, "DEF": 1},
                        flex_slots={"FLEX": 2})
    _, lineup = _score(monkeypatch, tmp_path, _LeagueProvider(rules))
    flex = [key for slot, key in _starters(lineup) if slot == "FLEX"]
    assert sorted(flex) == ["rb3", "wr3"]


def test_league_without_a_kicker_slot_starts_no_kicker(monkeypatch, tmp_path):
    rules = LeagueRules(roster_slots={"QB": 1, "RB": 2, "WR": 2, "TE": 1, "DEF": 1},
                        flex_slots={"FLEX": 1})
    _, lineup = _score(monkeypatch, tmp_path, _LeagueProvider(rules))
    assert "K" not in [slot for slot, _ in lineup]
    assert "k1" not in [key for _, key in _starters(lineup)]


def test_boundary_flag_sees_the_lineup_that_renders(monkeypatch, tmp_path):
    """The boundary pair is resolved from a lineup; it must be the same shape
    as the one `lineup_from` renders, or the flag warns about a slot the
    report doesn't have."""
    seen = []
    real = report.flag_starter_boundaries

    def spy(settings, recs, lineup):
        seen.append([slot for slot, _ in lineup])
        return real(settings, recs, lineup)
    monkeypatch.setattr(report, "flag_starter_boundaries", spy)
    rules = LeagueRules(roster_slots={"QB": 1, "RB": 2, "WR": 2, "TE": 1, "K": 1, "DEF": 1},
                        flex_slots={"FLEX": 1, "SUPER_FLEX": 1})
    _, lineup = _score(monkeypatch, tmp_path, _LeagueProvider(rules))
    assert seen == [[slot for slot, _ in lineup]]
    assert "SUPER_FLEX" in seen[0]


def test_manual_source_keeps_the_template(monkeypatch, tmp_path):
    ws, lineup = _score(monkeypatch, tmp_path, _ManualProvider())
    assert [slot for slot, _ in lineup] == report.LINEUP_SLOTS
    assert ws.slots is None


@pytest.mark.parametrize("rules", [LeagueRules(), None])
def test_empty_or_failed_rules_keep_the_template(monkeypatch, tmp_path, rules):
    provider = _LeagueProvider(rules, fail=rules is None)
    _, lineup = _score(monkeypatch, tmp_path, provider)
    assert [slot for slot, _ in lineup] == report.LINEUP_SLOTS
    assert provider.rule_reads == 1


def test_offline_run_reads_no_rules(monkeypatch, tmp_path):
    provider = _LeagueProvider(TEMPLATE_RULES)
    _, lineup = _score(monkeypatch, tmp_path, provider, offline=True)
    assert provider.rule_reads == 0
    assert [slot for slot, _ in lineup] == report.LINEUP_SLOTS


def test_preseason_run_reads_no_rules(monkeypatch, tmp_path):
    provider = _LeagueProvider(TEMPLATE_RULES)
    monkeypatch.setattr(cli, "_availability_view", REAL_AVAILABILITY_VIEW)
    monkeypatch.setattr(cli, "build_roster_provider", lambda *a, **k: provider)
    monkeypatch.setattr(cli.season, "preseason_banner", lambda settings: "PRESEASON")
    monkeypatch.setattr(report, "build_signals",
                        lambda settings: [_Ranks({p.key: 1.0 for p in _roster()})])
    args = argparse.Namespace(source=None, league=None, team=None, offline=False)
    cli._score_league(args, Settings(data_dir=tmp_path, weights={"ecr": 1.0}),
                      None, _roster(), 9)
    assert provider.rule_reads == 0


# --- request budget ------------------------------------------------------------
class _Resp:
    def __init__(self, body):
        self.status_code, self._body = 200, body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


class _CountingSession:
    def __init__(self, body):
        self.body, self.calls = body, []

    def get(self, url, params=None, **kwargs):
        self.calls.append(params)
        return _Resp(self.body)


def test_espn_pass_makes_one_extra_request_for_the_rules(monkeypatch, tmp_path):
    """The outlook request is the existing one; the league payload (which
    carries mSettings) is the only new one, and it is read once."""
    from ff_startsit.roster.espn import LEAGUE_VIEWS, ESPNProvider

    session = _CountingSession(json.loads((FIXTURES / "espn_league.json").read_text()))
    provider = ESPNProvider(league_id="1", season="2026", team_id="1", session=session)
    players = [Player("espn-1", "QB ONE", "KC", "QB")] + _roster()[1:]
    monkeypatch.setattr(cli, "_availability_view", REAL_AVAILABILITY_VIEW)
    monkeypatch.setattr(cli, "build_roster_provider", lambda *a, **k: provider)
    monkeypatch.setattr(cli.season, "preseason_banner", lambda settings: None)
    monkeypatch.setattr(report, "build_signals",
                        lambda settings: [_Ranks({p.key: 1.0 for p in players})])
    args = argparse.Namespace(source=None, league=None, team=None, offline=False)
    cli._score_league(args, Settings(data_dir=tmp_path, weights={"ecr": 1.0}),
                      None, players, 9)
    league_reads = [c for c in session.calls if c == LEAGUE_VIEWS]
    outlook_reads = [c for c in session.calls if c != LEAGUE_VIEWS]
    assert len(league_reads) == 1
    assert len(outlook_reads) == 1


def test_sleeper_rules_read_with_a_known_league_id_is_one_request(tmp_path):
    """Rules need only the league id. Resolving the user first cost three
    more requests (state, user, leagues) for an id the profile already had."""
    from ff_startsit.roster.sleeper import SleeperProvider

    calls = []

    class _Client:
        def current_season(self):
            calls.append("state")
            return "2026"

        def resolve_user_id(self, username):
            calls.append("user")
            return "u1"

        def pick_league(self, user_id, season, league_id=""):
            calls.append("leagues")
            return league_id or "L1"

        def league_settings(self, league_id):
            calls.append(("league", league_id))
            return {"roster_positions": ["QB", "RB", "SUPER_FLEX", "BN"]}

    provider = SleeperProvider(username="me", league_id="L9", data_dir=tmp_path)
    provider.client = _Client()
    rules = provider.get_league_rules()
    assert calls == [("league", "L9")]
    assert rules.flex_slots == {"SUPER_FLEX": 1}
