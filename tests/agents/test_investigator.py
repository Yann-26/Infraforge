"""Investigator: evidence verification, fallback paths and comment rendering."""

import json

from agents.comment import render_investigation
from agents.config import AgentSettings
from agents.context import clean_log, tail
from agents.diffs import parse_unified_diff
from agents.heuristics import classify
from agents.investigator import FailedJob, FailureContext, investigate
from agents.llm import LLMError, UsageTracker
from tests.agents.fakes import FakeLLM

LOG = """\x1b[0Ksection_start:1700000000:step_script\r\x1b[0K$ terraform validate
\x1b[31m╷\x1b[0m
│ Error: Unsupported argument
│
│   on main.tf line 12, in module "vm":
│   12:   cpu = 4
│
│ An argument named "cpu" is not expected here.
╵
ERROR: Job failed: exit code 1
"""

DIFF = """diff --git a/terraform/main.tf b/terraform/main.tf
--- a/terraform/main.tf
+++ b/terraform/main.tf
@@ -10,3 +10,3 @@ module "vm" {
   vm_name = var.vm_name
-  cpu_cores = 4
+  cpu = 4
   memory_mb = 8192
"""


def diagnosis(**overrides) -> str:
    data = {
        "category": "terraform_validate",
        "summary": "Unsupported argument `cpu` in module vm",
        "explanation": "The vm module declares `cpu_cores`; the MR renamed it to `cpu`.",
        "evidence": [{"line": "│ Error: Unsupported argument", "why": "terraform error"}],
        "file": "terraform/main.tf",
        "line": 12,
        "suggested_fix": "Revert the argument name to `cpu_cores`.",
        "suggested_patch": "-  cpu = 4\n+  cpu_cores = 4",
        "confidence": 0.95,
    }
    data.update(overrides)
    return json.dumps(data)


def ctx() -> FailureContext:
    return FailureContext([FailedJob("terraform_validate", "validate", LOG)], DIFF, "Rename", 99)


def settings() -> AgentSettings:
    return AgentSettings(_env_file=None)


def test_clean_log_strips_ansi_and_sections():
    cleaned = clean_log(LOG)
    assert "\x1b" not in cleaned and "section_start" not in cleaned
    assert "$ terraform validate" in cleaned


def test_tail_keeps_last_lines_and_marks_truncation():
    text = "\n".join(f"line {i}" for i in range(100))
    out = tail(text, max_lines=10, max_chars=10_000)
    assert out.splitlines()[-1] == "line 99" and "omitted" in out.splitlines()[0]


def test_successful_diagnosis():
    usage = UsageTracker()
    result = investigate(ctx(), FakeLLM(diagnosis()), settings(), usage)
    assert result.ok
    assert result.diagnosis.category == "terraform_validate"
    body = render_investigation(result, ctx(), usage)
    assert "Unsupported argument" in body
    assert "<!-- infraforge-agent:investigator key=pipeline-99 -->" in body
    assert "never pushes" in body
    assert "```diff" in body


def test_secrets_never_reach_the_model():
    secret_log = LOG + "export DB_PASSWORD=SuperSecret123\nconnecting to 10.100.20.7\n"
    job = FailedJob("t", "validate", secret_log)
    llm = FakeLLM(diagnosis())
    investigate(FailureContext([job], DIFF), llm, settings(), UsageTracker())
    sent = llm.calls[0][0] + "".join(m.content for m in llm.calls[0][1])
    assert "SuperSecret123" not in sent
    assert "10.100.20.7" not in sent


def test_hallucinated_evidence_is_dropped():
    reply = diagnosis(
        evidence=[
            {"line": "│ Error: Unsupported argument", "why": ""},
            {"line": "Error: provider proxmox crashed", "why": "made up"},
        ]
    )
    result = investigate(ctx(), FakeLLM(reply), settings(), UsageTracker())
    assert result.ok
    assert [e.line for e in result.diagnosis.evidence] == ["│ Error: Unsupported argument"]
    assert result.dropped_evidence == ["Error: provider proxmox crashed"]


def test_all_evidence_hallucinated_falls_back():
    reply = diagnosis(evidence=[{"line": "segmentation fault in kernel", "why": ""}])
    result = investigate(ctx(), FakeLLM(reply), settings(), UsageTracker())
    assert not result.ok and result.failure_reason == "unverified_evidence"


def test_unknown_file_location_is_removed():
    result = investigate(
        ctx(), FakeLLM(diagnosis(file="src/secret.py")), settings(), UsageTracker()
    )
    assert result.diagnosis.file is None and result.diagnosis.line is None
    assert any("not found" in n for n in result.notes)


def test_no_llm_falls_back_with_heuristic_guess():
    usage = UsageTracker()
    result = investigate(ctx(), None, settings(), usage, llm_unavailable_reason="not_configured")
    assert not result.ok
    body = render_investigation(result, ctx(), usage)
    assert "unavailable" in body
    assert "API key is not configured" in body
    assert "Terraform validate" in body  # rule-based guess
    assert "pipeline result is unaffected" in body


def test_llm_timeout_falls_back():
    llm = FakeLLM(LLMError("slow", reason="timeout"))
    result = investigate(ctx(), llm, settings(), UsageTracker())
    assert result.failure_reason == "timeout"
    assert "timed out" in render_investigation(result, ctx(), UsageTracker())


def test_invalid_output_twice_falls_back():
    result = investigate(ctx(), FakeLLM("garbage", "{}"), settings(), UsageTracker())
    assert result.failure_reason == "invalid_output" and result.attempts == 2


def test_no_failed_jobs():
    result = investigate(FailureContext([]), FakeLLM(diagnosis()), settings(), UsageTracker())
    assert result.failure_reason == "no_failed_jobs"


def test_heuristic_classifies_common_failures():
    assert classify(clean_log(LOG), "terraform_validate", "validate").category == (
        "terraform_validate"
    )
    assert classify("fatal: [vm-1]: FAILED! => {}", "ansible", "configure").category == (
        "ansible_task"
    )
    assert classify("FAILED tests/test_api.py::test_login - AssertionError").category == (
        "test_failure"
    )
    assert classify("curl: (6) Could not resolve host: pypi.org").category == "flaky_infra"
    assert classify("all good").category == "unknown"


def test_diff_parser_tracks_added_line_numbers():
    files = parse_unified_diff(DIFF)
    assert files[0].path == "terraform/main.tf"
    assert files[0].added == {11: "  cpu = 4"}
    assert files[0].kind == "terraform"
