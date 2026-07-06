"""Deterministic providers used by installed offline evals."""

from __future__ import annotations

from mycode.llm.base import BaseProvider, LLMResponse


class FakeProvider(BaseProvider):
    """Return one scripted response per chat call without network access."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.call_count = 0
        self.tools_seen: list[list | None] = []
        self.snapshots: list[list[dict]] = []

    def chat(self, messages, tools=None) -> LLMResponse:
        self.call_count += 1
        self.tools_seen.append(tools)
        self.snapshots.append([dict(message) for message in messages])
        if not self._responses:
            raise AssertionError("FakeProvider 脚本已耗尽,但 agent 又调用了一次 chat()")
        return self._responses.pop(0)


__all__ = ["FakeProvider"]
