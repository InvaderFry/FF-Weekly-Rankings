"""Expert boards: side by side with the blend, never inside it."""
import json
from dataclasses import replace

from ff_startsit import cli, report
from ff_startsit.boards.boone import BooneWeeklySource, board_from_analyst_ranks
from ff_startsit.boards.fantasypros import fantasypros_weekly
from ff_startsit.boards.models import ALL_POSITIONS, BoardEntry, ExpertBoard
from ff_startsit.config import Settings
from ff_startsit.models import Player, SignalValue
from ff_startsit.output import html, render
from ff_startsit.output.board import board_cell, format_rank, section_boards
from ff_startsit.sources.analysts import AnalystRanks
from ff_startsit.sources.base import Signal


class Ecr(Signal):
    """ECR rank = key + 1, so Player 0 is FP #1."""
    name = "ecr"
    higher_is_better = False
    is_sample = False

    def is_available(self):
        return True

    def fetch(self, week, players):
        return {p.key: SignalValue(float(p.key) + 1) for p in players}


class SampleEcr(Ecr):
    is_sample = True


class Boone:
    """Boone reverses FP among receivers and has no tight-end list yet."""
    def __init__(self):
        self.calls = 0

    def fetch(self, players, week, scoring):
        self.calls += 1
        wrs = [p for p in players if p.position == "WR"]
        return AnalystRanks("Justin Boone", scoring,
                            by_position={"WR": {p.key: float(len(wrs) - i)
                                                for i, p in enumerate(wrs)}},
                            unavailable={"TE": "no Full-PPR list published for this position"})


def roster():
    return [Player("0", "Alpha O'Neil", "KC", "WR"), Player("1", "Bravo", "BUF", "WR"),
            Player("2", "Charlie", "DET", "WR"), Player("3", "Delta", "SF", "TE"),
            Player("4", "Echo", "LAR", "TE")]


def ranked(settings, signals=None, fetcher=None):
    kwargs = {"analyst_fetcher": fetcher} if fetcher is not None else {}
    return report.rank_each_position(settings, roster(), 9,
                                     signals=signals or [Ecr()], **kwargs)


# --- the boards themselves -------------------------------------------------

def test_fantasypros_board_reads_the_ecr_rank_already_fetched():
    recs = ranked(Settings())
    board = fantasypros_weekly(recs.values())
    assert board.short == "FP" and board.label == "FantasyPros consensus"
    assert {k: e.pos_rank for k, e in board.entries.items()} == {
        "0": 1.0, "1": 2.0, "2": 3.0, "3": 4.0, "4": 5.0}


def test_sample_run_gets_no_board():
    """Preseason sample ranks are nobody's opinion and must not be shown as FP's."""
    recs = ranked(Settings(), signals=[SampleEcr()])
    assert all(rec.sample_data for rec in recs.values())
    assert fantasypros_weekly(recs.values()) is None
    assert all(rec.expert_boards == [] for rec in recs.values())


def test_no_ecr_reading_means_no_fp_board():
    class Down(Ecr):
        def fetch(self, week, players):
            return {p.key: SignalValue(None, available=False, note="no ECR rank")
                    for p in players}
    assert fantasypros_weekly(ranked(Settings(), signals=[Down()]).values()) is None


def test_boone_board_keeps_each_reason_and_files_league_refusals_under_all():
    ranks = AnalystRanks("Justin Boone", "std",
                         unavailable={"league": "league scoring (std) has no published counterpart"})
    board = board_from_analyst_ranks(ranks)
    assert board.entries == {}
    assert board.unavailable == {ALL_POSITIONS: "league scoring (std) has no published counterpart"}
    assert board.label == "Justin Boone (Yahoo)"


def test_boone_source_never_raises_for_a_fetch_failure():
    class Broken:
        def fetch(self, *a):
            raise ConnectionError("yahoo down")
    board = BooneWeeklySource(Broken()).fetch(roster(), 9, "ppr")
    assert board.entries == {}
    assert "yahoo down" in board.unavailable[ALL_POSITIONS]


def test_scoring_mismatch_is_in_the_header():
    board = ExpertBoard("cbs_consensus", "CBS", "CBS staff consensus", scoring_label="PPR")
    assert board.header == "CBS Wk (PPR)"


def test_rank_formatting():
    assert format_rank(12.0) == "12" and format_rank(12.5) == "12.5"


# --- wiring: beside the blend, never in it --------------------------------

def test_boards_do_not_change_the_blend(tmp_path):
    plain = ranked(Settings(data_dir=tmp_path))
    with_boone = ranked(Settings(data_dir=tmp_path, analysts="boone"), fetcher=Boone())
    for pos in plain:
        a, b = plain[pos], with_boone[pos]
        assert [(s.player.key, s.final) for s in a.scores] == \
               [(s.player.key, s.final) for s in b.scores]
        assert a.close_call == b.close_call
        assert [n for n in a.notes] == [n for n in b.notes]


def test_boards_never_reach_the_results_log(tmp_path):
    settings = Settings(data_dir=tmp_path, analysts="boone")
    recs = report.rank_each_position(settings, roster(), 9, log=True,
                                     signals=[Ecr()], analyst_fetcher=Boone())
    for rec in recs.values():
        from ff_startsit.pipeline import log_deferred
        log_deferred(settings, rec, command="report")
    text = settings.results_log_path.read_text()
    assert text  # rows were written
    for row in map(json.loads, text.splitlines()):
        assert "expert" not in json.dumps(row).lower()
        assert "boone" not in json.dumps(row).lower()


def test_sections_only_show_boards_that_read_someone_there(tmp_path):
    recs = ranked(Settings(data_dir=tmp_path, analysts="boone"), fetcher=Boone())
    assert [b.short for b in section_boards(recs["WR"])] == ["FP", "Boone"]
    # Boone has no TE list yet: no all-dash column in that table.
    assert [b.short for b in section_boards(recs["TE"])] == ["FP"]


def test_markdown_section_shows_ranks_side_by_side(tmp_path):
    recs = ranked(Settings(data_dir=tmp_path, analysts="boone"), fetcher=Boone())
    md = render.render_markdown(recs["WR"], title="WR")
    header = next(line for line in md.splitlines() if line.startswith("| #"))
    assert header.startswith("| # | Player | Pos | Team | Score | FP Wk | Boone Wk | ECR |")
    alpha = next(line for line in md.splitlines() if "Alpha O'Neil" in line)
    cells = [c.strip() for c in alpha.strip("|").split("|")]
    assert cells[5:7] == ["1", "3"]          # FP has him 1st, Boone 3rd
    assert "FP Wk: FantasyPros consensus; Boone Wk: Justin Boone (Yahoo)" in md


def test_digest_carries_the_boards(tmp_path):
    settings = Settings(data_dir=tmp_path, analysts="boone")
    recs = ranked(settings, fetcher=Boone())
    digest = report.render_digest(9, "ppr", recs)
    assert "| FP Wk | Boone Wk |" in digest


def test_dashboard_shows_ranks_side_by_side(tmp_path):
    recs = ranked(Settings(data_dir=tmp_path, analysts="boone"), fetcher=Boone())
    page = html._position_table(recs["WR"])
    assert "<th class='num expert' title='FantasyPros consensus'>FP Wk</th>" in page
    assert "<th class='num expert' title='Justin Boone (Yahoo)'>Boone Wk</th>" in page
    assert "Ranks by position" in page


def test_missing_reading_renders_as_a_dash():
    board = ExpertBoard("x", "X", "X", entries={"a": BoardEntry(pos_rank=4)})
    assert board_cell(board, "a") == "4" and board_cell(board, "zzz") == "—"


def test_rank_command_gets_the_fp_board_without_boone(tmp_path, monkeypatch):
    """`rank` builds its rec outside rank_each_position; it must still get FP."""
    settings = Settings(data_dir=tmp_path)
    from ff_startsit.pipeline import recommend
    rec = recommend(settings, roster()[:3], 9, signals=[Ecr()], log=False,
                    exclude_unavailable=True)
    cli._annotate_analyst(settings, rec, roster()[:3], 9, "WR")
    assert [b.short for b in rec.expert_boards] == ["FP"]


def test_rank_command_adds_boone_when_enabled(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, analysts="boone")
    from ff_startsit.pipeline import recommend
    from ff_startsit.sources import analysts
    monkeypatch.setattr(analysts, "AnalystFetcher", lambda *a, **k: Boone())
    rec = recommend(settings, roster()[:3], 9, signals=[Ecr()], log=False,
                    exclude_unavailable=True)
    cli._annotate_analyst(settings, rec, roster()[:3], 9, "WR")
    assert [b.short for b in rec.expert_boards] == ["FP", "Boone"]


def test_boone_mark_sits_on_the_player_he_would_start(tmp_path):
    """The disagreement block became a mark in the table plus one plain note."""
    from ff_startsit.engine.analyst import AnalystConflict
    recs = ranked(Settings(data_dir=tmp_path, analysts="boone"), fetcher=Boone())
    wr = recs["WR"]
    wr.analyst_conflicts = [AnalystConflict("Justin Boone", "WR", "Alpha O'Neil", "Charlie",
                                            3, 1, False, True)]
    md = render.render_markdown(wr, title="WR")
    charlie = next(line for line in md.splitlines() if line.startswith("| ") and "Charlie" in line)
    alpha = next(line for line in md.splitlines() if line.startswith("| ") and "Alpha" in line)
    assert "| 1 ▲ |" in charlie and "▲" not in alpha
    assert "_▲ Justin Boone disagrees: he starts Charlie (his WR1) over Alpha O'Neil (his WR3)._" in md
    page = html._position_table(wr)
    assert "1 ▲" in page
