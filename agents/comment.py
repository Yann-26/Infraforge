"""Markdown rendering for MR comments.

Every comment carries a hidden marker so the agents can (a) avoid posting the same
comment twice for one pipeline/commit and (b) find their own comments when counting
👍 / 👎 feedback.
"""

from agents.llm import UsageTracker

MARKER_PREFIX = "<!-- infraforge-agent:"
FOOTER = (
    "\n\n---\n"
    "<sub>🤖 AI suggestion from the InfraForge agents — review it before applying anything. "
    "The agent can only comment: it never pushes, merges or runs Terraform. "
    "React with 👍 if this helped or 👎 if it was wrong or noise.</sub>"
)

CATEGORY_LABELS = {
    "terraform_plan": "Terraform plan error",
    "terraform_validate": "Terraform validate/init error",
    "ansible_task": "Ansible task failure",
    "test_failure": "Test failure",
    "lint_policy": "Lint / policy / secret-scan failure",
    "dependency": "Dependency resolution",
    "flaky_infra": "Flaky / infrastructure issue",
    "unknown": "Unknown",
}

UNAVAILABLE_REASONS = {
    "not_configured": "the LLM API key is not configured",
    "timeout": "the LLM request timed out",
    "rate_limited": "the LLM API rate-limited the request",
    "api_error": "the LLM API returned an error",
    "connection": "the LLM API could not be reached",
    "refusal": "the model declined the request",
    "truncated": "the model's answer was cut off",
    "empty": "the model returned an empty answer",
    "invalid_output": "the model's answer failed validation twice",
    "unverified_evidence": "the model's evidence could not be matched to the log",
    "no_failed_jobs": "no failed job logs could be found",
    "client_error": "an internal error occurred",
    "gitlab_error": "GitLab data could not be fetched",
}


def marker(agent: str, key: str) -> str:
    return f"{MARKER_PREFIX}{agent} key={key} -->"


def usage_line(usage: UsageTracker, prompt_id: str, latency: float) -> str:
    u = usage.as_dict()
    return (
        f"<sub>prompt `{prompt_id}` · {u['calls']} call(s) · {u['input_tokens']} in / "
        f"{u['output_tokens']} out tokens · ~${u['cost_usd']:.4f} · {latency:.1f}s</sub>"
    )


def code_block(text: str, lang: str = "") -> str:
    fence = "````" if "```" in text else "```"
    return f"{fence}{lang}\n{text.rstrip()}\n{fence}"


def unavailable_reason(reason: str | None) -> str:
    return UNAVAILABLE_REASONS.get(reason or "", reason or "unknown error")


def _job_links(jobs) -> str:
    return ", ".join(f"[`{j.name}`]({j.web_url})" if j.web_url else f"`{j.name}`" for j in jobs)


def render_investigation(result, ctx, usage: UsageTracker) -> str:
    """Comment for the CI failure investigator (diagnosis or fallback)."""
    head = marker("investigator", f"pipeline-{ctx.pipeline_id}")
    stats = usage_line(usage, result.prompt_id, result.latency_seconds)
    if result.ok:
        body = _render_diagnosis(result, _job_links(ctx.jobs))
    else:
        body = _render_investigator_unavailable(result, _job_links(ctx.jobs))
    show_stats = result.ok or usage.calls
    return "\n".join([head, *body, *(["", stats] if show_stats else [])]) + FOOTER


def _render_investigator_unavailable(result, jobs: str) -> list[str]:
    guess = result.heuristic
    reason = unavailable_reason(result.failure_reason)
    lines = [
        "### ⚠️ AI failure investigator unavailable",
        f"The investigator could not analyse this failure: **{reason}**.",
        f"Failed job(s): {jobs or 'unknown'}. "
        + "The pipeline result is unaffected — check the job log as usual.",
    ]
    if guess.category != "unknown":
        where = f" — `{guess.file}`:{guess.line}" if guess.file else ""
        lines += ["", f"**Rule-based guess (no AI):** {CATEGORY_LABELS[guess.category]}{where}"]
        if guess.evidence:
            lines.append(code_block("\n".join(guess.evidence[:3])))
    return lines


def _render_diagnosis(result, jobs: str) -> list[str]:
    d = result.diagnosis
    location = "not identified"
    if d.file:
        location = f"`{d.file}`" + (f" line {d.line}" if d.line else "")
    lines = [
        f"### 🔎 CI failure: {d.summary}",
        "",
        "| | |",
        "|---|---|",
        f"| **Category** | {CATEGORY_LABELS.get(d.category, d.category)} |",
        f"| **Failed job(s)** | {jobs} |",
        f"| **Location** | {location} |",
        f"| **Confidence** | {d.confidence:.0%} |",
        "",
        d.explanation,
        "",
        "**Evidence from the job log**",
        code_block("\n".join(e.line for e in d.evidence)),
        *[f"- `{e.line[:80]}` — {e.why}" for e in d.evidence if e.why],
        "",
        "**Suggested fix**",
        "",
        d.suggested_fix,
    ]
    if d.suggested_patch:
        lines += ["", "<details><summary>Suggested patch</summary>", ""]
        lines += [code_block(d.suggested_patch, "diff"), "", "</details>"]
    notes = list(result.notes)
    if result.dropped_evidence:
        notes.append(f"{len(result.dropped_evidence)} cited line(s) not found in the log removed")
    if notes:
        lines += ["", "<sub>Checks: " + "; ".join(notes) + "</sub>"]
    return lines


SEVERITY_ICONS = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "⚪"}


def should_post_review(result) -> bool:
    """Low noise: stay silent when the review ran and found nothing worth a human's time."""
    if result.skipped:
        return False
    return bool(result.findings) or not result.llm_ok


def render_review(result, ctx, usage: UsageTracker, threshold: str) -> str:
    head = marker("reviewer", f"sha-{ctx.commit_sha}")
    lines = [head]
    if result.llm_ok:
        count = len(result.findings)
        title = f"{count} finding(s) at severity ≥ {threshold}" if count else "no findings"
        lines.append(f"### 🧐 AI review: {title}")
        if result.summary:
            lines += ["", result.summary]
    else:
        reason = unavailable_reason(result.failure_reason)
        lines += [
            "### ⚠️ AI reviewer unavailable",
            f"The AI review did not run: **{reason}**. "
            + "Only deterministic OPA/security checks are shown below.",
        ]

    for i, f in enumerate(result.findings, start=1):
        source = "OPA policy" if f.source == "opa" else f"AI, confidence {f.confidence:.0%}"
        icon = SEVERITY_ICONS[f.severity]
        lines += [
            "",
            f"**{i}. {icon} {f.severity.upper()} · `{f.file}`:{f.line} · `{f.rule}`** "
            + f"<sub>({source})</sub>",
            "",
            f.reason,
            "",
            "<details><summary>Suggested fix</summary>",
            "",
            f.suggested_fix if "```" in f.suggested_fix else code_block(f.suggested_fix),
            "",
            "</details>",
        ]
    if not result.findings and not result.llm_ok:
        lines += ["", "_No deterministic findings._"]

    checks = list(result.policy_notes)
    if result.filtered:
        dropped = ", ".join(
            f"{n} {k.replace('_', ' ')}" for k, n in sorted(result.filtered.items())
        )
        checks.append(f"filtered: {dropped}")
    if result.omitted_files:
        checks.append(f"{len(result.omitted_files)} file(s) not reviewed (size/type)")
    if checks:
        lines += ["", "<sub>Checks: " + "; ".join(checks) + "</sub>"]
    lines += ["", usage_line(usage, result.prompt_id, result.latency_seconds)]
    return "\n".join(lines) + FOOTER
