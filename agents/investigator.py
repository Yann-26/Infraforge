"""CI failure investigator: failed job log + MR diff -> validated Diagnosis.

Pure logic, no I/O: the CLI fetches data from GitLab and posts the comment, the
evaluation script feeds fixtures. Every failure path returns a result with
``diagnosis=None`` and a ``failure_reason``; nothing here raises to the caller.
"""

import json
import re
from dataclasses import dataclass, field

from agents.config import AgentSettings
from agents.context import fence_untrusted, prepare_log, prepare_text
from agents.diffs import parse_unified_diff, render_for_prompt
from agents.heuristics import HeuristicGuess, classify
from agents.llm import LLMClient, UsageTracker
from agents.prompts import load_prompt
from agents.schemas import Diagnosis
from agents.structured import run_structured

PROMPT_VERSION = "v1"


@dataclass
class FailedJob:
    name: str
    stage: str
    log: str  # raw log; prepared (tail + redaction) inside investigate()
    id: int | None = None
    web_url: str | None = None


@dataclass
class FailureContext:
    jobs: list[FailedJob]
    diff: str = ""
    mr_title: str = ""
    pipeline_id: int | str = "local"


@dataclass
class InvestigationResult:
    heuristic: HeuristicGuess
    diagnosis: Diagnosis | None = None
    failure_reason: str | None = None
    error: str | None = None
    attempts: int = 0
    latency_seconds: float = 0.0
    prompt_id: str = f"investigator.{PROMPT_VERSION}"
    redactions: int = 0
    dropped_evidence: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.diagnosis is not None


_BOX = re.compile(r"[│╷╵┃|]")


def _norm(text: str) -> str:
    return " ".join(_BOX.sub(" ", text).split()).lower()


def verify_evidence(diagnosis: Diagnosis, logs: str) -> list[str]:
    """Drop evidence lines that do not appear in the log. Returns the dropped lines."""
    haystack = _norm(logs)
    kept, dropped = [], []
    for item in diagnosis.evidence:
        needle = _norm(item.line)
        if len(needle) >= 8 and needle in haystack:
            kept.append(item)
        else:
            dropped.append(item.line)
    diagnosis.evidence = kept
    return dropped


def verify_location(diagnosis: Diagnosis, logs: str, diff: str) -> str | None:
    """Clear file/line if the file is not mentioned anywhere we showed the model."""
    if diagnosis.file and diagnosis.file not in logs and diagnosis.file not in diff:
        dropped = diagnosis.file
        diagnosis.file, diagnosis.line = None, None
        return f"location `{dropped}` was not found in the log or diff and was removed"
    return None


def _order_jobs(jobs: list[FailedJob]) -> list[FailedJob]:
    order = ["validate", "test", "security", "quality", "plan", "review", "apply", "configure"]
    return sorted(jobs, key=lambda j: order.index(j.stage) if j.stage in order else len(order))


def investigate(
    ctx: FailureContext,
    llm: LLMClient | None,
    settings: AgentSettings,
    usage: UsageTracker,
    llm_unavailable_reason: str = "not_configured",
) -> InvestigationResult:
    jobs = _order_jobs(ctx.jobs)[: settings.max_failed_jobs]
    prepared: list[tuple[FailedJob, str]] = []
    redactions = 0
    for job in jobs:
        log, n = prepare_log(job.log, settings)
        redactions += n
        prepared.append((job, log))

    first_job, first_log = prepared[0] if prepared else (FailedJob("unknown", "", ""), "")
    result = InvestigationResult(heuristic=classify(first_log, first_job.name, first_job.stage))

    files = parse_unified_diff(ctx.diff)
    diff_text, omitted = render_for_prompt(files, settings.max_diff_chars)
    diff_text, n = prepare_text(diff_text, settings)
    result.redactions = redactions + n
    if omitted:
        result.notes.append(f"{len(omitted)} file(s) left out of the diff sent to the model")

    if not prepared:
        result.failure_reason, result.error = "no_failed_jobs", "no failed job logs found"
        return result
    if llm is None:
        result.failure_reason = llm_unavailable_reason
        return result

    jobs_text = "\n\n".join(
        f"### Job `{job.name}` (stage: {job.stage})\n{fence_untrusted(log, 'job_log')}"
        for job, log in prepared
    )
    prompt = load_prompt("investigator", PROMPT_VERSION)
    system, user = prompt.render(
        schema=json.dumps(Diagnosis.model_json_schema()),
        pipeline_id=str(ctx.pipeline_id),
        mr_title=ctx.mr_title or "(unknown)",
        jobs=jobs_text,
        diff=fence_untrusted(diff_text or "(no diff available)", "mr_diff"),
    )
    structured = run_structured(
        llm, system, user, Diagnosis, usage, validation_retries=settings.validation_retries
    )
    result.attempts, result.latency_seconds = structured.attempts, structured.latency_seconds
    if not structured.ok:
        result.failure_reason, result.error = structured.failure_reason, structured.error
        return result

    diagnosis = structured.value
    all_logs = "\n".join(log for _, log in prepared)
    result.dropped_evidence = verify_evidence(diagnosis, all_logs)
    if not diagnosis.evidence:
        # Nothing the model cited exists in the log: do not present it as a finding.
        result.failure_reason = "unverified_evidence"
        result.error = "none of the cited log lines were found in the job log"
        return result
    if note := verify_location(diagnosis, all_logs, diff_text):
        result.notes.append(note)
    result.diagnosis = diagnosis
    return result
