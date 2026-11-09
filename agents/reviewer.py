"""Automated MR reviewer: diff -> OPA findings + validated LLM findings -> filtered list.

Noise control (in order):
1. deterministic OPA/security findings are always kept,
2. LLM findings below the severity threshold or confidence floor are dropped,
3. LLM findings that do not point at a line added by the MR are dropped,
4. duplicates (same file/line as another finding) are dropped,
5. the list is capped at ``max_findings``.
Every drop is counted so the eval and metrics can show what was filtered.
"""

import json
from collections import Counter
from dataclasses import dataclass, field

from agents.config import SEVERITY_ORDER, AgentSettings
from agents.context import fence_untrusted, prepare_text
from agents.diffs import FileDiff, parse_unified_diff, render_for_prompt
from agents.llm import LLMClient, UsageTracker
from agents.policy_check import PolicyReport, policy_sources, run_policies
from agents.prompts import load_prompt
from agents.schemas import Review, ReviewFinding
from agents.structured import run_structured

PROMPT_VERSION = "v1"
LINE_TOLERANCE = 2  # snap a finding to an added line at most this far away


@dataclass
class ReviewContext:
    diff: str
    mr_title: str = ""
    mr_description: str = ""
    commit_sha: str = "local"


@dataclass
class ReviewResult:
    findings: list[ReviewFinding] = field(default_factory=list)
    summary: str = ""
    llm_ok: bool = False
    failure_reason: str | None = None
    error: str | None = None
    attempts: int = 0
    latency_seconds: float = 0.0
    prompt_id: str = f"reviewer.{PROMPT_VERSION}"
    filtered: Counter = field(default_factory=Counter)
    policy_notes: list[str] = field(default_factory=list)
    reviewed_files: list[str] = field(default_factory=list)
    omitted_files: list[str] = field(default_factory=list)
    redactions: int = 0

    @property
    def skipped(self) -> bool:
        return not self.reviewed_files


def _snap_to_added_line(finding: ReviewFinding, files: dict[str, FileDiff]) -> bool:
    f = files.get(finding.file)
    if f is None or not f.added:
        return False
    if finding.line in f.added:
        return True
    nearest = min(f.added, key=lambda n: abs(n - finding.line))
    if abs(nearest - finding.line) <= LINE_TOLERANCE:
        finding.line = nearest
        return True
    return False


def filter_findings(
    llm_findings: list[ReviewFinding],
    policy_findings: list[ReviewFinding],
    files: dict[str, FileDiff],
    settings: AgentSettings,
    filtered: Counter,
) -> list[ReviewFinding]:
    threshold = SEVERITY_ORDER[settings.severity_threshold]
    kept: list[ReviewFinding] = []

    def is_duplicate(f: ReviewFinding) -> bool:
        for k in kept:
            if k.file != f.file:
                continue
            if k.line == f.line:
                return True
            # an AI finding next to a deterministic one is almost always the same issue
            if k.source == "opa" and f.source == "llm" and abs(k.line - f.line) <= 1:
                return True
        return False

    def add(f: ReviewFinding) -> None:
        if is_duplicate(f):
            filtered["duplicate"] += 1
        else:
            kept.append(f)

    for f in policy_findings:
        if SEVERITY_ORDER[f.severity] < threshold:
            filtered["below_threshold"] += 1
            continue
        add(f)
    for f in llm_findings:
        if SEVERITY_ORDER[f.severity] < threshold:
            filtered["below_threshold"] += 1
        elif f.confidence < settings.min_confidence:
            filtered["low_confidence"] += 1
        elif not _snap_to_added_line(f, files):
            filtered["not_on_added_line"] += 1
        else:
            add(f)

    kept.sort(key=lambda f: (-SEVERITY_ORDER[f.severity], f.source != "opa", f.file, f.line))
    if len(kept) > settings.max_findings:
        filtered["over_limit"] += len(kept) - settings.max_findings
        kept = kept[: settings.max_findings]
    return kept


def _describe_policy_findings(report: PolicyReport) -> str:
    if not report.findings:
        return "(none)"
    return "\n".join(f"- {f.file}:{f.line} {f.rule} ({f.severity})" for f in report.findings)


def review(
    ctx: ReviewContext,
    llm: LLMClient | None,
    settings: AgentSettings,
    usage: UsageTracker,
    llm_unavailable_reason: str = "not_configured",
    policy_runner=run_policies,
) -> ReviewResult:
    result = ReviewResult()
    files = [f for f in parse_unified_diff(ctx.diff) if not f.deleted and not f.skipped]
    reviewable = [f for f in files if f.kind and f.added]
    result.reviewed_files = [f.path for f in reviewable]
    if not reviewable:
        result.failure_reason = "nothing_to_review"
        return result

    report = policy_runner(reviewable, settings)
    result.policy_notes = report.notes
    by_path = {f.path: f for f in reviewable}

    llm_findings: list[ReviewFinding] = []
    if llm is None:
        result.failure_reason = llm_unavailable_reason
    else:
        llm_findings = _ask_llm(ctx, llm, settings, usage, reviewable, report, result)

    result.findings = filter_findings(
        llm_findings, report.findings, by_path, settings, result.filtered
    )
    return result


def _ask_llm(ctx, llm, settings, usage, reviewable, report, result) -> list[ReviewFinding]:
    diff_text, result.omitted_files = render_for_prompt(
        reviewable, settings.max_diff_chars, only_reviewable=True
    )
    diff_text, n_diff = prepare_text(diff_text, settings)
    description, n_desc = prepare_text(ctx.mr_description[:2000], settings)
    result.redactions = n_diff + n_desc

    system, user = load_prompt("reviewer", PROMPT_VERSION).render(
        schema=json.dumps(Review.model_json_schema()),
        mr_title=ctx.mr_title or "(untitled)",
        mr_description=fence_untrusted(description, "mr_description") if description else "",
        policies=fence_untrusted(policy_sources(settings), "policies"),
        deterministic=_describe_policy_findings(report),
        diff=fence_untrusted(diff_text, "mr_diff"),
    )
    structured = run_structured(
        llm, system, user, Review, usage, validation_retries=settings.validation_retries
    )
    result.attempts, result.latency_seconds = structured.attempts, structured.latency_seconds
    if not structured.ok:
        result.failure_reason, result.error = structured.failure_reason, structured.error
        return []
    result.llm_ok = True
    result.summary = structured.value.summary
    findings = structured.value.findings
    for f in findings:
        f.source = "llm"  # the model cannot claim deterministic authority
    return findings
