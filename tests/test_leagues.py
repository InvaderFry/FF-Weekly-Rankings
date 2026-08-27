"""Multi-league config: parsing FF_LEAGUES / leagues.json and load precedence."""

import json

from ff_startsit.config import load_settings, parse_leagues


def _clear(monkeypatch):
    for name in ("FF_LEAGUES", "FF_LEAGUES_FILE", "FF_DEFAULT_LEAGUE",
                 "ESPN_LEAGUE_ID", "ESPN_TEAM_ID", "SLEEPER_LEAGUE_ID"):
        monkeypatch.delenv(name, raising=False)


def test_parse_leagues_basic_and_scoring():
    leagues = parse_leagues("work=espn:111:3, dynasty=espn:222:7:half")
    assert [l.name for l in leagues] == ["work", "dynasty"]
    assert leagues[0].source == "espn" and leagues[0].league_id == "111"
    assert leagues[0].team_id == "3" and leagues[0].scoring is None
    assert leagues[1].scoring == "half"


def test_parse_leagues_skips_malformed_entries():
    # bad source, missing league id, and a no-'=' chunk are all dropped.
    leagues = parse_leagues("ok=espn:111:1, bad=notasource:1:2, noid=espn::4, junk")
    assert [l.name for l in leagues] == ["ok"]


def test_parse_leagues_unknown_scoring_falls_back_to_global():
    leagues = parse_leagues("x=espn:1:2:banana")
    assert leagues[0].scoring is None  # ignored, will use FF_SCORING


def test_parse_leagues_dedupes_by_name_case_insensitive():
    leagues = parse_leagues("Work=espn:1:1, work=espn:2:2")
    assert len(leagues) == 1 and leagues[0].league_id == "1"


def test_ff_leagues_env_wins(tmp_path, monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("FF_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FF_LEAGUES", "work=espn:111:3,dynasty=espn:222:7")
    s = load_settings()
    assert [l.name for l in s.leagues] == ["work", "dynasty"]
    assert s.default_league == "work"  # first league by default


def test_default_league_env_override(tmp_path, monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("FF_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FF_LEAGUES", "work=espn:111:3,dynasty=espn:222:7")
    monkeypatch.setenv("FF_DEFAULT_LEAGUE", "dynasty")
    assert load_settings().default_league == "dynasty"


def test_leagues_file_used_when_env_absent(tmp_path, monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("FF_DATA_DIR", str(tmp_path))
    lf = tmp_path / "leagues.json"
    lf.write_text(json.dumps({"leagues": [
        {"name": "keeper", "source": "espn", "id": "999", "team": "5", "scoring": "std"},
    ]}))
    monkeypatch.setenv("FF_LEAGUES_FILE", str(lf))
    s = load_settings()
    assert [l.name for l in s.leagues] == ["keeper"]
    assert s.leagues[0].scoring == "std"


def test_env_beats_file(tmp_path, monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("FF_DATA_DIR", str(tmp_path))
    lf = tmp_path / "leagues.json"
    lf.write_text(json.dumps({"leagues": [{"name": "fromfile", "source": "espn",
                                           "id": "1", "team": "1"}]}))
    monkeypatch.setenv("FF_LEAGUES_FILE", str(lf))
    monkeypatch.setenv("FF_LEAGUES", "fromenv=espn:2:2")
    assert [l.name for l in load_settings().leagues] == ["fromenv"]


def test_synthesized_default_from_flat_env(tmp_path, monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("FF_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ESPN_LEAGUE_ID", "555")
    monkeypatch.setenv("ESPN_TEAM_ID", "4")
    s = load_settings()
    assert len(s.leagues) == 1
    p = s.leagues[0]
    assert p.name == "default" and p.league_id == "555" and p.team_id == "4"


def test_corrupt_leagues_file_falls_back_to_default(tmp_path, monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("FF_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ESPN_LEAGUE_ID", "555")
    lf = tmp_path / "leagues.json"
    lf.write_text("{not valid json")
    monkeypatch.setenv("FF_LEAGUES_FILE", str(lf))
    s = load_settings()
    assert [l.name for l in s.leagues] == ["default"]


# --- the per-league Settings copy -----------------------------------------
def test_each_league_gets_its_own_label_even_at_identical_scoring(monkeypatch):
    """The trap this guards.

    Both `_league_context` and `_league_bundles` used to do `lsettings = settings`
    and only `replace(...)` when a league's scoring *differed* from the global. That
    is fine for scoring — same value, no copy needed — and wrong the moment a
    per-league field rides along: every same-scoring league would share one Settings
    object and log whichever label was written last.
    """
    from dataclasses import replace

    from ff_startsit.config import LeagueProfile, Settings

    settings = Settings(scoring="ppr")
    profiles = [LeagueProfile("work", "espn", "111", "3"),          # no scoring
                LeagueProfile("dynasty", "espn", "222", "7"),        # no scoring
                LeagueProfile("keeper", "espn", "333", "1", "half")] # differs

    copies = [replace(settings, scoring=p.scoring or settings.scoring,
                      league_label=p.name) for p in profiles]

    assert [c.league_label for c in copies] == ["work", "dynasty", "keeper"]
    assert [c.scoring for c in copies] == ["ppr", "ppr", "half"]
    # No copy is the original object, and none shares it with another.
    assert len({id(c) for c in copies}) == 3
    assert all(c is not settings for c in copies)


def test_league_context_labels_the_settings_it_returns(monkeypatch, tmp_path):
    _clear(monkeypatch)
    monkeypatch.setenv("FF_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FF_LEAGUES", "work=espn:111:3, dynasty=espn:222:7")

    from types import SimpleNamespace

    from ff_startsit.cli import _league_context

    settings = load_settings()
    for name in ("work", "dynasty"):
        args = SimpleNamespace(league_name=name)
        lsettings, profile = _league_context(args, settings)
        assert profile.name == name
        assert lsettings.league_label == name
    # The caller's Settings is never mutated in place.
    assert settings.league_label == ""
