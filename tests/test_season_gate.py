"""Scheduled runs publish only in season.

No workflow checked the calendar, so every cron fired all year: a Thursday
start/sit in June, a Tuesday waiver pass in March. ``season.in_season_window``
is the rule (the rehearsal week, then kickoff through the Monday that ends
Week 18, on Central dates) and ``workflow.in_season`` the decision, so the
YAML only reads an output. A manual dispatch always runs.
"""
from datetime import date

import pytest

from ff_startsit import season
from ff_startsit.workflow import in_season


@pytest.mark.parametrize("day,expected", [
    (date(2026, 7, 21), False),     # July: nothing to rank
    (date(2026, 9, 1), False),      # eight days before kickoff
    (date(2026, 9, 2), True),       # rehearsal window opens (7 days out)
    (date(2026, 9, 8), True),       # the day before kickoff
    (date(2026, 9, 9), True),       # kickoff (Wednesday)
    (date(2026, 11, 15), True),     # mid-season
    (date(2027, 1, 11), True),      # Monday that ends Week 18
    (date(2027, 1, 12), False),     # the Tuesday after: no Week 19
    (date(2027, 2, 14), False),     # playoffs are not fantasy weeks
    (date(2025, 9, 4), True),       # 2025 kickoff (Thursday)
    (date(2026, 1, 5), True),       # Monday that ends 2025's Week 18
    (date(2026, 1, 6), False),
    (date(2026, 3, 15), False),     # spring
])
def test_in_season_window(day, expected):
    assert season.in_season_window(day) is expected


def test_season_ends_on_the_monday_closing_week_eighteen():
    assert season.season_end(2026) == date(2027, 1, 11)
    assert season.season_end(2026).weekday() == 0
    assert season.date_week(season.season_end(2026)) == 18


def test_a_manual_dispatch_always_runs(tmp_path):
    out = tmp_path / "out"
    assert in_season("workflow_dispatch", today=date(2026, 6, 1), output=out) is True
    assert out.read_text() == "run=true\n"


def test_a_schedule_outside_the_season_does_not_run(tmp_path, capsys):
    out = tmp_path / "out"
    assert in_season("schedule", today=date(2026, 6, 1), output=out) is False
    assert out.read_text() == "run=false\n"
    assert "out of season" in capsys.readouterr().out


def test_a_schedule_in_season_runs(tmp_path):
    out = tmp_path / "out"
    assert in_season("schedule", today=date(2026, 10, 1), output=out) is True
    assert out.read_text() == "run=true\n"


def test_every_scheduled_workflow_is_gated():
    """Each workflow with a cron must run its work behind the gate job."""
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[1] / ".github" / "workflows"
    for path in sorted(root.glob("*.yml")):
        doc = yaml.safe_load(path.read_text())
        triggers = doc.get(True) or doc.get("on") or {}
        if "schedule" not in triggers:
            continue
        jobs = doc["jobs"]
        assert "gate" in jobs, path.name
        assert "in-season" in str(jobs["gate"]["steps"]), path.name
        for name, job in jobs.items():
            if name == "gate":
                continue
            assert "gate" in job.get("needs", []), (path.name, name)
            assert "needs.gate.outputs.run == 'true'" in job.get("if", ""), (path.name, name)
