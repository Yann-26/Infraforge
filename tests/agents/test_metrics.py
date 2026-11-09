"""Metrics recording, Pushgateway push and the text-file fallback."""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from agents.config import AgentSettings
from agents.feedback import collect, verdict
from agents.metrics import RunMetrics, feedback_registry

REPORT = {
    "agent": "reviewer",
    "outcome": "fallback",
    "failure_reason": "timeout",
    "latency_seconds": 12.5,
    "llm_latency_seconds": 10.0,
    "posted": True,
    "findings_detail": [
        {"severity": "critical", "source": "opa"},
        {"severity": "high", "source": "opa"},
    ],
    "filtered": {"below_threshold": 3},
    "usage": {"calls": 1, "input_tokens": 4000, "output_tokens": 500, "cost_usd": 0.026},
}


def value(metrics: RunMetrics, name: str, **labels) -> float:
    return metrics.registry.get_sample_value(name, labels) or 0.0


def test_record_maps_report_to_metrics():
    m = RunMetrics("reviewer")
    m.record(REPORT)
    assert value(m, "infraforge_agent_runs_total", agent="reviewer", outcome="fallback") == 1
    assert value(m, "infraforge_agent_fallbacks_total", agent="reviewer", reason="timeout") == 1
    assert value(m, "infraforge_agent_failures_total", agent="reviewer", reason="timeout") == 1
    assert value(m, "infraforge_agent_tokens_total", agent="reviewer", direction="input") == 4000
    assert value(m, "infraforge_agent_cost_usd_total", agent="reviewer") == pytest.approx(0.026)
    assert value(m, "infraforge_agent_latency_seconds_sum", agent="reviewer") == 12.5
    assert (
        value(
            m,
            "infraforge_agent_findings_total",
            agent="reviewer",
            severity="critical",
            source="opa",
        )
        == 1
    )
    assert value(m, "infraforge_agent_comments_posted_total", agent="reviewer") == 1


def test_missing_api_key_is_a_fallback_but_not_a_failure():
    m = RunMetrics("investigator")
    m.record({"outcome": "fallback", "failure_reason": "not_configured", "usage": {}})
    assert value(
        m, "infraforge_agent_fallbacks_total", agent="investigator", reason="not_configured"
    )
    assert not value(
        m, "infraforge_agent_failures_total", agent="investigator", reason="not_configured"
    )


def test_unreachable_pushgateway_falls_back_to_textfile(tmp_path):
    path = tmp_path / "m.prom"
    settings = AgentSettings(
        _env_file=None, pushgateway_url="http://127.0.0.1:9", metrics_textfile=str(path)
    )
    m = RunMetrics("reviewer")
    m.record(REPORT)
    assert m.export(settings) is False
    assert "infraforge_agent_runs_total" in path.read_text()


def test_push_uses_per_run_grouping_key(tmp_path):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # pushadd uses POST
            seen.append(self.path)
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()
    settings = AgentSettings(
        _env_file=None,
        pushgateway_url=f"http://127.0.0.1:{server.server_port}",
        metrics_textfile=str(tmp_path / "m.prom"),
        CI_PIPELINE_ID=77,
        CI_JOB_ID=88,
    )
    m = RunMetrics("investigator")
    m.record({"outcome": "ok", "usage": {}})
    assert m.export(settings) is True
    server.server_close()
    assert seen == ["/metrics/job/infraforge_ai_agents/agent/investigator/ci_job/88/pipeline/77"]


@pytest.mark.parametrize(
    "names,expected",
    [
        (["thumbsup"], "accepted"),
        (["thumbsdown", "thumbsdown", "thumbsup"], "rejected"),
        ([], "ignored"),
        (["thumbsup", "thumbsdown"], "ignored"),
        (["rocket"], "ignored"),
    ],
)
def test_verdict(names, expected):
    assert verdict([{"name": n} for n in names]) == expected


def test_collect_counts_only_agent_comments():
    class FakeGitLab:
        def merge_requests(self, updated_after):
            return [{"iid": 1}]

        def notes(self, iid):
            return [
                {"id": 10, "body": "<!-- infraforge-agent:reviewer key=sha-a -->\n..."},
                {"id": 11, "body": "<!-- infraforge-agent:investigator key=pipeline-1 -->"},
                {"id": 12, "body": "human comment"},
            ]

        def note_award_emoji(self, iid, note_id):
            return [{"name": "thumbsup"}] if note_id == 10 else []

    counts = collect(FakeGitLab(), days=7)
    assert counts == {("reviewer", "accepted"): 1, ("investigator", "ignored"): 1}
    registry = feedback_registry(counts)
    labels = {"agent": "reviewer", "verdict": "accepted"}
    assert registry.get_sample_value("infraforge_agent_suggestions", labels) == 1


def test_dashboard_only_queries_metrics_that_exist():
    import json
    import re
    from pathlib import Path

    from prometheus_client import generate_latest

    dashboard = json.loads(Path("observability/grafana/dashboards/ai-agents.json").read_text())
    exprs = [t["expr"] for p in dashboard["panels"] for t in p.get("targets", [])]
    used = {name for e in exprs for name in re.findall(r"infraforge_agent_\w+", e)}

    m = RunMetrics("reviewer")
    m.record(REPORT)
    exported = (
        generate_latest(m.registry).decode() + generate_latest(feedback_registry({})).decode()
    )
    defined = set(re.findall(r"^(infraforge_agent_\w+?)(?:\{| )", exported, re.MULTILINE))
    assert used <= defined, f"dashboard uses undefined metrics: {used - defined}"
