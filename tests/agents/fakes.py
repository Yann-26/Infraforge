"""Test doubles shared by the agent unit tests."""

from agents.llm import LLMError, LLMResponse, Message


class FakeLLM:
    """Returns scripted replies in order. An ``Exception`` item is raised instead."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[tuple[str, list[Message]]] = []

    def complete(self, system: str, messages: list[Message]) -> LLMResponse:
        self.calls.append((system, list(messages)))
        if not self.replies:
            raise LLMError("no more scripted replies", reason="api_error")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return LLMResponse(text=reply, input_tokens=1000, output_tokens=200, model="fake")
