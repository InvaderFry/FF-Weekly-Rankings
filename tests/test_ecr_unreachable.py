"""An unreachable FantasyPros page must say so, not read as "unranked".

``ECRSignal._rows_uncached`` returned ``[]`` on a ``RequestException`` with no
warning and no status entry, so every player read "no ECR rank" — identical to
a player FantasyPros simply doesn't rank, and the one signal carrying 0.60 of
the blend went dark without a word.
"""
import requests

from ff_startsit.config import Settings
from ff_startsit.data_status import DataStatus, finish_status
from ff_startsit.models import Player
from ff_startsit.pipeline import recommend
from ff_startsit.report import LeagueBundle, Lineup, render_multi_digest
from ff_startsit.sources.ecr import ECRSignal


class _Resp:
    def __init__(self, status):
        self.status_code = status
        self.text = ""

    def raise_for_status(self):
        raise requests.HTTPError(f"{self.status_code} Server Error", response=self)


class _Session:
    def __init__(self, exc=None, status=None):
        self.exc, self.status = exc, status
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return _Resp(self.status)


def _players():
    return [Player("1", "Back One", "KC", "RB"), Player("2", "Back Two", "BUF", "RB"),
            Player("3", "Wideout", "MIA", "WR")]


def test_unreachable_page_gives_a_distinct_note_and_a_status_line(capsys):
    signal = ECRSignal(session=_Session(exc=requests.ConnectionError("reset")))
    values = signal.fetch(3, _players())

    assert {v.note for v in values.values()} == {"ECR page unreachable"}
    assert all(not v.available for v in values.values())
    lines = [line for _, line in signal.source_status]
    assert len(lines) == 1
    assert "RB, WR" in lines[0]
    assert "FantasyPros page unreachable (ConnectionError)" in lines[0]
    assert "requested Week 3" in lines[0]
    # Warned once for the run, not once per position.
    assert capsys.readouterr().err.count("FantasyPros rankings page unreachable") == 1


def test_an_http_error_names_the_status():
    signal = ECRSignal(session=_Session(status=502))
    signal.fetch(3, _players())
    (_, line), = signal.source_status
    assert "unreachable (HTTP 502)" in line


def test_an_unranked_player_still_reads_no_ecr_rank():
    """Only the unreachable page gets the new note; a reachable page that
    doesn't list a player keeps "no ECR rank"."""
    signal = ECRSignal(session=_Session(status=502))
    signal._rows_cache[("RB", 3)] = []          # reached, ranked nobody
    values = signal.fetch(3, _players())
    assert values["1"].note == "no ECR rank"
    assert values["3"].note == "ECR page unreachable"


def test_the_digest_shows_the_unreachable_page(tmp_path):
    signal = ECRSignal(session=_Session(status=502))
    settings = Settings(data_dir=tmp_path)
    players = _players()
    recs = {pos: recommend(settings, [p for p in players if p.position == pos], 3,
                           signals=[signal], log=False)
            for pos in ("RB", "WR")}

    bundle = LeagueBundle("league", "ppr", recs, Lineup([]))
    finish_status(DataStatus(2026, 3, ["league"]), [bundle])
    digest = render_multi_digest(3, [bundle])

    unreachable = [line for line in digest.splitlines()
                   if "FantasyPros page unreachable" in line]
    assert len(unreachable) == 1                 # one line, not one per position
    assert "ECR RB, WR: FantasyPros page unreachable (HTTP 502)" in unreachable[0]
    assert "ecr missing 3/3 player readings (ECR page unreachable)" in digest
