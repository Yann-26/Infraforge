"""Evaluate the agents on tests/agent_eval/cases.

Usage::

    python -m tests.agent_eval.run_eval --mode heuristic     # offline, free
    python -m tests.agent_eval.run_eval --mode llm           # Claude, needs ANTHROPIC_API_KEY
    python -m tests.agent_eval.run_eval --mode llm --cases inv-0 --report report.md

Modes
  heuristic  no LLM. Investigator = rule-based classifier (agents/heuristics.py).
             Reviewer = deterministic OPA/security rules only. This is the baseline the
             LLM has to beat, and it is what the fallback comment shows.
  llm        the real agents (same code path as CI, minus GitLab I/O).

Metrics
  investigator  category accuracy, file accuracy, fix-keyword hit rate, fallback rate
  reviewer      recall (expected issues found), precision, false-positive rate (noise =
                reported findings matching no expected issue), noisy clean diffs
  both          tokens, cost per run, latency
"""

import argparse
import json
import statistics
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from agents.config import AgentSettings
from agents.investigator import FailedJob, FailureContext, investigate
from agents.llm import AnthropicClient, UsageTracker
from agents.policy_check import OpaRunner, run_policies
from agents.reviewer import ReviewContext, review
from tests.agent_eval.rego_replica import replica_policy_runner

CASES_DIR = Path(__file__).parent / "cases"
RESULTS_DIR = Path(__file__).parent / "results"
LINE_TOLERANCE = 1


@dataclass
class Case:
    id: str
    path: Path
    meta: dict

    @property
    def agent(self) -> str:
        return self.meta["agent"]

    def read(self, name: str) -> str:
        p = self.path / name
        return p.read_text(encoding="utf-8") if p.exists() else ""


def load_cases(pattern: str | None = None) -> list[Case]:
    cases = []
    for d in sorted(CASES_DIR.iterdir()):
        if d.is_dir() and (pattern is None or pattern in d.name):
            cases.append(Case(d.name, d, yaml.safe_load((d / "case.yml").read_text())))
    return cases


# --- scoring --------------------------------------------------------------------


def same_file(expected: str | None, predicted: str | None) -> bool:
    if not expected or not predicted:
        return False
    e, p = expected.strip("/"), predicted.strip("/")
    return e == p or e.endswith("/" + p) or p.endswith("/" + e)


def keyword_hit(keywords: list[str], text: str) -> bool | None:
    if not keywords:
        return None
    lowered = text.lower()
    return any(k.lower() in lowered for k in keywords)


def match_findings(expected: list[dict], findings: list[dict]) -> tuple[int, int, int, list]:
    """Return ``(found_expected, true_positive_findings, false_positives, per_finding)``."""
    found = [False] * len(expected)
    per_finding, tp, fp = [], 0, 0
    for f in findings:
        hit = None
        for i, e in enumerate(expected):
            lines = e["lines"]
            near = min(lines) - LINE_TOLERANCE <= f["line"] <= max(lines) + LINE_TOLERANCE
            if same_file(e["file"], f["file"]) and near:
                hit = i
                break
        if hit is None:
            fp += 1
        else:
            tp += 1
            found[hit] = True
        per_finding.append({**f, "matched_expected": hit})
    return sum(found), tp, fp, per_finding


# --- runners --------------------------------------------------------------------


def run_investigator(case: Case, llm, settings: AgentSettings) -> dict:
    meta, expected = case.meta, case.meta["expected"]
    job = FailedJob(meta.get("job_name", "job"), meta.get("stage", ""), case.read("job.log"))
    ctx = FailureContext([job], case.read("mr.diff"), meta.get("description", ""), case.id)
    usage = settings_usage(settings)
    started = time.monotonic()
    result = investigate(ctx, llm, settings, usage, llm_unavailable_reason="heuristic_mode")
    latency = time.monotonic() - started

    if llm is None:
        category, file, text, fallback = result.heuristic.category, result.heuristic.file, "", False
    elif result.ok:
        d = result.diagnosis
        category, file, fallback = d.category, d.file, False
        text = " ".join([d.summary, d.explanation, d.suggested_fix, d.suggested_patch or ""])
    else:
        category, file, text, fallback = None, None, "", True

    return {
        "id": case.id,
        "agent": "investigator",
        "expected_category": expected["category"],
        "predicted_category": category,
        "correct": category == expected["category"],
        "file_expected": expected.get("file"),
        "file_predicted": file,
        "file_correct": same_file(expected.get("file"), file) if expected.get("file") else None,
        "keyword_hit": keyword_hit(expected.get("keywords", []), text) if llm else None,
        "fallback": fallback,
        "failure_reason": result.failure_reason if llm else None,
        "latency_seconds": round(latency, 2),
        "usage": usage.as_dict(),
    }


def run_reviewer(case: Case, llm, settings: AgentSettings, policy_runner) -> dict:
    expected = case.meta["expected"]["findings"]
    ctx = ReviewContext(case.read("mr.diff"), case.meta.get("mr_title", ""), "", case.id)
    usage = settings_usage(settings)
    started = time.monotonic()
    result = review(
        ctx, llm, settings, usage, llm_unavailable_reason="heuristic_mode",
        policy_runner=policy_runner,
    )  # fmt: skip
    latency = time.monotonic() - started
    findings = [
        {"file": f.file, "line": f.line, "rule": f.rule, "severity": f.severity,
         "source": f.source}
        for f in result.findings
    ]  # fmt: skip
    found, tp, fp, per_finding = match_findings(expected, findings)
    return {
        "id": case.id,
        "agent": "reviewer",
        "expected": len(expected),
        "found": found,
        "reported": len(findings),
        "true_positives": tp,
        "false_positives": fp,
        "clean_case": not expected,
        "fallback": bool(llm) and not result.llm_ok,
        "failure_reason": result.failure_reason if llm else None,
        "filtered": dict(result.filtered),
        "findings": per_finding,
        "latency_seconds": round(latency, 2),
        "usage": usage.as_dict(),
    }


def settings_usage(settings: AgentSettings) -> UsageTracker:
    price_in, price_out = settings.price_per_mtok()
    return UsageTracker(price_in_per_mtok=price_in, price_out_per_mtok=price_out)


# --- summary --------------------------------------------------------------------


def _ratio(num: float, den: float) -> float | None:
    return round(num / den, 3) if den else None


def summarize(results: list[dict]) -> dict:
    inv = [r for r in results if r["agent"] == "investigator"]
    rev = [r for r in results if r["agent"] == "reviewer"]
    file_scored = [r for r in inv if r["file_correct"] is not None]
    kw_scored = [r for r in inv if r["keyword_hit"] is not None]
    reported = sum(r["reported"] for r in rev)
    cost = sum(r["usage"]["cost_usd"] for r in results)
    latencies = [r["latency_seconds"] for r in results]
    return {
        "investigator": {
            "cases": len(inv),
            "category_accuracy": _ratio(sum(r["correct"] for r in inv), len(inv)),
            "file_accuracy": _ratio(sum(r["file_correct"] for r in file_scored), len(file_scored)),
            "fix_keyword_hit_rate": _ratio(
                sum(r["keyword_hit"] for r in kw_scored), len(kw_scored)
            ),
            "fallback_rate": _ratio(sum(r["fallback"] for r in inv), len(inv)),
        },
        "reviewer": {
            "cases": len(rev),
            "recall": _ratio(sum(r["found"] for r in rev), sum(r["expected"] for r in rev)),
            "precision": _ratio(sum(r["true_positives"] for r in rev), reported),
            "false_positive_rate": _ratio(sum(r["false_positives"] for r in rev), reported),
            "noisy_clean_cases": sum(r["clean_case"] and r["reported"] > 0 for r in rev),
            "clean_cases": sum(r["clean_case"] for r in rev),
            "fallback_rate": _ratio(sum(r["fallback"] for r in rev), len(rev)),
        },
        "cost": {
            "total_usd": round(cost, 4),
            "per_run_usd": round(cost / len(results), 4) if results else 0,
            "input_tokens": sum(r["usage"]["input_tokens"] for r in results),
            "output_tokens": sum(r["usage"]["output_tokens"] for r in results),
            "llm_calls": sum(r["usage"]["calls"] for r in results),
        },
        "latency_seconds": {
            "mean": round(statistics.mean(latencies), 2) if latencies else 0,
            "max": max(latencies, default=0),
        },
    }


def _fmt(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float) and value <= 1:
        return f"{value:.0%}"
    return str(value)


def render_markdown(mode: str, model: str, summary: dict, results: list[dict]) -> str:
    inv, rev, cost = summary["investigator"], summary["reviewer"], summary["cost"]
    lines = [
        f"# Agent evaluation — mode `{mode}`" + (f", model `{model}`" if mode == "llm" else ""),
        "",
        f"_Generated {datetime.now(UTC):%Y-%m-%d %H:%M UTC} by tests/agent_eval/run_eval.py._",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Investigator cases | {inv['cases']} |",
        f"| Investigator category accuracy | {_fmt(inv['category_accuracy'])} |",
        f"| Investigator file accuracy | {_fmt(inv['file_accuracy'])} |",
        f"| Investigator fix mentions expected keyword | {_fmt(inv['fix_keyword_hit_rate'])} |",
        f"| Investigator fallback rate | {_fmt(inv['fallback_rate'])} |",
        f"| Reviewer cases | {rev['cases']} ({rev['clean_cases']} clean diffs) |",
        f"| Reviewer recall | {_fmt(rev['recall'])} |",
        f"| Reviewer precision | {_fmt(rev['precision'])} |",
        f"| Reviewer false-positive rate (noise) | {_fmt(rev['false_positive_rate'])} |",
        f"| Clean diffs with any finding | {rev['noisy_clean_cases']} / {rev['clean_cases']} |",
        "| LLM calls / tokens in / out | "
        + f"{cost['llm_calls']} / {cost['input_tokens']} / {cost['output_tokens']} |",
        f"| Cost total / per run | ${cost['total_usd']:.4f} / ${cost['per_run_usd']:.4f} |",
        "| Latency mean / max | "
        + f"{summary['latency_seconds']['mean']}s / {summary['latency_seconds']['max']}s |",
        "",
        "## Investigator cases",
        "",
        "| Case | Expected | Predicted | File | Fallback |",
        "|---|---|---|---|---|",
    ]
    for r in (r for r in results if r["agent"] == "investigator"):
        mark = "✅" if r["correct"] else "❌"
        file_mark = {True: "✅", False: "❌", None: "–"}[r["file_correct"]]
        fallback = r["failure_reason"] or "" if r["fallback"] else ""
        lines.append(
            f"| `{r['id']}` | {r['expected_category']} | {mark} {r['predicted_category']} | "
            f"{file_mark} | {fallback} |"
        )
    lines += ["", "## Reviewer cases", "", "| Case | Expected | Found | Reported | FP |",
              "|---|---|---|---|---|"]  # fmt: skip
    for r in (r for r in results if r["agent"] == "reviewer"):
        lines.append(
            f"| `{r['id']}` | {r['expected']} | {r['found']} | {r['reported']} | "
            f"{r['false_positives']} |"
        )
    return "\n".join(lines) + "\n"


# --- main -----------------------------------------------------------------------


def pick_policy_runner(settings: AgentSettings):
    if OpaRunner(settings).available:
        return run_policies, "opa"
    return replica_policy_runner, "rego-replica (opa not installed)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate InfraForge agents")
    parser.add_argument("--mode", choices=["heuristic", "llm"], default="heuristic")
    parser.add_argument("--cases", help="only run cases whose id contains this text")
    parser.add_argument("--report", help="also write the markdown report to this file")
    args = parser.parse_args(argv)

    settings = AgentSettings()
    llm = AnthropicClient(settings) if args.mode == "llm" else None
    policy_runner, policy_engine = pick_policy_runner(settings)

    results = []
    for case in load_cases(args.cases):
        if case.agent == "investigator":
            results.append(run_investigator(case, llm, settings))
        else:
            results.append(run_reviewer(case, llm, settings, policy_runner))
        print(f"  {case.id}: done", flush=True)

    summary = summarize(results)
    summary["policy_engine"] = policy_engine
    markdown = render_markdown(args.mode, settings.model, summary, results)
    print("\n" + markdown)

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = RESULTS_DIR / f"{args.mode}-{stamp}.json"
    out.write_text(json.dumps({"summary": summary, "results": results}, indent=2))
    print(f"Detailed results: {out}")
    if args.report:
        Path(args.report).write_text(markdown, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
