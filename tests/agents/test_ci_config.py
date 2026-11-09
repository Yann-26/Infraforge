"""Guard rails on the CI include: agents must never block or change anything."""

from pathlib import Path

import yaml

CI = yaml.safe_load(Path("ci/agents.gitlab-ci.yml").read_text())
JOBS = {name: job for name, job in CI.items() if isinstance(job, dict) and "script" in job}
AI_JOBS = {name: job for name, job in JOBS.items() if name.startswith("ai_")}
FORBIDDEN = ("git push", "terraform apply", "/merge", "/approve", "auto-approve", "glab mr merge")


def test_expected_jobs_exist():
    assert {"ai_mr_review", "ai_investigate_failure", "ai_agents_feedback", "code_quality"} <= set(
        JOBS
    )


def test_every_job_is_non_blocking():
    for name, job in JOBS.items():
        base = CI.get(job.get("extends", ""), {})
        assert job.get("allow_failure", base.get("allow_failure")) is True, name


def test_ai_jobs_have_timeouts():
    base = CI[".ai_agent_base"]
    assert base["timeout"]
    assert all(CI[job["extends"]] is base for job in AI_JOBS.values())


def test_no_job_pushes_merges_or_applies():
    for name, job in JOBS.items():
        commands = " ".join(job.get("script", []) + job.get("before_script", []))
        for forbidden in FORBIDDEN:
            assert forbidden not in commands, f"{name} contains '{forbidden}'"


def test_investigator_only_runs_on_failure():
    rules = JOBS["ai_investigate_failure"]["rules"]
    assert all(rule.get("when") == "on_failure" for rule in rules)
    assert JOBS["ai_investigate_failure"]["stage"] == ".post"
