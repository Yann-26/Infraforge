"""Command line entry point: ``python -m agents <command>``.

Commands
  investigate  explain a failed CI job and comment on the MR
  review       review the MR diff and comment high-severity findings

Golden rule: the process ALWAYS exits 0. The original CI job has already failed (or
passed) on its own; an agent problem must never change the pipeline result.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from loguru import logger

from agents.comment import marker, render_investigation, render_review, should_post_review
from agents.config import AgentSettings
from agents.diffs import gitlab_diffs_to_unified
from agents.gitlab_client import GitLabClient, GitLabError
from agents.investigator import FailedJob, FailureContext, investigate
from agents.llm import AnthropicClient, LLMClient, LLMError, UsageTracker
from agents.reviewer import ReviewContext, review


def build_llm(settings: AgentSettings) -> tuple[LLMClient | None, str]:
    """Return ``(client, reason)``; client is None when the LLM cannot be used."""
    try:
        return AnthropicClient(settings), ""
    except LLMError as exc:
        logger.warning("LLM disabled: {}", exc)
        return None, exc.reason
    except Exception as exc:  # noqa: BLE001 - e.g. SDK import problems
        logger.warning("LLM disabled: {}", exc)
        return None, "client_error"


def build_gitlab(settings: AgentSettings) -> GitLabClient | None:
    if not (settings.gitlab_token and settings.project_id):
        logger.warning("AGENT_GITLAB_TOKEN or CI_PROJECT_ID not set: running without GitLab")
        return None
    return GitLabClient(
        settings.gitlab_url,
        settings.gitlab_token.get_secret_value(),
        settings.project_id,
        timeout=settings.gitlab_timeout_seconds,
    )


def post_comment(gitlab: GitLabClient | None, settings: AgentSettings, body: str, key_marker: str):
    """Post once per marker. Returns True if a comment was posted."""
    if gitlab is None or settings.mr_iid is None:
        logger.info("no MR to comment on; comment printed to the job log only")
        return False
    if gitlab.has_note_with_marker(settings.mr_iid, key_marker):
        logger.info("agent already commented for {}; skipping", key_marker)
        return False
    gitlab.create_note(settings.mr_iid, body)
    logger.info("comment posted on MR !{}", settings.mr_iid)
    return True


def _usage(settings: AgentSettings) -> UsageTracker:
    price_in, price_out = settings.price_per_mtok()
    return UsageTracker(price_in_per_mtok=price_in, price_out_per_mtok=price_out)


def _write_report(path: str | None, report: dict) -> None:
    logger.info("run report: {}", json.dumps(report))
    if path:
        Path(path).write_text(json.dumps(report, indent=2), encoding="utf-8")


# --- investigate --------------------------------------------------------------


def _failure_context_from_gitlab(gitlab: GitLabClient, settings: AgentSettings) -> FailureContext:
    jobs = [j for j in gitlab.failed_jobs(settings.pipeline_id) if not j["name"].startswith("ai_")]
    # Prefer jobs that actually failed the pipeline.
    blocking = [j for j in jobs if not j.get("allow_failure")] or jobs
    failed = [
        FailedJob(j["name"], j["stage"], gitlab.job_trace(j["id"]), j["id"], j.get("web_url"))
        for j in blocking[: settings.max_failed_jobs]
    ]
    diff, title = "", ""
    if settings.mr_iid:
        title = gitlab.merge_request(settings.mr_iid).get("title", "")
        diff = gitlab_diffs_to_unified(gitlab.merge_request_diffs(settings.mr_iid))
    return FailureContext(failed, diff, title, settings.pipeline_id or "unknown")


def _failure_context_from_files(args) -> FailureContext:
    log = Path(args.log_file).read_text(encoding="utf-8", errors="replace")
    diff = Path(args.diff_file).read_text(encoding="utf-8") if args.diff_file else ""
    return FailureContext([FailedJob(args.job_name, args.stage, log)], diff, args.mr_title)


def cmd_investigate(args, settings: AgentSettings) -> dict:
    started = time.monotonic()
    usage = _usage(settings)
    gitlab = None if args.log_file else build_gitlab(settings)
    llm, llm_reason = build_llm(settings)

    try:
        if args.log_file:
            ctx = _failure_context_from_files(args)
        elif gitlab and settings.pipeline_id:
            ctx = _failure_context_from_gitlab(gitlab, settings)
        else:
            ctx = FailureContext([], pipeline_id=settings.pipeline_id or "unknown")
    except (GitLabError, OSError) as exc:
        logger.error("could not collect failure context: {}", exc)
        ctx = FailureContext([], pipeline_id=settings.pipeline_id or "unknown")

    result = investigate(ctx, llm, settings, usage, llm_unavailable_reason=llm_reason)
    body = render_investigation(result, ctx, usage)
    print(body)

    posted = False
    if not args.dry_run:
        try:
            key = marker("investigator", f"pipeline-{ctx.pipeline_id}")
            posted = post_comment(gitlab, settings, body, key)
        except GitLabError as exc:
            logger.error("could not post MR comment: {}", exc)

    return {
        "agent": "investigator",
        "outcome": "ok" if result.ok else "fallback",
        "failure_reason": result.failure_reason,
        "category": result.diagnosis.category if result.ok else None,
        "heuristic_category": result.heuristic.category,
        "attempts": result.attempts,
        "redactions": result.redactions,
        "posted": posted,
        "latency_seconds": round(time.monotonic() - started, 3),
        "llm_latency_seconds": round(result.latency_seconds, 3),
        "prompt": result.prompt_id,
        "usage": usage.as_dict(),
    }


# --- review -------------------------------------------------------------------


def _review_context(args, gitlab: GitLabClient | None, settings: AgentSettings) -> ReviewContext:
    if args.diff_file:
        diff = Path(args.diff_file).read_text(encoding="utf-8")
        return ReviewContext(diff, args.mr_title, "", settings.commit_sha or "local")
    if gitlab is None or settings.mr_iid is None:
        return ReviewContext("")
    mr = gitlab.merge_request(settings.mr_iid)
    diff = gitlab_diffs_to_unified(gitlab.merge_request_diffs(settings.mr_iid))
    sha = settings.commit_sha or mr.get("sha") or "unknown"
    return ReviewContext(diff, mr.get("title", ""), mr.get("description") or "", sha)


def cmd_review(args, settings: AgentSettings) -> dict:
    started = time.monotonic()
    usage = _usage(settings)
    gitlab = None if args.diff_file else build_gitlab(settings)
    llm, llm_reason = build_llm(settings)
    try:
        ctx = _review_context(args, gitlab, settings)
    except (GitLabError, OSError) as exc:
        logger.error("could not fetch the MR diff: {}", exc)
        ctx = ReviewContext("")

    result = review(ctx, llm, settings, usage, llm_unavailable_reason=llm_reason)
    body = render_review(result, ctx, usage, settings.severity_threshold)
    print(body)

    posted = False
    if not args.dry_run and should_post_review(result):
        try:
            key = marker("reviewer", f"sha-{ctx.commit_sha}")
            posted = post_comment(gitlab, settings, body, key)
        except GitLabError as exc:
            logger.error("could not post MR comment: {}", exc)

    severities = [f.severity for f in result.findings]
    return {
        "agent": "reviewer",
        "outcome": "skipped" if result.skipped else ("ok" if result.llm_ok else "fallback"),
        "failure_reason": result.failure_reason,
        "findings": len(result.findings),
        "findings_by_severity": {s: severities.count(s) for s in set(severities)},
        "findings_by_source": {
            src: sum(f.source == src for f in result.findings) for src in ("llm", "opa")
        },
        "filtered": dict(result.filtered),
        "reviewed_files": result.reviewed_files,
        "attempts": result.attempts,
        "redactions": result.redactions,
        "posted": posted,
        "latency_seconds": round(time.monotonic() - started, 3),
        "llm_latency_seconds": round(result.latency_seconds, 3),
        "prompt": result.prompt_id,
        "usage": usage.as_dict(),
    }


# --- main ---------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m agents", description=__doc__)
    parser.add_argument("--output", help="write a JSON run report to this file")
    sub = parser.add_subparsers(dest="command", required=True)

    inv = sub.add_parser("investigate", help="explain a failed CI job")
    inv.add_argument("--log-file", help="local job log (skips GitLab)")
    inv.add_argument("--diff-file", help="local unified diff")
    inv.add_argument("--job-name", default="local-job")
    inv.add_argument("--stage", default="")
    inv.add_argument("--mr-title", default="")
    inv.add_argument("--dry-run", action="store_true", help="print the comment, do not post")

    rev = sub.add_parser("review", help="review the MR diff")
    rev.add_argument("--diff-file", help="local unified diff (skips GitLab)")
    rev.add_argument("--mr-title", default="")
    rev.add_argument("--dry-run", action="store_true", help="print the comment, do not post")
    return parser


COMMANDS = {"investigate": cmd_investigate, "review": cmd_review}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level} | agents | {message}")
    try:
        settings = AgentSettings()
        report = COMMANDS[args.command](args, settings)
        _write_report(args.output, report)
    except Exception:  # noqa: BLE001 - never fail the pipeline because of the agent
        logger.exception("agent crashed; ignoring so the pipeline result is unchanged")
    return 0
