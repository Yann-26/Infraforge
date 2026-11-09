"""JSON validation, retry-once and fallback behaviour of run_structured()."""

import json
from types import SimpleNamespace

import httpx2
import pytest

from agents.config import AgentSettings
from agents.llm import AnthropicClient, LLMError, Message, UsageTracker
from agents.schemas import Diagnosis, Review
from agents.structured import extract_json, parse_and_validate, run_structured
from tests.agents.fakes import FakeLLM

VALID_DIAGNOSIS = {
    "category": "terraform_plan",
    "summary": "Unsupported argument in VM module",
    "explanation": "The module passes `cpu` but the variable is called `cpu_cores`.",
    "evidence": [{"line": 'Error: Unsupported argument "cpu"', "why": "root cause"}],
    "file": "terraform/main.tf",
    "line": 12,
    "suggested_fix": "Rename the argument to cpu_cores.",
    "suggested_patch": None,
    "confidence": 0.9,
}


def test_valid_json_is_accepted_first_try():
    llm = FakeLLM(json.dumps(VALID_DIAGNOSIS))
    usage = UsageTracker()
    result = run_structured(llm, "sys", "user", Diagnosis, usage)
    assert result.ok and result.attempts == 1
    assert result.value.category == "terraform_plan"
    assert usage.calls == 1 and usage.input_tokens == 1000


def test_fenced_json_with_prose_is_extracted():
    text = "Here you go:\n```json\n" + json.dumps(VALID_DIAGNOSIS) + "\n```\nThanks"
    assert parse_and_validate(text, Diagnosis).line == 12


def test_invalid_then_valid_retries_once_with_error_feedback():
    bad = dict(VALID_DIAGNOSIS, category="bogus", confidence=3)
    llm = FakeLLM(json.dumps(bad), json.dumps(VALID_DIAGNOSIS))
    result = run_structured(llm, "sys", "user", Diagnosis, UsageTracker())
    assert result.ok and result.attempts == 2
    retry_messages = llm.calls[1][1]
    assert [m.role for m in retry_messages] == ["user", "assistant", "user"]
    assert "category" in retry_messages[-1].content
    assert "confidence" in retry_messages[-1].content


def test_invalid_twice_falls_back():
    llm = FakeLLM("not json at all", '{"summary": "missing fields"}')
    usage = UsageTracker()
    result = run_structured(llm, "sys", "user", Diagnosis, usage)
    assert not result.ok
    assert result.failure_reason == "invalid_output"
    assert result.attempts == 2
    assert usage.calls == 2  # both attempts are billed and counted


def test_retry_count_is_configurable():
    llm = FakeLLM("nope")
    result = run_structured(llm, "s", "u", Review, UsageTracker(), validation_retries=0)
    assert result.failure_reason == "invalid_output" and result.attempts == 1


def test_extra_fields_are_rejected():
    with pytest.raises(ValueError):
        parse_and_validate(json.dumps(dict(VALID_DIAGNOSIS, shell="rm -rf /")), Diagnosis)


def test_no_json_object_raises_value_error():
    with pytest.raises(ValueError):
        extract_json("I could not determine the cause.")


@pytest.mark.parametrize("reason", ["timeout", "api_error", "rate_limited", "refusal"])
def test_llm_errors_fall_back_without_validation_retry(reason):
    llm = FakeLLM(LLMError("boom", reason=reason), json.dumps(VALID_DIAGNOSIS))
    result = run_structured(llm, "sys", "user", Diagnosis, UsageTracker())
    assert not result.ok
    assert result.failure_reason == reason
    assert len(llm.calls) == 1  # transport retries already happened inside the SDK


def test_unexpected_exception_never_escapes():
    llm = FakeLLM(RuntimeError("bug in client"))
    result = run_structured(llm, "sys", "user", Diagnosis, UsageTracker())
    assert result.failure_reason == "client_error"


def test_usage_recorded_even_when_llm_fails_after_spending_tokens():
    from agents.llm import LLMResponse

    spent = LLMResponse(text="{", input_tokens=500, output_tokens=8000, model="m")
    llm = FakeLLM(LLMError("truncated", reason="truncated", response=spent))
    usage = UsageTracker(price_in_per_mtok=4, price_out_per_mtok=20)
    run_structured(llm, "s", "u", Diagnosis, usage)
    assert usage.output_tokens == 8000
    assert usage.cost_usd == pytest.approx((500 * 4 + 8000 * 20) / 1e6)


# --- AnthropicClient error mapping (SDK call stubbed, no network) -------------


def _client(monkeypatch, create):
    settings = AgentSettings(ANTHROPIC_API_KEY="test-key", refusal_fallback=False)
    client = AnthropicClient(settings)
    monkeypatch.setattr(client, "_create", create)
    return client


def _response(stop_reason="end_turn", text='{"a": 1}'):
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        model="claude-opus-5-5",
        stop_reason=stop_reason,
    )


def test_missing_api_key_is_not_configured(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMError) as exc:
        AnthropicClient(AgentSettings())
    assert exc.value.reason == "not_configured"


def test_timeout_is_mapped(monkeypatch):
    import anthropic

    def create(system, messages):
        raise anthropic.APITimeoutError(request=httpx2.Request("POST", "https://api.test"))

    with pytest.raises(LLMError) as exc:
        _client(monkeypatch, create).complete("s", [Message("user", "u")])
    assert exc.value.reason == "timeout"


def test_text_blocks_only_and_usage(monkeypatch):
    response = _client(monkeypatch, lambda s, m: _response()).complete("s", [Message("user", "u")])
    assert response.text == '{"a": 1}'
    assert (response.input_tokens, response.output_tokens) == (10, 5)


@pytest.mark.parametrize("stop,reason", [("refusal", "refusal"), ("max_tokens", "truncated")])
def test_bad_stop_reasons_are_errors_with_usage(monkeypatch, stop, reason):
    client = _client(monkeypatch, lambda s, m: _response(stop_reason=stop))
    with pytest.raises(LLMError) as exc:
        client.complete("s", [Message("user", "u")])
    assert exc.value.reason == reason
    assert exc.value.response.output_tokens == 5
