import pytest


@pytest.fixture(autouse=True)
def isolated_agent_env(tmp_path, monkeypatch):
    """Keep test runs from writing metrics into the repo or pushing anywhere."""
    monkeypatch.setenv("AGENT_METRICS_TEXTFILE", str(tmp_path / "agent-metrics.prom"))
    monkeypatch.delenv("AGENT_PUSHGATEWAY_URL", raising=False)
