"""Agent configuration.

Every value can be overridden with an environment variable (CI/CD variables in GitLab).
Secrets (``ANTHROPIC_API_KEY``, ``AGENT_GITLAB_TOKEN``) are only ever read from the
environment and are never logged.
"""

from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Severity = Literal["low", "medium", "high", "critical"]
SEVERITY_ORDER: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}

# USD per million tokens (input, output). Used only for cost reporting.
MODEL_PRICING_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}


class AgentSettings(BaseSettings):
    """Runtime settings for all agents."""

    model_config = SettingsConfigDict(env_prefix="AGENT_", case_sensitive=False, extra="ignore")

    # --- LLM ---------------------------------------------------------------
    anthropic_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY", "AGENT_ANTHROPIC_API_KEY")
    )
    model: str = "claude-opus-5-5"
    effort: Literal["low", "medium", "high"] = "medium"
    max_output_tokens: int = 8000
    llm_timeout_seconds: float = 60.0
    # Transport retries (429/5xx/timeouts), done by the SDK with exponential backoff.
    llm_max_retries: int = 2
    # Extra attempts when the model returns JSON that fails Pydantic validation.
    validation_retries: int = 1
    # Server-side refusal fallback (Claude API beta). Disable if your account lacks it.
    refusal_fallback: bool = True

    # --- Input limits ------------------------------------------------------
    log_tail_lines: int = 300
    max_log_chars: int = 30_000
    max_diff_chars: int = 60_000
    max_failed_jobs: int = 3
    redact_ips: bool = True

    # --- Reviewer ----------------------------------------------------------
    severity_threshold: Severity = "high"
    min_confidence: float = 0.7
    max_findings: int = 10

    # --- GitLab (token limited to Reporter + api, used only to read + comment) --
    gitlab_url: str = Field(
        default="https://gitlab.com",
        validation_alias=AliasChoices("CI_SERVER_URL", "AGENT_GITLAB_URL"),
    )
    gitlab_token: SecretStr | None = None
    project_id: str | None = Field(
        default=None, validation_alias=AliasChoices("CI_PROJECT_ID", "AGENT_PROJECT_ID")
    )
    mr_iid: int | None = Field(
        default=None, validation_alias=AliasChoices("CI_MERGE_REQUEST_IID", "AGENT_MR_IID")
    )
    pipeline_id: int | None = Field(
        default=None, validation_alias=AliasChoices("CI_PIPELINE_ID", "AGENT_PIPELINE_ID")
    )
    job_id: int | None = Field(
        default=None, validation_alias=AliasChoices("CI_JOB_ID", "AGENT_JOB_ID")
    )
    commit_sha: str | None = Field(
        default=None, validation_alias=AliasChoices("CI_COMMIT_SHA", "AGENT_COMMIT_SHA")
    )
    gitlab_timeout_seconds: float = 20.0

    # --- Metrics -----------------------------------------------------------
    pushgateway_url: str | None = None
    metrics_textfile: str = "agent-metrics.prom"

    # --- Paths -------------------------------------------------------------
    policies_dir: str = "policies/opa"
    opa_binary: str = "opa"

    def price_per_mtok(self) -> tuple[float, float]:
        return MODEL_PRICING_PER_MTOK.get(self.model, (0.0, 0.0))


def get_settings() -> AgentSettings:
    return AgentSettings()
