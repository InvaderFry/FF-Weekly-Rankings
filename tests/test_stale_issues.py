"""A new week's issue closes the earlier weeks' issues of the same kind.

Every workflow opened one issue per week and none ever closed one, so the
open list grew by three a week. Which issue is stale is decided in
``workflow.stale_issues`` from titles alone, and only titles it can read:
an issue it can't place in a season and week is left as it is.
"""
import json

from ff_startsit.workflow import stale_issues

from .conftest import REPO_ROOT, requires_repo

ISSUES = [
    {"number": 40, "title": "2026 Week 9 start/sit"},
    {"number": 41, "title": "2026 Week 9 waiver wire"},
    {"number": 44, "title": "2026 Week 10 start/sit"},   # the one just created
    {"number": 30, "title": "2026 Week 8 start/sit"},
    {"number": 12, "title": "2025 Week 17 start/sit"},
    {"number": 5, "title": "Week 3 start/sit"},           # legacy, no season
    {"number": 50, "title": "2026 Week 11 start/sit"},    # later week: not stale
    {"number": 7, "title": "Bug: start/sit table wraps"}, # a real issue
]


def test_earlier_weeks_of_the_same_kind_are_stale():
    assert stale_issues(ISSUES, "2026 Week 10 start/sit", "start/sit") == [40, 30, 12]


def test_other_kinds_legacy_titles_and_later_weeks_are_left_alone():
    stale = stale_issues(ISSUES, "2026 Week 10 start/sit", "start/sit")
    for keep in (41, 44, 5, 50, 7):
        assert keep not in stale


def test_a_title_that_is_not_a_season_week_closes_nothing():
    assert stale_issues(ISSUES, "start/sit", "start/sit") == []


def test_kind_must_match_exactly():
    issues = [{"number": 1, "title": "2026 Week 1 waiver wire"},
              {"number": 2, "title": "2026 Week 1 waiver wire extra"}]
    assert stale_issues(issues, "2026 Week 2 waiver wire", "waiver wire") == [1]


def test_cli_prints_one_number_per_line(tmp_path, monkeypatch, capsys):
    import sys

    from ff_startsit import workflow

    path = tmp_path / "open.json"
    path.write_text(json.dumps(ISSUES))
    monkeypatch.setattr(sys, "argv", ["workflow", "stale-issues", str(path),
                                      "2026 Week 10 start/sit", "start/sit"])
    workflow.main()
    assert capsys.readouterr().out.split() == ["40", "30", "12"]


@requires_repo
def test_each_workflow_closes_stale_issues_when_it_creates_one():
    root = REPO_ROOT / ".github" / "workflows"
    for name, kind in (("weekly-report.yml", "start/sit"), ("waivers.yml", "waiver wire"),
                       ("values.yml", "trade values")):
        text = (root / name).read_text()
        create = text.index("gh issue create --title \"$TITLE\"")
        tail = text[create:create + 1200]
        assert f'stale-issues open-issues.json "$TITLE" "{kind}"' in tail, name
        assert "gh issue close" in tail, name
