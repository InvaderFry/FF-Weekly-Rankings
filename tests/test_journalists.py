from pathlib import Path

from ff_startsit.config import Settings
from ff_startsit.data.matching import ExternalRow
from ff_startsit.models import Player
from ff_startsit.pipeline import build_signals
from ff_startsit.sources.ecr import ECRSignal
from ff_startsit.sources.journalists import (
    JournalistsSignal,
    _average_rows,
    parse_cbs_table,
    parse_journalists_csv,
)

CSV = """name,team,position,richard,eisenberg,boone
Alpha Back,KC,RB,1,3,2
Bravo Back,CHI,RB,5,5,
Charlie Back,Buffalo Bills,RB,,,9
Niners,SF,DST,2,2,1
"""


def test_parse_journalists_csv_wide_format():
    data = parse_journalists_csv(CSV)
    assert set(data) == {"richard", "eisenberg", "boone"}
    # Blank cells are skipped: boone has Alpha, Charlie, Niners (not Bravo).
    boone_names = {r.name for r in data["boone"]}
    assert boone_names == {"Alpha Back", "Charlie Back", "Niners"}
    # Team normalization + DST -> DEF.
    niners = next(r for r in data["richard"] if r.name == "Niners")
    assert niners.team == "SF" and niners.position == "DEF"
    charlie = next(r for r in data["boone"] if r.name == "Charlie Back")
    assert charlie.team == "BUF"  # "Buffalo Bills" normalized


def test_signal_equal_averages_available_analysts(tmp_path):
    path = tmp_path / "j.csv"
    path.write_text(CSV)
    settings = Settings(journalists_file=path, cbs_rankings_url="", boone_article_url="")
    sig = JournalistsSignal(settings)

    players = [
        Player("a", "Alpha Back", "KC", "RB"),
        Player("b", "Bravo Back", "CHI", "RB"),
        Player("c", "Charlie Back", "BUF", "RB"),
        Player("d", "Delta Back", "MIA", "RB"),   # not in CSV
    ]
    out = sig.fetch(3, players)
    assert out["a"].raw == 2.0          # mean(1,3,2)
    assert out["b"].raw == 5.0          # mean(5,5), boone blank
    assert out["c"].raw == 9.0          # only boone
    assert not out["d"].available       # no analyst -> unavailable -> blend uses Vegas


def test_signal_all_missing_is_unavailable(tmp_path):
    settings = Settings(journalists_file=tmp_path / "missing.csv")
    sig = JournalistsSignal(settings)
    out = sig.fetch(3, [Player("x", "Nobody", "KC", "RB")])
    assert not out["x"].available


def test_average_rows_for_cbs_half_ppr():
    ppr = [ExternalRow("Alpha Back", "KC", "RB", 2.0),
           ExternalRow("Bravo Back", "CHI", "RB", 6.0)]
    std = [ExternalRow("Alpha Back", "KC", "RB", 4.0),
           ExternalRow("Bravo Back", "CHI", "RB", 10.0)]
    avg = {(r.name, r.value) for r in _average_rows([ppr, std])}
    assert ("Alpha Back", 3.0) in avg     # mean(2,4)
    assert ("Bravo Back", 8.0) in avg     # mean(6,10)


def test_parse_cbs_table_scrapes_rows():
    html = """
    <table>
      <tr><th>Rank</th><th>Player</th><th>Team</th><th>Pos</th></tr>
      <tr><td>1</td><td><a href="#">Christian McCaffrey</a></td><td>SF</td><td>RB</td></tr>
      <tr><td>2</td><td><a href="#">Bijan Robinson</a></td><td>ATL</td><td>RB</td></tr>
    </table>
    """
    rows = parse_cbs_table(html)
    assert [r.name for r in rows] == ["Christian McCaffrey", "Bijan Robinson"]
    assert rows[0].value == 1.0 and rows[0].team == "SF" and rows[0].position == "RB"


def test_pipeline_backbone_selection():
    assert isinstance(build_signals(Settings(ranking_source="journalists"))[0], JournalistsSignal)
    assert isinstance(build_signals(Settings(ranking_source="fantasypros"))[0], ECRSignal)
