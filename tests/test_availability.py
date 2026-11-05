"""The start/sit availability check: likely-out benching and lineup holes.

The case that prompted it is pinned by a trimmed live ESPN response
(``espn_outlook_week9.json``, saved 2026-11-05): Kyler Murray listed
QUESTIONABLE with a Week 9 projection of 0.0 on the news that he would sit.
"""

from __future__ import annotations

import pytest

from ff_startsit import report
from ff_startsit.availability import (LineupHole, Outlook, StreamOption, likely_out,
                                      lineup_holes, parse_outlook, stream_options,
                                      week_projection)
from ff_startsit.config import Settings
from ff_startsit.models import Player, PlayerScore, SignalValue
from ff_startsit.roster.espn import free_agent_filter, parse_free_agents
from ff_startsit.sources.injury import InjurySignal, assign, parse_injury_rows
from ff_startsit.waivers.models import PoolPlayer

from .conftest import load_fixture

KYLER = "espn-3917315"
GIBBS = "espn-4429795"


# --- reading ESPN ------------------------------------------------------------

def test_parse_outlook_reads_the_live_kyler_case():
    outlook = parse_outlook(load_fixture("espn_outlook_week9.json"), 9, 2026)
    assert outlook[KYLER] == Outlook(projection=0.0, status="QUESTIONABLE")
    assert outlook[GIBBS].projection == pytest.approx(26.54, abs=0.01)
    assert outlook[GIBBS].status == "ACTIVE"


def test_week_projection_ignores_actuals_season_totals_and_last_season():
    info = load_fixture("espn_outlook_week9.json")["players"][1]["player"]  # Gibbs
    # 2025 Week 9 carries a projection (19.19) and an actual (5.8); neither is
    # this season's Week 9 projection.
    assert week_projection(info, 9, 2026) == pytest.approx(26.54, abs=0.01)
    assert week_projection(info, 10, 2026) == pytest.approx(24.12, abs=0.01)
    assert week_projection(info, 11, 2026) is None


def test_free_agents_carry_their_projection():
    payload = load_fixture("espn_outlook_week9.json")
    pool = parse_free_agents(payload, week=9, season=2026)
    by_name = {pp.player.name: pp for pp in pool}
    assert by_name["Kyler Murray"].projection == 0.0
    assert by_name["Jahmyr Gibbs"].projection == pytest.approx(26.54, abs=0.01)
    # Without a week the old behaviour stands: no projection claimed.
    assert parse_free_agents(payload)[0].projection is None


def test_free_agent_filter_can_ask_for_one_position():
    body = free_agent_filter(9, 50, slot_ids=[0])
    assert body["players"]["filterSlotIds"]["value"] == [0]
    assert len(free_agent_filter(9, 50)["players"]["filterSlotIds"]["value"]) == 6


# --- likely out ----------------------------------------------------------------

def _p(key, name, team, pos="QB"):
    return Player(key, name, team, pos)


def test_questionable_at_zero_on_a_game_week_is_likely_out():
    kyler = _p(KYLER, "Kyler Murray", "MIN")
    out = likely_out([kyler], {KYLER: Outlook(0.0, "QUESTIONABLE")}, {"MIN", "DET"})
    assert out[KYLER].startswith("Likely out: ESPN projects 0.0 pts despite Questionable")


@pytest.mark.parametrize("outlook, playing", [
    (Outlook(0.0, "QUESTIONABLE"), {"DET"}),       # his team is on bye: 0.0 is the bye
    (Outlook(0.0, "QUESTIONABLE"), None),          # no schedule: can't tell a bye apart
    (Outlook(0.0, "ACTIVE"), {"MIN"}),             # healthy at zero is a backup, not an injury
    (Outlook(0.0, "OUT"), {"MIN"}),                # already ruled out on his own
    (Outlook(13.6, "QUESTIONABLE"), {"MIN"}),      # ESPN expects him to play
    (Outlook(None, "QUESTIONABLE"), {"MIN"}),      # no projection: no claim
])
def test_likely_out_claims_nothing_without_all_three_readings(outlook, playing):
    assert likely_out([_p(KYLER, "Kyler Murray", "MIN")], {KYLER: outlook}, playing) == {}


def test_injury_signal_scores_likely_out_as_ruled_out():
    meta = {"1": {"full_name": "Kyler Murray", "position": "QB", "team": "MIN",
                  "injury_status": "Questionable"},
            "2": {"full_name": "Hurt Guy", "position": "QB", "team": "DET",
                  "injury_status": "Out"}}
    rows = parse_injury_rows(meta)
    kyler, hurt = _p(KYLER, "Kyler Murray", "MIN"), _p("espn-2", "Hurt Guy", "DET")
    plain = assign([kyler], rows)
    assert plain[KYLER].raw == 75.0
    vals = assign([kyler, hurt], rows, {KYLER: "Likely out: x", "espn-2": "Likely out: y"})
    assert vals[KYLER].raw == 0.0 and vals[KYLER].note == "Likely out: x"
    assert InjurySignal(data_dir=None).rules_out(vals[KYLER])
    # An official Out keeps its own designation rather than the softer note.
    assert vals["espn-2"].note == "Out"


# --- through score_week --------------------------------------------------------

class _Schedule:
    def __init__(self, teams):
        self.teams = teams

    def for_week(self, week):
        return {t: object() for t in self.teams}


class _FakeECR:
    name = "ecr"
    higher_is_better = False
    is_sample = False
    served_wrong_week = False
    source_status: list = []

    def __init__(self, ranks, schedule):
        self.ranks = ranks
        self.schedule = schedule

    def is_available(self):
        return True

    def fetch(self, week, players):
        return {p.key: (SignalValue(self.ranks[p.key]) if p.key in self.ranks
                        else SignalValue(None, available=False, note="no ECR rank"))
                for p in players}


class _MetaClient:
    def __init__(self, meta):
        self.meta = meta

    def load_player_metadata(self):
        return self.meta


def _week(monkeypatch, tmp_path, players, ranks, statuses, outlook, playing):
    meta = {str(i): {"full_name": p.name, "position": p.position, "team": p.team,
                     "injury_status": statuses.get(p.key, "")}
            for i, p in enumerate(players)}
    signals = [_FakeECR(ranks, _Schedule(playing)),
               InjurySignal(data_dir=tmp_path, client=_MetaClient(meta))]
    monkeypatch.setattr(report, "build_signals", lambda settings: signals)
    settings = Settings(data_dir=tmp_path, weights={"ecr": 0.6, "injury": 0.12})
    ws = report.score_week(settings, players, 9, outlook=outlook)
    return ws, report.lineup_from(ws)


def _qb(lineup):
    return next(pick for slot, pick in lineup if slot == "QB")


def test_a_likely_out_starter_is_benched_for_his_backup(monkeypatch, tmp_path):
    kyler = _p(KYLER, "Kyler Murray", "MIN")
    backup = _p("espn-9", "Backup Qb", "DET")
    ws, lineup = _week(monkeypatch, tmp_path, [kyler, backup],
                       ranks={KYLER: 8.0, "espn-9": 25.0},
                       statuses={KYLER: "Questionable"},
                       outlook={KYLER: Outlook(0.0, "QUESTIONABLE"),
                                "espn-9": Outlook(14.0, "ACTIVE")},
                       playing={"MIN", "DET"})
    assert _qb(lineup).player.key == "espn-9"
    benched = {s.player.key: s for s in ws.recs["QB"].scores}[KYLER]
    assert benched.final is None
    assert any("Likely out" in f for f in benched.flags)
    report.check_availability(ws, lineup, 9, free_agents=lambda pos: [])
    # (These tiny rosters leave RB/WR/TE empty, which are holes of their own.)
    assert _qb_holes(lineup) == []


def _qb_holes(lineup):
    return [h for h in lineup.holes if h.slot == "QB"]


def test_without_the_projection_he_still_starts(monkeypatch, tmp_path):
    """The old behaviour, and what a failed ESPN read degrades to."""
    kyler = _p(KYLER, "Kyler Murray", "MIN")
    backup = _p("espn-9", "Backup Qb", "DET")
    _, lineup = _week(monkeypatch, tmp_path, [kyler, backup],
                      ranks={KYLER: 8.0, "espn-9": 25.0},
                      statuses={KYLER: "Questionable"}, outlook=None,
                      playing={"MIN", "DET"})
    assert _qb(lineup).player.key == KYLER


def _pool():
    def pp(key, name, team, rank_status="", proj=15.0, owned=10.0, pos="QB"):
        return PoolPlayer(Player(key, name, team, pos), percent_owned=owned,
                          injury_status=rank_status, projection=proj)
    return [
        pp("fa-1", "Ranked Fourteen", "DET"),
        pp("fa-2", "Ranked Twenty", "KC", owned=3.0),
        pp("fa-3", "On Bye", "BUF"),
        pp("fa-4", "Hurt Starter", "KC", rank_status="OUT"),
        pp("fa-5", "Clipboard Holder", "DET", proj=0.0),
        pp("fa-6", "Ranked Eleven", "KC", rank_status="QUESTIONABLE", proj=17.2),
        pp("fa-7", "Unranked Arm", "DET", proj=9.0),
        pp("fa-8", "A Receiver", "DET", pos="WR"),
    ]


_RANKS = {"fa-1": 14.0, "fa-2": 20.0, "fa-3": 5.0, "fa-4": 2.0, "fa-5": 30.0,
          "fa-6": 11.0}


def test_lone_qb_ruled_out_is_a_hole_with_three_fresh_pickups(monkeypatch, tmp_path):
    kyler = _p(KYLER, "Kyler Murray", "MIN")
    ws, lineup = _week(monkeypatch, tmp_path, [kyler], ranks={KYLER: 8.0, **_RANKS},
                       statuses={KYLER: "Questionable"},
                       outlook={KYLER: Outlook(0.0, "QUESTIONABLE")},
                       playing={"MIN", "DET", "KC"})
    asked = []

    def free_agents(positions):
        asked.append(positions)
        return _pool()

    report.check_availability(ws, lineup, 9, free_agents=free_agents)
    assert len(asked) == 1                          # one request for every hole
    assert "QB" in asked[0] and "K" not in asked[0]
    [hole] = _qb_holes(lineup)
    assert hole.slot == "QB" and hole.starter == "Kyler Murray"
    assert "Likely out" in hole.reason
    # Ranked first, in FantasyPros order; bye, OUT, projected-out and the
    # receiver are filtered; the unranked QB would only fill a fourth spot.
    assert [o.player.name for o in hole.options] == [
        "Ranked Eleven", "Ranked Fourteen", "Ranked Twenty"]
    text = "\n".join(report.hole_lines(lineup))
    assert "🚨 No healthy QB for your QB slot: Kyler Murray" in text
    assert "Ranked Eleven (KC) — QB11, 17.2 proj, Questionable, 10% rostered" in text


def test_an_official_out_with_no_backup_is_a_hole_too(monkeypatch, tmp_path):
    kyler = _p(KYLER, "Kyler Murray", "MIN")
    ws, lineup = _week(monkeypatch, tmp_path, [kyler], ranks={KYLER: 8.0},
                       statuses={KYLER: "Out"}, outlook={},
                       playing={"MIN"})
    report.check_availability(ws, lineup, 9, free_agents=None)
    [hole] = _qb_holes(lineup)
    assert hole.reason == "Out"
    assert hole.options_note == "free agents weren't checked for this league"


def test_empty_kicker_and_defense_slots_are_not_alarms():
    """The start/sit template has a K and a DEF; plenty of leagues have neither."""
    holes = lineup_holes([("K", None), ("DEF", None), ("TE", None)], {"KC"})
    assert [h.slot for h in holes] == ["TE"]


def test_a_bye_with_a_healthy_spare_is_not_a_hole():
    def ps(key, team, pos, final=60.0):
        s = PlayerScore(player=Player(key, key, team, pos), final=final)
        s.raw["injury"] = SignalValue(100.0)
        return s
    on_bye, spare = ps("k1", "BUF", "K"), ps("k2", "KC", "K", 40.0)
    assert lineup_holes([("K", on_bye)], {"KC"}, [on_bye, spare]) == []
    [hole] = lineup_holes([("K", on_bye)], {"KC"}, [on_bye])
    assert hole.reason == "on bye"


def test_flex_hole_ranks_on_the_pooled_list_and_superflex_streams_a_qb():
    pool = _pool()
    seen = []

    def rank(position, players):
        seen.append((position, sorted(p.position for p in players)))
        return {}
    stream_options("FLEX", pool, rank, {"DET", "KC"}, 9)
    assert seen[0] == ("FLEX", ["WR"])
    [hole] = lineup_holes([("SUPER_FLEX", None)], None)
    assert hole.position == "QB"


def test_sample_runs_report_no_holes():
    class _Sample:
        is_sample = True
    ws = report.WeekScores(recs={}, signals=[_Sample()])
    lineup = report.Lineup(slots=[("QB", None)])
    report.check_availability(ws, lineup, 1, free_agents=lambda p: pytest.fail("no"))
    assert lineup.holes == []


def test_a_failed_wire_read_still_shows_the_warning():
    hole = LineupHole(slot="QB", position="QB", starter="Kyler Murray", reason="Out")
    from ff_startsit.availability import fill_options

    def boom(positions):
        raise RuntimeError("503")
    fill_options([hole], boom, None, {"MIN"}, 9)
    assert hole.options == [] and "503" in hole.options_note


# --- renderers and the Discord gate --------------------------------------------

def _holed_lineup():
    kyler = PlayerScore(player=_p(KYLER, "Kyler Murray", "MIN"), final=50.0)
    lineup = report.Lineup(slots=[("QB", kyler)])
    lineup.holes = [LineupHole(
        slot="QB", position="QB", starter="Kyler Murray",
        reason="Likely out: ESPN projects 0.0 pts despite Questionable",
        options=[StreamOption(Player("fa-1", "D'Andre Arm", "DET", "QB"), rank=14.0,
                              projection=15.2, percent_owned=12.0)])]
    return lineup


def test_every_renderer_leads_with_the_hole():
    from ff_startsit.output.discord import _build_embed
    from ff_startsit.output.html import _dashboard_body
    lineup = _holed_lineup()
    md = "\n".join(report._digest_body({}, lineup=lineup))
    assert md.index("🚨") < md.index("## Suggested lineup")
    assert "D'Andre Arm (DET) — QB14, 15.2 proj, 12% rostered" in md
    html = "".join(_dashboard_body(lineup, {}))
    assert html.index("🚨") < html.index("Suggested lineup")
    assert "D&#x27;Andre Arm" in html
    embed = _build_embed(9, "ppr", lineup, {})
    assert embed["description"].startswith("**🚨 No healthy QB")
    assert embed["color"] == 0xD29922


def _bundle(lineup):
    return report.LeagueBundle(label="HailMeowy", scoring="ppr", recs={}, lineup=lineup)


def test_a_new_hole_on_an_unchanged_name_is_news_for_discord():
    before = report.lineup_marker(2026, 9, [_bundle(report.Lineup(
        slots=[("QB", PlayerScore(player=_p(KYLER, "Kyler Murray", "MIN"), final=50.0))]))])
    after = report.lineup_marker(2026, 9, [_bundle(_holed_lineup())])
    changes = report.lineup_changes_since(after, before)
    assert changes == ["- **HailMeowy:** no healthy player for QB "
                       "(Kyler Murray won't play), see pickups."]
    # The same hole on the next pass is not news again.
    assert report.lineup_changes_since(after, after) == []


# --- the CLI wiring ------------------------------------------------------------

def test_score_league_wires_espn_projections_and_a_fresh_wire_read(monkeypatch, tmp_path):
    """Every start/sit command goes through `cli._score_league`; pin that it
    reads ESPN's outlook, benches on it, and fills the hole from the wire."""
    import argparse
    from ff_startsit import cli
    from .conftest import REAL_AVAILABILITY_VIEW

    kyler = _p(KYLER, "Kyler Murray", "MIN")
    calls = {"outlook": 0, "fa": []}

    class _Provider:
        def get_week_outlook(self, week, players):
            calls["outlook"] += 1
            return {KYLER: Outlook(0.0, "QUESTIONABLE")}

        def get_free_agents(self, week, positions=None):
            calls["fa"].append(positions)
            return _pool()

    monkeypatch.setattr(cli, "_availability_view", REAL_AVAILABILITY_VIEW)
    monkeypatch.setattr(cli, "build_roster_provider", lambda *a, **k: _Provider())
    monkeypatch.setattr(cli.season, "preseason_banner", lambda settings: None)
    meta = {"1": {"full_name": "Kyler Murray", "position": "QB", "team": "MIN",
                  "injury_status": "Questionable"}}
    signals = [_FakeECR({KYLER: 8.0, **_RANKS}, _Schedule({"MIN", "DET", "KC"})),
               InjurySignal(data_dir=tmp_path, client=_MetaClient(meta))]
    monkeypatch.setattr(report, "build_signals", lambda settings: signals)

    settings = Settings(data_dir=tmp_path, weights={"ecr": 0.6, "injury": 0.12},
                        league_label="HailMeowy")
    args = argparse.Namespace(source=None, league=None, team=None, offline=False)
    ws, lineup = cli._score_league(args, settings, None, [kyler], 9)

    assert calls["outlook"] == 1 and len(calls["fa"]) == 1
    [hole] = _qb_holes(lineup)
    assert hole.options[0].player.name == "Ranked Eleven"
    status = [line for rec in ws.recs.values() for _, line in rec.source_status]
    assert any("ESPN weekly projections for 1/1 roster players" in s for s in status)
