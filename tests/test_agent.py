"""Agent loop tests (Task 4): native tool use, multi-call rounds, step cap.

Uses scripted providers (no network) plus the real tool dispatch on temp dirs.
"""

import io
import json

from rich.console import Console

from mycode.agent.loop import _assistant_message, run_agent
from mycode.llm.base import BaseProvider, LLMResponse, StopReason, ToolCall


def _quiet() -> Console:
    return Console(file=io.StringIO())


class ScriptedProvider(BaseProvider):
    """Returns a fixed sequence of LLMResponses, recording messages per call."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.snapshots: list[list[dict]] = []

    def chat(self, messages, tools=None) -> LLMResponse:
        self.snapshots.append([dict(m) for m in messages])
        return self._responses.pop(0)


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


def test_immediate_answer_without_tools() -> None:
    provider = ScriptedProvider([LLMResponse(text="答案", stop_reason=StopReason.END_TURN)])
    messages = [{"role": "user", "content": "hi"}]
    result = run_agent(provider, messages, max_steps=5, tools=[], console=_quiet())
    assert result == "答案"
    assert len(provider.snapshots) == 1
    assert not [m for m in messages if m["role"] == "tool"]


def test_executes_tool_then_answers(tmp_path) -> None:
    (tmp_path / "hello.py").write_text("x", encoding="utf-8")
    provider = ScriptedProvider(
        [
            LLMResponse(
                tool_calls=[ToolCall(id="c1", name="list_files", args={"path": str(tmp_path)})],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            LLMResponse(text="这是一个测试目录", stop_reason=StopReason.END_TURN),
        ]
    )
    messages = [{"role": "user", "content": "这里有什么?"}]
    result = run_agent(provider, messages, max_steps=5, tools=[], console=_quiet())

    assert result == "这是一个测试目录"

    # assistant message appended as a whole block, with the tool_calls
    assistant = [m for m in messages if m["role"] == "assistant"][0]
    assert assistant["tool_calls"][0]["id"] == "c1"
    assert assistant["tool_calls"][0]["function"]["name"] == "list_files"

    # exactly one tool result, id matches, content is the real listing
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "c1"
    assert "hello.py" in tool_msgs[0]["content"]


def test_multiple_tool_calls_in_one_round(tmp_path) -> None:
    (tmp_path / "a.py").write_text("MARK = 1\n", encoding="utf-8")
    provider = ScriptedProvider(
        [
            LLMResponse(
                tool_calls=[
                    ToolCall(id="c1", name="list_files", args={"path": str(tmp_path)}),
                    ToolCall(id="c2", name="search_code", args={"query": "MARK", "path": str(tmp_path)}),
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            LLMResponse(text="done", stop_reason=StopReason.END_TURN),
        ]
    )
    messages = [{"role": "user", "content": "q"}]
    result = run_agent(provider, messages, max_steps=5, tools=[], console=_quiet())

    assert result == "done"
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    # both calls executed, in order, ids one-to-one
    assert [m["tool_call_id"] for m in tool_msgs] == ["c1", "c2"]
    assert "a.py" in tool_msgs[0]["content"]
    assert "MARK" in tool_msgs[1]["content"]


def test_tool_error_is_fed_back_not_raised(tmp_path) -> None:
    provider = ScriptedProvider(
        [
            LLMResponse(
                tool_calls=[ToolCall(id="c1", name="read_file", args={"path": "no-such-file"})],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            LLMResponse(text="处理完毕", stop_reason=StopReason.END_TURN),
        ]
    )
    messages = [{"role": "user", "content": "读不存在的文件"}]
    result = run_agent(provider, messages, max_steps=5, tools=[], console=_quiet())
    assert result == "处理完毕"
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    assert tool_msgs[0]["content"].startswith("错误:")


def test_stops_at_max_steps(tmp_path) -> None:
    provider = AlwaysToolProvider(str(tmp_path))
    messages = [{"role": "user", "content": "loop forever"}]
    result = run_agent(provider, messages, max_steps=3, tools=[], console=_quiet())
    assert result is None
    assert provider.count == 3  # exactly max_steps model calls, no more


def test_model_error_returns_none_without_raising() -> None:
    class BoomProvider(BaseProvider):
        def chat(self, messages, tools=None):
            raise RuntimeError("network down")

    result = run_agent(BoomProvider(), [{"role": "user", "content": "x"}], max_steps=3, tools=[], console=_quiet())
    assert result is None


def test_assistant_message_serializes_tool_calls() -> None:
    resp = LLMResponse(
        text=None,
        tool_calls=[ToolCall(id="c1", name="read_file", args={"path": "a.py"})],
        stop_reason=StopReason.TOOL_CALLS,
    )
    msg = _assistant_message(resp)
    assert msg["role"] == "assistant"
    tc = msg["tool_calls"][0]
    assert tc["type"] == "function"
    assert tc["id"] == "c1"
    # arguments must be a JSON *string* that round-trips back to the dict
    assert json.loads(tc["function"]["arguments"]) == {"path": "a.py"}
