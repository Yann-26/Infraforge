"""End-to-end CLI behaviour: the agent never breaks the pipeline."""

import json

import pytest

from agents import cli


@pytest.fixture
def no_secrets(monkeypatch):
    for name in (
        "ANTHROPIC_API_KEY",
        "AGENT_GITLAB_TOKEN",
        "CI_PROJECT_ID",
        "CI_MERGE_REQUEST_IID",
    ):
        monkeypatch.delenv(name, raising=False)


def test_investigate_without_api_key_exits_zero_and_reports_fallback(tmp_path, no_secrets, capsys):
    log = tmp_path / "job.log"
    log.write_text('fatal: [vm-1]: FAILED! => {"msg": "apt failed"}\n')
    report = tmp_path / "report.json"

    code = cli.main(["--output", str(report), "investigate", "--log-file", str(log), "--dry-run"])

    assert code == 0
    data = json.loads(report.read_text())
    assert data["outcome"] == "fallback"
    assert data["failure_reason"] == "not_configured"
    assert data["heuristic_category"] == "ansible_task"
    assert "AI failure investigator unavailable" in capsys.readouterr().out


def test_crash_inside_agent_still_exits_zero(monkeypatch, no_secrets):
    def boom(args, settings):
        raise RuntimeError("unexpected")

    monkeypatch.setitem(cli.COMMANDS, "investigate", boom)
    assert cli.main(["investigate", "--dry-run"]) == 0


def test_gitlab_unreachable_still_exits_zero(monkeypatch, no_secrets, capsys):
    monkeypatch.setenv("AGENT_GITLAB_TOKEN", "t")
    monkeypatch.setenv("CI_PROJECT_ID", "1")
    monkeypatch.setenv("CI_PIPELINE_ID", "5")
    monkeypatch.setenv("CI_MERGE_REQUEST_IID", "3")
    monkeypatch.setenv("CI_SERVER_URL", "http://127.0.0.1:9")  # nothing listens here
    monkeypatch.setenv("AGENT_GITLAB_TIMEOUT_SECONDS", "0.5")
    monkeypatch.setattr("agents.gitlab_client.time.sleep", lambda s: None)
    assert cli.main(["investigate"]) == 0
    assert "unavailable" in capsys.readouterr().out
