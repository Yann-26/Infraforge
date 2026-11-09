"""Call the model and get back a validated Pydantic object — or a clean failure.

Flow::

    call LLM ──► extract JSON ──► validate with Pydantic ──► ok
                       │ invalid
                       ▼
           retry once, feeding the validation error back
                       │ still invalid / LLM error
                       ▼
                 StructuredResult(ok=False, ...)  → caller falls back

The caller never sees an exception from here: every failure becomes a
``StructuredResult`` with a ``failure_reason`` the metrics and comments can use.
"""

import json
import re
import time
from dataclasses import dataclass
from typing import Generic, TypeVar

from loguru import logger
from pydantic import BaseModel, ValidationError

from agents.llm import LLMClient, LLMError, Message, UsageTracker

T = TypeVar("T", bound=BaseModel)

_FENCE = re.compile(r"```(?:json)?\s*(\{[\s\S]*\})\s*```")


@dataclass
class StructuredResult(Generic[T]):
    value: T | None
    attempts: int
    latency_seconds: float
    failure_reason: str | None = None  # timeout | api_error | invalid_output | ...
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.value is not None


def extract_json(text: str) -> str:
    """Pull the JSON object out of a model reply (handles ``` fences and stray prose)."""
    fenced = _FENCE.search(text)
    if fenced:
        return fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in model output")
    return text[start : end + 1]


def parse_and_validate(text: str, schema: type[T]) -> T:
    """Raise ``ValueError`` (incl. ``ValidationError``) if ``text`` is not a valid ``schema``."""
    raw = extract_json(text)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    return schema.model_validate(data)


def _short_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        parts = [f"{'.'.join(map(str, e['loc'])) or '<root>'}: {e['msg']}" for e in exc.errors()]
        return "; ".join(parts[:10])
    return str(exc)[:500]


def run_structured(
    llm: LLMClient,
    system: str,
    user: str,
    schema: type[T],
    usage: UsageTracker,
    validation_retries: int = 1,
) -> StructuredResult[T]:
    """Ask for ``schema``-shaped JSON; retry ``validation_retries`` times on invalid output."""
    started = time.monotonic()
    messages = [Message("user", user)]
    attempts = 0
    last_error = ""

    while attempts <= validation_retries:
        attempts += 1
        try:
            response = llm.complete(system, messages)
        except LLMError as exc:
            if exc.response is not None:
                usage.record(exc.response)
            logger.warning("LLM call failed ({}): {}", exc.reason, exc)
            return StructuredResult(
                None, attempts, time.monotonic() - started, exc.reason, str(exc)
            )
        except Exception as exc:  # noqa: BLE001 - an agent must never crash the job
            logger.exception("unexpected LLM client error")
            return StructuredResult(
                None, attempts, time.monotonic() - started, "client_error", str(exc)
            )

        usage.record(response)
        try:
            value = parse_and_validate(response.text, schema)
            return StructuredResult(value, attempts, time.monotonic() - started)
        except ValueError as exc:
            last_error = _short_error(exc)
            logger.warning("attempt {}: model output failed validation: {}", attempts, last_error)
            messages += [
                Message("assistant", response.text),
                Message(
                    "user",
                    "Your previous reply was not valid. Errors: "
                    f"{last_error}\nReply again with ONLY one JSON object matching the schema.",
                ),
            ]

    return StructuredResult(
        None, attempts, time.monotonic() - started, "invalid_output", last_error
    )
