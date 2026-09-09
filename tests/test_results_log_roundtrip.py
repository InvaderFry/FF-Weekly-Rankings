"""The writer and the reader of the decision log, joined end to end.

``results_log.log_recommendation`` writes the JSONL rows; ``calibrate.log_reader.
load_decisions`` parses them back. Nothing else in the suite connects the two —
the calibration tests build their rows by hand — so the two halves of one schema
could drift apart with every test still green.

That drift is unrecoverable rather than merely broken. The log is **append-only**
and never rewritten, and ``load_decisions`` skips a malformed row by design, so a
field the writer renames is a season of rows the reader silently discards; the
symptom surfaces weeks later as ``calibrate`` reporting thin data, with no way to
recover the decisions in between. Every assertion here is on a field one side
writes and the other reads.
"""

from ff_startsit.calibrate.log_reader import dedupe_decisions, load_decisions
from ff_startsit.config import Settings
from ff_startsit.models import Player, SignalValue
from ff_startsit.pipeline import recommend
from ff_startsit.sources.base import Signal


class _Ranks(Signal):
    """A signal serving canned raw values, shaped like the real ones."""

    higher_is_better = False

    def __init__(self, name, raws, higher_is_better=False):
        self.name = name
        self.raws = raws
        self.higher_is_better = higher_is_better

    def is_available(self):
        return True

    def fetch(self, week, players):
        return {p.key: SignalValue(self.raws[p.key]) if p.key in self.raws
                else SignalValue(None, available=False, note="no value")
                for p in players}


def _players():
    return [
        Player(key="100", name="Alpha", team="KC", position="RB"),
        Player(key="200", name="Bravo", team="SF", position="RB"),
        Player(key="300", name="Charlie", team="BUF", position="RB"),
    ]


def _signals():
    return [
        _Ranks("ecr", {"100": 4.0, "200": 11.0, "300": 26.0}),
        _Ranks("vegas", {"100": 27.5, "200": 21.0, "300": 19.5},
               higher_is_better=True),
    ]


def _settings(tmp_path, **kw):
    base = dict(
        weights={"ecr": 0.7, "vegas": 0.3},
        data_dir=tmp_path,
        scoring="half",
        league_label="dynasty",
    )
    base.update(kw)
    return Settings(**base)


def test_a_written_row_reads_back_as_the_decision_that_wrote_it(tmp_path):
    """The whole contract in one pass: everything the calibrator consumes has to
    survive the trip through JSONL."""
    settings = _settings(tmp_path)
    players = _players()

    rec = recommend(settings, players, week=6, signals=_signals(),
                    command="rank --pos RB")

    decisions = load_decisions(settings.results_log_path)
    assert len(decisions) == 1
    d = decisions[0]

    assert d.week == 6
    assert d.scoring == "half"                      # not defaulted to ppr
    assert d.league == "dynasty"                    # provenance survives
    assert d.weights == {"ecr": 0.7, "vegas": 0.3}  # backtest replays these
    assert d.close_call == rec.close_call

    # The candidates are the join key space and the learner's whole input.
    assert [c.key for c in d.candidates] == [s.player.key for s in rec.scores]
    assert {c.name for c in d.candidates} == {"Alpha", "Bravo", "Charlie"}
    assert all(c.position == "RB" for c in d.candidates)


def test_the_normalized_values_survive_the_trip_unchanged(tmp_path):
    """The learner re-blends these under trial weights and never re-fetches, so a
    row whose normalized scores don't round-trip is a row that fits garbage."""
    settings = _settings(tmp_path)
    rec = recommend(settings, _players(), week=6, signals=_signals())

    d = load_decisions(settings.results_log_path)[0]
    written = {s.player.key: s.normalized for s in rec.scores}

    for c in d.candidates:
        assert c.normalized == written[c.key]
        assert set(c.normalized) == {"ecr", "vegas"}


def test_the_season_the_reader_infers_matches_the_row_it_filters_on(tmp_path):
    """Season is not written — it is inferred from the row's own timestamp — so
    the round trip is the only place that check can be made."""
    settings = _settings(tmp_path)
    recommend(settings, _players(), week=6, signals=_signals())

    season = load_decisions(settings.results_log_path)[0].season
    assert season                                   # never empty
    # Filtering on what the reader itself inferred must return the row, and any
    # other season must not.
    assert len(load_decisions(settings.results_log_path, season=season)) == 1
    assert load_decisions(settings.results_log_path, season="1999") == []
    assert len(load_decisions(settings.results_log_path, week=6)) == 1
    assert load_decisions(settings.results_log_path, week=7) == []


def test_a_candidate_a_signal_could_not_cover_still_reads_back(tmp_path):
    """Graceful degradation has to survive the log too: an unavailable signal is
    dropped from that player's map, not written as a zero the learner would
    treat as a real reading."""
    settings = _settings(tmp_path)
    signals = [
        _Ranks("ecr", {"100": 4.0, "200": 11.0, "300": 26.0}),
        _Ranks("vegas", {"100": 27.5, "200": 21.0}, higher_is_better=True),
    ]
    recommend(settings, _players(), week=6, signals=signals)

    d = load_decisions(settings.results_log_path)[0]
    by_key = {c.key: c for c in d.candidates}
    assert set(by_key["300"].normalized) == {"ecr"}     # no vegas key at all
    assert set(by_key["100"].normalized) == {"ecr", "vegas"}


def test_two_runs_of_one_week_append_and_then_dedupe_to_one(tmp_path):
    """The append side of 'append-only' and the collapse side of dedupe are one
    contract: the Thursday and Sunday passes must both land, and must then count
    as a single piece of evidence."""
    settings = _settings(tmp_path)
    recommend(settings, _players(), week=6, signals=_signals())
    recommend(settings, _players(), week=6, signals=_signals())

    decisions = load_decisions(settings.results_log_path)
    assert len(decisions) == 2                      # both rows were appended
    assert len(dedupe_decisions(decisions)) == 1    # one question, asked twice


def test_a_run_that_was_never_logged_reads_back_as_nothing(tmp_path):
    """The suppressed-row paths are only honest if the reader agrees the row is
    absent — `log=False` must leave no file for `calibrate` to find."""
    settings = _settings(tmp_path)
    recommend(settings, _players(), week=6, signals=_signals(), log=False)

    assert load_decisions(settings.results_log_path) == []
