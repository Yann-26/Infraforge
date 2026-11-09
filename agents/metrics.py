"""Prometheus metrics for agent runs.

Agents run as short CI jobs, so Prometheus cannot scrape them. Each run pushes its
metrics to a Pushgateway (``AGENT_PUSHGATEWAY_URL``) under its own grouping key
(agent + pipeline + job), and always writes a ``.prom`` text file that CI keeps as an
artifact. If the Pushgateway is unreachable the run continues; only the file remains.

Because every run is its own group, ``sum(...)`` over a metric gives totals across
runs; see observability/grafana/dashboards/ai-agents.json and docs/AI_AGENTS.md.
"""

from loguru import logger
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    disable_created_metrics,
    pushadd_to_gateway,
    write_to_textfile,
)

from agents.config import AgentSettings

disable_created_metrics()  # *_created series add nothing for one-shot CI runs

JOB = "infraforge_ai_agents"
FEEDBACK_JOB = "infraforge_ai_agents_feedback"
# Reasons where the agent did not fail, it simply had nothing to do / no key.
NOT_FAILURES = {None, "not_configured", "nothing_to_review"}


class RunMetrics:
    def __init__(self, agent: str):
        self.agent = agent
        self.registry = CollectorRegistry()
        r = self.registry
        self.runs = Counter("infraforge_agent_runs", "Agent runs", ["agent", "outcome"], registry=r)
        self.failures = Counter(
            "infraforge_agent_failures", "LLM/agent failures", ["agent", "reason"], registry=r
        )
        self.fallbacks = Counter(
            "infraforge_agent_fallbacks",
            "Runs that used the fallback path",
            ["agent", "reason"],
            registry=r,
        )
        self.latency = Histogram(
            "infraforge_agent_latency_seconds",
            "End-to-end run latency",
            ["agent"],
            buckets=(1, 2, 5, 10, 20, 30, 60, 120, 300),
            registry=r,
        )
        self.llm_latency = Histogram(
            "infraforge_agent_llm_latency_seconds",
            "Time spent in LLM calls",
            ["agent"],
            buckets=(1, 2, 5, 10, 20, 30, 60, 120, 300),
            registry=r,
        )
        self.tokens = Counter(
            "infraforge_agent_tokens", "LLM tokens", ["agent", "direction"], registry=r
        )
        self.cost = Counter(
            "infraforge_agent_cost_usd", "Estimated LLM cost", ["agent"], registry=r
        )
        self.llm_calls = Counter("infraforge_agent_llm_calls", "LLM calls", ["agent"], registry=r)
        self.findings = Counter(
            "infraforge_agent_findings",
            "Reported findings",
            ["agent", "severity", "source"],
            registry=r,
        )
        self.filtered = Counter(
            "infraforge_agent_findings_filtered",
            "Findings dropped by noise filters",
            ["agent", "reason"],
            registry=r,
        )
        self.comments = Counter(
            "infraforge_agent_comments_posted", "MR comments posted", ["agent"], registry=r
        )
        self.last_run = Gauge(
            "infraforge_agent_last_run_timestamp_seconds",
            "When this run finished",
            ["agent"],
            registry=r,
        )

    def record(self, report: dict) -> None:
        a = self.agent
        outcome = report.get("outcome", "unknown")
        reason = report.get("failure_reason")
        usage = report.get("usage", {})

        self.runs.labels(a, outcome).inc()
        if outcome == "fallback":
            self.fallbacks.labels(a, reason or "unknown").inc()
        if reason not in NOT_FAILURES:
            self.failures.labels(a, reason).inc()
        self.latency.labels(a).observe(report.get("latency_seconds", 0))
        if usage.get("calls"):
            self.llm_latency.labels(a).observe(report.get("llm_latency_seconds", 0))
        self.llm_calls.labels(a).inc(usage.get("calls", 0))
        self.tokens.labels(a, "input").inc(usage.get("input_tokens", 0))
        self.tokens.labels(a, "output").inc(usage.get("output_tokens", 0))
        self.cost.labels(a).inc(usage.get("cost_usd", 0.0))
        if report.get("posted"):
            self.comments.labels(a).inc()
        for (severity, source), n in _findings(report).items():
            self.findings.labels(a, severity, source).inc(n)
        for why, n in (report.get("filtered") or {}).items():
            self.filtered.labels(a, why).inc(n)
        self.last_run.labels(a).set_to_current_time()

    def export(self, settings: AgentSettings) -> bool:
        """Write the text file and push to the gateway. Returns True if pushed."""
        try:
            write_to_textfile(settings.metrics_textfile, self.registry)
        except OSError as exc:
            logger.warning("could not write metrics file: {}", exc)
        return push(self.registry, settings, JOB, self.grouping_key(settings))

    def grouping_key(self, settings: AgentSettings) -> dict[str, str]:
        return {
            "agent": self.agent,
            "pipeline": str(settings.pipeline_id or "local"),
            "ci_job": str(settings.job_id or "local"),
        }


def _findings(report: dict) -> dict[tuple[str, str], int]:
    """Findings by (severity, source); the CLI report stores them per dimension."""
    details = report.get("findings_detail") or []
    out: dict[tuple[str, str], int] = {}
    for f in details:
        key = (f["severity"], f["source"])
        out[key] = out.get(key, 0) + 1
    return out


def push(registry, settings: AgentSettings, job: str, grouping_key: dict[str, str]) -> bool:
    if not settings.pushgateway_url:
        return False
    try:
        pushadd_to_gateway(
            settings.pushgateway_url,
            job=job,
            registry=registry,
            grouping_key=grouping_key,
            timeout=5,
        )
        logger.info("metrics pushed to {}", settings.pushgateway_url)
        return True
    except Exception as exc:  # noqa: BLE001 - metrics must never fail the job
        logger.warning(
            "Pushgateway unreachable ({}); metrics kept in {}", exc, settings.metrics_textfile
        )
        return False


def feedback_registry(counts: dict[tuple[str, str], int]) -> CollectorRegistry:
    """Gauge of suggestion verdicts (accepted / rejected / ignored) per agent."""
    registry = CollectorRegistry()
    gauge = Gauge(
        "infraforge_agent_suggestions",
        "Agent comments by reviewer verdict",
        ["agent", "verdict"],
        registry=registry,
    )
    for agent in ("investigator", "reviewer"):
        for verdict in ("accepted", "rejected", "ignored"):
            gauge.labels(agent, verdict).set(counts.get((agent, verdict), 0))
    return registry
