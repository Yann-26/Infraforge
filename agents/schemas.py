"""Pydantic models for everything the model is allowed to return.

The model is asked for JSON matching one of these schemas. Anything that does not
validate is rejected (and retried once) — we never act on free-form text.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FailureCategory = Literal[
    "terraform_plan",
    "terraform_validate",
    "ansible_task",
    "test_failure",
    "lint_policy",
    "dependency",
    "flaky_infra",
    "unknown",
]

FindingSeverity = Literal["low", "medium", "high", "critical"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LogEvidence(_Strict):
    """A log line the diagnosis relies on. Must be copied verbatim from the log."""

    line: str = Field(min_length=1, max_length=500)
    why: str = Field(default="", max_length=300)


class Diagnosis(_Strict):
    """Output of the CI failure investigator."""

    category: FailureCategory
    summary: str = Field(min_length=1, max_length=300)
    explanation: str = Field(min_length=1, max_length=3000)
    evidence: list[LogEvidence] = Field(min_length=1, max_length=8)
    file: str | None = Field(default=None, max_length=300)
    line: int | None = Field(default=None, ge=1)
    suggested_fix: str = Field(min_length=1, max_length=2000)
    suggested_patch: str | None = Field(default=None, max_length=6000)
    confidence: float = Field(ge=0.0, le=1.0)


class ReviewFinding(_Strict):
    """One reviewer finding. Must point at a line added in the MR diff."""

    file: str = Field(min_length=1, max_length=300)
    line: int = Field(ge=1)
    severity: FindingSeverity
    rule: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=1500)
    suggested_fix: str = Field(min_length=1, max_length=3000)
    confidence: float = Field(ge=0.0, le=1.0)
    source: Literal["llm", "opa"] = "llm"


class Review(_Strict):
    """Output of the MR reviewer."""

    summary: str = Field(min_length=1, max_length=1000)
    findings: list[ReviewFinding] = Field(default_factory=list, max_length=30)
