"""Thin LLM client.

``LLMClient`` is the only interface agents depend on, so tests and the evaluation
script can plug in a fake. ``AnthropicClient`` is the production implementation.

Reliability controls (all configurable in ``AgentSettings``):
- per-request timeout,
- transport retries with exponential backoff (handled by the SDK: 408/409/429/5xx and
  connection errors),
- refusals and truncated output are reported as errors, never silently accepted.
"""

from dataclasses import dataclass, field
from typing import Protocol

from loguru import logger

from agents.config import AgentSettings


class LLMError(Exception):
    """The LLM call failed (timeout, API error, refusal, empty output)."""

    def __init__(
        self, message: str, reason: str = "api_error", response: "LLMResponse | None" = None
    ):
        super().__init__(message)
        self.reason = reason
        # Set when tokens were spent before the failure, so usage is still recorded.
        self.response = response


@dataclass
class Message:
    role: str  # "user" | "assistant"
    content: str


@dataclass
class LLMResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


@dataclass
class UsageTracker:
    """Accumulates token usage and cost for one agent run."""

    price_in_per_mtok: float = 0.0
    price_out_per_mtok: float = 0.0
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    models: set[str] = field(default_factory=set)

    def record(self, response: LLMResponse) -> None:
        self.calls += 1
        self.input_tokens += response.input_tokens
        self.output_tokens += response.output_tokens
        if response.model:
            self.models.add(response.model)

    @property
    def cost_usd(self) -> float:
        return (
            self.input_tokens * self.price_in_per_mtok
            + self.output_tokens * self.price_out_per_mtok
        ) / 1_000_000

    def as_dict(self) -> dict:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "models": sorted(self.models),
        }


class LLMClient(Protocol):
    def complete(self, system: str, messages: list[Message]) -> LLMResponse: ...


class AnthropicClient:
    """Claude via the official Anthropic SDK."""

    def __init__(self, settings: AgentSettings):
        if settings.anthropic_api_key is None:
            raise LLMError("ANTHROPIC_API_KEY is not set", reason="not_configured")
        import anthropic  # imported lazily so tests and offline eval need no SDK

        self._anthropic = anthropic
        self._settings = settings
        self._client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key.get_secret_value(),
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )

    def _create(self, system: str, messages: list[dict]):
        s = self._settings
        kwargs = {
            "model": s.model,
            "max_tokens": s.max_output_tokens,
            "system": system,
            "messages": messages,
            "output_config": {"effort": s.effort},
        }
        if s.refusal_fallback:
            # On a policy refusal the API re-runs the request on a fallback model.
            return self._client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
            )
        return self._client.messages.create(**kwargs)

    def complete(self, system: str, messages: list[Message]) -> LLMResponse:
        anthropic = self._anthropic
        payload = [{"role": m.role, "content": m.content} for m in messages]
        try:
            response = self._create(system, payload)
        except anthropic.APITimeoutError as exc:
            raise LLMError(f"LLM request timed out: {exc}", reason="timeout") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError(f"LLM rate limited: {exc}", reason="rate_limited") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"LLM API error {exc.status_code}", reason="api_error") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"LLM connection error: {exc}", reason="connection") from exc

        usage = response.usage
        result = LLMResponse(
            text="".join(b.text for b in response.content if b.type == "text"),
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            model=response.model,
        )
        logger.info(
            "LLM call model={} stop={} in={} out={}",
            result.model,
            response.stop_reason,
            result.input_tokens,
            result.output_tokens,
        )
        if response.stop_reason == "refusal":
            raise LLMError("model declined the request", reason="refusal", response=result)
        if response.stop_reason == "max_tokens":
            # Truncated JSON would fail validation anyway; report it precisely.
            raise LLMError("model output hit max_tokens", reason="truncated", response=result)
        if not result.text.strip():
            raise LLMError("model returned no text", reason="empty", response=result)
        return result
