"""Reviewer: severity threshold, location validation, dedupe, fallback."""

import json

from agents.comment import render_review, should_post_review
from agents.config import AgentSettings
from agents.llm import LLMError, UsageTracker
from agents.policy_check import PolicyReport
from agents.reviewer import ReviewContext, review
from agents.schemas import ReviewFinding
from tests.agents.fakes import FakeLLM

DIFF = """diff --git a/orchestrator/app/api/requests.py b/orchestrator/app/api/requests.py
--- a/orchestrator/app/api/requests.py
+++ b/orchestrator/app/api/requests.py
@@ -40,3 +40,5 @@ router = APIRouter()
 @router.delete("/api/requests/{request_id}")
-async def delete_request(request_id: str, user=Depends(require_role("admin"))):
+async def delete_request(request_id: str):
+    db.query(f"DELETE FROM requests WHERE id = '{request_id}'")
     return {"deleted": request_id}
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-old
+new
"""


def finding(**kw) -> dict:
    base = {
        "file": "orchestrator/app/api/requests.py",
        "line": 42,
        "severity": "critical",
        "rule": "py-sql-injection",
        "reason": "request_id is interpolated into SQL.",
        "suggested_fix": "Use a bound parameter.",
        "confidence": 0.9,
    }
    base.update(kw)
    return base


def reply(*findings) -> str:
    return json.dumps({"summary": "Removes the admin check.", "findings": list(findings)})


def no_policies(files, settings):
    return PolicyReport(ran=True)


def settings(**kw) -> AgentSettings:
    return AgentSettings(_env_file=None, **kw)


def run(llm, policy_runner=no_policies, **kw):
    return review(
        ReviewContext(DIFF, "Allow deleting requests", commit_sha="abc123"),
        llm,
        settings(**kw),
        UsageTracker(),
        policy_runner=policy_runner,
    )


def test_high_findings_on_added_lines_are_kept():
    result = run(FakeLLM(reply(finding(), finding(line=41, rule="py-missing-authz"))))
    assert result.llm_ok
    assert [f.rule for f in result.findings] == ["py-missing-authz", "py-sql-injection"]
    assert result.reviewed_files == ["orchestrator/app/api/requests.py"]  # README not reviewed


def test_below_threshold_and_low_confidence_are_filtered():
    result = run(
        FakeLLM(
            reply(
                finding(severity="medium", rule="style"),
                finding(line=41, confidence=0.4, rule="maybe"),
            )
        )
    )
    assert result.findings == []
    assert result.filtered == {"below_threshold": 1, "low_confidence": 1}
    assert not should_post_review(result)  # nothing to say -> no comment (low noise)


def test_threshold_is_configurable():
    result = run(FakeLLM(reply(finding(severity="medium"))), severity_threshold="medium")
    assert len(result.findings) == 1


def test_findings_not_on_added_lines_are_dropped_or_snapped():
    result = run(
        FakeLLM(
            reply(
                finding(file="orchestrator/app/main.py", rule="other-file"),
                finding(line=200, rule="far-away"),
                finding(line=43, rule="snapped"),  # context line next to an added line
            )
        )
    )
    assert [(f.rule, f.line) for f in result.findings] == [("snapped", 42)]
    assert result.filtered["not_on_added_line"] == 2


def test_duplicates_and_cap():
    llm = FakeLLM(reply(*[finding(rule=f"r{i}") for i in range(3)]))
    result = run(llm)
    assert len(result.findings) == 1 and result.filtered["duplicate"] == 2

    many = "\n".join(f"+x{i} = {i}" for i in range(30))
    diff = f"diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1,30 @@\n{many}\n"
    llm = FakeLLM(reply(*[finding(file="a.py", line=i, rule=f"r{i}") for i in range(1, 30, 3)]))
    out = review(ReviewContext(diff), llm, settings(max_findings=3), UsageTracker(), no_policies)
    assert len(out.findings) == 3 and out.filtered["over_limit"] == 7


def test_model_cannot_claim_opa_source():
    result = run(FakeLLM(reply(finding(source="opa"))))
    assert result.findings[0].source == "llm"


def test_opa_findings_kept_and_llm_duplicate_dropped():
    def policies(files, s):
        return PolicyReport(
            ran=True,
            findings=[
                ReviewFinding(
                    file="orchestrator/app/api/requests.py",
                    line=42,
                    severity="high",
                    rule="PY_SQL",
                    reason="sql",
                    suggested_fix="bind",
                    confidence=1.0,
                    source="opa",
                )
            ],
        )

    result = run(FakeLLM(reply(finding())), policy_runner=policies)
    assert [(f.rule, f.source) for f in result.findings] == [("PY_SQL", "opa")]


def test_llm_unavailable_still_reports_opa_and_says_so():
    def policies(files, s):
        return PolicyReport(
            ran=True,
            findings=[
                ReviewFinding(
                    file="orchestrator/app/api/requests.py",
                    line=41,
                    severity="critical",
                    rule="OPA_X",
                    reason="r",
                    suggested_fix="f",
                    confidence=1.0,
                    source="opa",
                )
            ],
        )

    result = run(FakeLLM(LLMError("down", reason="timeout")), policy_runner=policies)
    assert not result.llm_ok and result.failure_reason == "timeout"
    assert len(result.findings) == 1
    assert should_post_review(result)
    body = render_review(result, ReviewContext(DIFF, commit_sha="abc"), UsageTracker(), "high")
    assert "AI reviewer unavailable" in body and "timed out" in body and "OPA_X" in body
    assert "<!-- infraforge-agent:reviewer key=sha-abc -->" in body


def test_invalid_output_falls_back():
    result = run(FakeLLM("nope", "still nope"))
    assert result.failure_reason == "invalid_output" and result.findings == []
    assert should_post_review(result)  # tells the MR the agent was unavailable


def test_non_reviewable_diff_is_skipped_silently():
    diff = "diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n+x\n"
    llm = FakeLLM(reply())
    result = review(ReviewContext(diff), llm, settings(), UsageTracker(), no_policies)
    assert result.skipped and not should_post_review(result)
    assert llm.calls == []  # no tokens spent


def test_rendered_comment_lists_findings():
    result = run(FakeLLM(reply(finding())))
    body = render_review(result, ReviewContext(DIFF, commit_sha="abc"), UsageTracker(), "high")
    assert "CRITICAL" in body and "py-sql-injection" in body and "confidence 90%" in body
