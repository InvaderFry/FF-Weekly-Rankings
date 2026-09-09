import os
from pathlib import Path
import subprocess

import pytest

from ff_startsit.workflow import chatops_reply, run_cli, verify_site


def test_unknown_command_stays_silent(monkeypatch):
    monkeypatch.setattr("ff_startsit.cli.main", lambda argv: pytest.fail("must not execute"))
    assert chatops_reply("/unknown") is None


@pytest.mark.parametrize("failure,expected", [(SystemExit(2), "status 2"),
                                               (RuntimeError("offline"), "offline")])
def test_command_errors_reply(monkeypatch, failure, expected):
    def fail(argv):
        raise failure
    monkeypatch.setattr("ff_startsit.cli.main", fail)
    assert expected in chatops_reply("/lineup")


def test_valid_command_reply(monkeypatch):
    monkeypatch.setattr("ff_startsit.cli.main", lambda argv: print("Your lineup"))
    assert "Your lineup" in chatops_reply("/lineup")


def test_missing_page_preserves_site_and_marks_stale(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary"))
    (tmp_path / "index.html").write_text("previous")
    assert not verify_site(tmp_path)
    assert (tmp_path / "index.html").read_text() == "previous"
    assert "complete=false" in (tmp_path / "output").read_text()
    assert "waivers.html" in (tmp_path / "summary").read_text()
    assert "stale" in (tmp_path / "summary").read_text()
    (tmp_path / "waivers.html").write_text("waivers")
    assert verify_site(tmp_path)


def test_run_diagnostics_redact_credentials(tmp_path, monkeypatch, capsys):
    import sys
    monkeypatch.setenv("ESPN_S2", "private-cookie")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary"))
    def run(argv):
        print("warning: forecast unavailable; private-cookie", file=sys.stderr)
        return 1
    monkeypatch.setattr("ff_startsit.cli.main", run)
    assert run_cli(["publish"]) == 1
    assert "private-cookie" not in capsys.readouterr().err
    assert "forecast unavailable; [redacted]" in (tmp_path / "summary").read_text()


def test_persist_decisions_in_temporary_remote(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/persist-decision-log.sh"
    remote, repo = tmp_path / "remote.git", tmp_path / "repo"
    def git(*args, cwd=None):
        return subprocess.check_output(["git", *args], cwd=cwd, stderr=subprocess.DEVNULL, text=True)
    git("init", "--bare", str(remote))
    git("init", str(repo))
    git("config", "user.email", "test@example.com", cwd=repo)
    git("config", "user.name", "Test", cwd=repo)
    (repo / ".gitignore").write_text("results_log.jsonl\n")
    git("add", ".", cwd=repo)
    git("commit", "-m", "initial", cwd=repo)
    git("remote", "add", "origin", str(remote), cwd=repo)
    cache = repo / ".cache"
    cache.mkdir()
    log = cache / "results_log.jsonl"
    env = dict(os.environ, LOG_BRANCH="calibration-data", LOG_FILE="results_log.jsonl")
    for content in ['{"decision":1}\n', '{"decision":1}\n{"decision":2}\n']:
        log.write_text(content)
        subprocess.run(["bash", str(script)], cwd=repo, env=env, check=True, capture_output=True)
        assert git("show", "calibration-data:results_log.jsonl", cwd=remote) == content
    assert len(git("worktree", "list", "--porcelain", cwd=repo).split("worktree ")) == 2


def test_issue_identity_requires_season():
    from ff_startsit.workflow import issue_title
    assert issue_title("Season 2026 · Week 1", "start/sit") == "2026 Week 1 start/sit"
    assert issue_title("Season 2027 · Week 1", "start/sit") != "2026 Week 1 start/sit"
    with pytest.raises(ValueError):
        issue_title("Week 1", "start/sit")


@pytest.mark.parametrize("command", ["publish", "waivers"])
def test_artifact_mode_builds_outputs_without_notification_or_log(monkeypatch, command):
    monkeypatch.setenv("ARTIFACT_ONLY", "true")
    calls = []
    monkeypatch.setattr("ff_startsit.cli.main", lambda argv: calls.append(argv))
    args = [command, "--all-leagues", "--week", "1", "--discord", "--log",
            "--report", "report.md", "--dashboard", "site/index.html"]
    assert run_cli(args) == 0
    assert calls == [[command, "--all-leagues", "--week", "1", "--report", "report.md",
                      "--dashboard", "site/index.html"]]


def test_production_mode_retains_requested_effects(monkeypatch):
    monkeypatch.delenv("ARTIFACT_ONLY", raising=False)
    calls = []
    monkeypatch.setattr("ff_startsit.cli.main", lambda argv: calls.append(argv))
    assert run_cli(["publish", "--discord", "--log"]) == 0
    assert calls == [["publish", "--discord", "--log"]]
