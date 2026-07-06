"""Reusable fakes for deterministic agent-loop tests (no network, no API key).

`FakeProvider` returns a preset script of LLMResponses — one per `chat()`/`stream()`
call — so the loop can be tested deterministically without touching a real model.
"""

import io
from typing import Any

from rich.console import Console

from mycode.llm.base import BaseProvider, LLMResponse, StopReason, ToolCall


def quiet_console() -> Console:
    """A Console that discards output, keeping test runs clean."""
    return Console(file=io.StringIO())


class FakeProvider(BaseProvider):
    """Returns a scripted sequence of LLMResponses and records call context."""

    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.call_count = 0
        self.tools_seen: list[list[dict[str, Any]] | None] = []
        self.snapshots: list[list[dict[str, Any]]] = []

    def chat(self, messages, tools=None) -> LLMResponse:  # noqa: ARG002
        self.snapshots.append([dict(m) for m in messages])
        self.tools_seen.append(tools)
        self.call_count += 1
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        if not isinstance(item, LLMResponse):
            raise AssertionError(f"unexpected script item: {item!r}")
        return item

    def stream(self, messages, tools=None):  # noqa: ARG002
        self.snapshots.append([dict(m) for m in messages])
        self.tools_seen.append(tools)
        self.call_count += 1
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        if not isinstance(item, LLMResponse):
            raise AssertionError(f"unexpected script item: {item!r}")
        if item.reasoning_content:
            from mycode.llm.base import ReasoningChunk

            yield ReasoningChunk(item.reasoning_content)
        if item.text:
            yield item.text
        return item


class AlwaysToolProvider(BaseProvider):
    """Never finishes — always asks for a tool call (to test the step cap)."""

    def __init__(self, path: str) -> None:
        self.count = 0
        self._path = path

    def chat(self, messages, tools=None) -> LLMResponse:
        self.count += 1
        return LLMResponse(
            tool_calls=[
                ToolCall(id=f"c{self.count}", name="list_files", args={"path": self._path})
            ],
            stop_reason=StopReason.TOOL_CALLS,
        )


class BoomProvider(BaseProvider):
    """Raises on `chat()` — to test the loop's error handling."""

    def __init__(self, exc: Exception | None = None) -> None:
        self._exc = exc or RuntimeError("network down")

    def chat(self, messages, tools=None) -> LLMResponse:
        raise self._exc
