"""Deterministic agent-loop tests using FakeProvider (no network, no API key).

This is the single home for run_agent tests (Task 4 logic + Task 5 fake-provider
acceptance). All providers are scripted; tool dispatch runs for real against
temp dirs.
"""

import io
import json

from rich.console import Console

from mycode.agent.events import EventType
from mycode.agent.loop import _assistant_message, _format_model_error, run_agent
from mycode.agent.runner import AgentRunner, RunRequest, error_signature
from mycode.llm.base import LLMResponse, ReasoningChunk, StopReason, ToolCall, Usage
from mycode.tools.registry import ToolResult
from tests.fakes import AlwaysToolProvider, BoomProvider, FakeProvider, quiet_console


def _stub_dispatch(content: str):
    """Build a dispatch_tool replacement returning a fixed ToolResult(string).

    Mirrors how the old tests patched ``dispatch`` to return a plain string; the
    runner now consumes structured ``ToolResult`` objects, so the stub classifies
    the canned content with the same error-signature heuristic the runner uses.
    """

    def _fn(name, args):  # noqa: ANN001 - matches dispatch_tool signature
        sig = error_signature(content)
        return ToolResult(
            name=name,
            args=dict(args),
            content=content,
            is_error=sig is not None,
            error_signature=sig,
        )

    return _fn


def _assert_tool_pairing(messages: list[dict]) -> None:
    """每个 assistant.tool_calls 后必须紧跟数量与 id 一一对应的 tool 结果消息。"""
    i = 0
    while i < len(messages):
        m = messages[i]
        if m["role"] == "assistant" and m.get("tool_calls"):
            ids = [tc["id"] for tc in m["tool_calls"]]
            following = messages[i + 1 : i + 1 + len(ids)]
            assert [t["role"] for t in following] == ["tool"] * len(ids)
            assert [t["tool_call_id"] for t in following] == ids
            i += 1 + len(ids)
        else:
            i += 1


# --------------------------------------------------------------------------- #
# Task 5 canonical scenario: read_file tool_call, then final text.
# --------------------------------------------------------------------------- #
def test_read_file_tool_call_then_final_text(tmp_path) -> None:
    f = tmp_path / "notes.txt"
    f.write_text("hello from file\n", encoding="utf-8")

    provider = FakeProvider(
        [
            # 第一次:返回一个 read_file 的 tool_call
            LLMResponse(
                tool_calls=[ToolCall(id="call_1", name="read_file", args={"path": str(f)})],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            # 第二次:返回最终文本
            LLMResponse(text="文件内容已读取完毕", stop_reason=StopReason.END_TURN),
        ]
    )
    messages = [{"role": "user", "content": "读一下 notes.txt"}]
    result = run_agent(provider, messages, max_steps=5, tools=[], console=quiet_console())

    # 最终文本正确
    assert result == "文件内容已读取完毕"
    # provider 被调用了两次(一次出 tool_call,一次出 final）
    assert provider.call_count == 2

    # 工具确实被调用:历史里有 tool 结果,且内容是真实文件内容
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert "hello from file" in tool_msgs[0]["content"]

    # tool_calls 与 tool 结果配对正确(id 一一对上)
    assistant = next(m for m in messages if m["role"] == "assistant" and m.get("tool_calls"))
    assert [tc["id"] for tc in assistant["tool_calls"]] == ["call_1"]
    assert tool_msgs[0]["tool_call_id"] == "call_1"
    _assert_tool_pairing(messages)


# --------------------------------------------------------------------------- #
# loop feeds the real tool schemas to the provider
# --------------------------------------------------------------------------- #
def test_loop_passes_tool_schemas_to_provider() -> None:
    provider = FakeProvider([LLMResponse(text="ok", stop_reason=StopReason.END_TURN)])
    run_agent(provider, [{"role": "user", "content": "hi"}], max_steps=3, console=quiet_console())
    names = {t["name"] for t in provider.tools_seen[0]}
    assert {"list_files", "read_file", "search_code"} <= names


# --------------------------------------------------------------------------- #
# no tools -> immediate answer
# --------------------------------------------------------------------------- #
def test_immediate_answer_without_tools() -> None:
    provider = FakeProvider([LLMResponse(text="答案", stop_reason=StopReason.END_TURN)])
    messages = [{"role": "user", "content": "hi"}]
    result = run_agent(provider, messages, max_steps=5, tools=[], console=quiet_console())
    assert result == "答案"
    assert provider.call_count == 1
    assert not [m for m in messages if m["role"] == "tool"]


# --------------------------------------------------------------------------- #
# multiple tool_calls in one round: all executed, ids one-to-one
# --------------------------------------------------------------------------- #
def test_multiple_tool_calls_in_one_round(tmp_path) -> None:
    (tmp_path / "a.py").write_text("MARK = 1\n", encoding="utf-8")
    provider = FakeProvider(
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
    result = run_agent(provider, messages, max_steps=5, tools=[], console=quiet_console())

    assert result == "done"
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["c1", "c2"]
    assert "a.py" in tool_msgs[0]["content"]
    assert "MARK" in tool_msgs[1]["content"]
    _assert_tool_pairing(messages)


# --------------------------------------------------------------------------- #
# tool errors are fed back as content, not raised
# --------------------------------------------------------------------------- #
def test_tool_error_is_fed_back_not_raised() -> None:
    provider = FakeProvider(
        [
            LLMResponse(
                tool_calls=[ToolCall(id="c1", name="read_file", args={"path": "no-such-file"})],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            LLMResponse(text="处理完毕", stop_reason=StopReason.END_TURN),
        ]
    )
    messages = [{"role": "user", "content": "读不存在的文件"}]
    result = run_agent(provider, messages, max_steps=5, tools=[], console=quiet_console())
    assert result == "处理完毕"
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    assert tool_msgs[0]["content"].startswith("错误:")


# --------------------------------------------------------------------------- #
# step cap
# --------------------------------------------------------------------------- #
def test_stops_at_max_steps(tmp_path) -> None:
    provider = AlwaysToolProvider(str(tmp_path))
    messages = [{"role": "user", "content": "loop forever"}]
    buf = io.StringIO()
    result = run_agent(
        provider, messages, max_steps=3, tools=[], console=Console(file=buf, width=200)
    )
    assert result is None
    assert provider.count == 3  # exactly max_steps model calls, no more
    out = buf.getvalue()
    # hitting max_steps still gives a "what I tried / give direction" summary
    assert "已达到最大步数" in out
    assert "给个方向" in out


# --------------------------------------------------------------------------- #
# provider/network error is handled gracefully
# --------------------------------------------------------------------------- #
def test_model_error_returns_none_without_raising() -> None:
    result = run_agent(
        BoomProvider(), [{"role": "user", "content": "x"}], max_steps=3, tools=[], console=quiet_console()
    )
    assert result is None


def test_model_error_formatter_gives_specific_hint() -> None:
    class RateLimitError(Exception):
        pass

    message = _format_model_error(RateLimitError("too many requests"))

    assert "RateLimitError" in message
    assert "限速" in message


# --------------------------------------------------------------------------- #
# assistant message reconstruction serializes tool_calls back to JSON strings
# --------------------------------------------------------------------------- #
def test_loop_invokes_compaction_when_context_limit_set(monkeypatch) -> None:
    calls = {"n": 0}

    def spy(provider, messages, *, context_limit, **kw):
        calls["n"] += 1
        return False

    monkeypatch.setattr("mycode.agent.runner.maybe_compact", spy)
    provider = FakeProvider([LLMResponse(text="ok", stop_reason=StopReason.END_TURN)])
    run_agent(
        provider, [{"role": "user", "content": "x"}], max_steps=3, tools=[],
        console=quiet_console(), context_limit=1000,
    )
    assert calls["n"] >= 1


def test_loop_skips_compaction_without_context_limit(monkeypatch) -> None:
    calls = {"n": 0}
    monkeypatch.setattr(
        "mycode.agent.runner.maybe_compact",
        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or False,
    )
    provider = FakeProvider([LLMResponse(text="ok", stop_reason=StopReason.END_TURN)])
    run_agent(provider, [{"role": "user", "content": "x"}], max_steps=3, tools=[], console=quiet_console())
    assert calls["n"] == 0


def test_loop_streams_text_incrementally() -> None:
    from mycode.llm.base import BaseProvider

    class StreamProvider(BaseProvider):
        def chat(self, messages, tools=None):  # pragma: no cover - not used here
            return LLMResponse(text="hello world", stop_reason=StopReason.END_TURN)

        def stream(self, messages, tools=None):
            yield from ["he", "llo", " world"]
            return LLMResponse(text="hello world", stop_reason=StopReason.END_TURN)

    buf = io.StringIO()
    console = Console(file=buf, width=200)
    result = run_agent(
        StreamProvider(), [{"role": "user", "content": "x"}], max_steps=3, tools=[], console=console
    )
    assert result == "hello world"
    assert "hello world" in buf.getvalue()


def test_loop_displays_reasoning_chain() -> None:
    from mycode.llm.base import BaseProvider

    class ReasoningProvider(BaseProvider):
        def chat(self, messages, tools=None):  # pragma: no cover - not used here
            return LLMResponse(text="ok", stop_reason=StopReason.END_TURN)

        def stream(self, messages, tools=None):
            yield from [
                ReasoningChunk("let"),
                ReasoningChunk(" me"),
                ReasoningChunk(" think"),
            ]
            yield "ok"
            return LLMResponse(text="ok", reasoning_content="let me think", stop_reason=StopReason.END_TURN)

    buf = io.StringIO()
    console = Console(file=buf, width=200)
    result = run_agent(
        ReasoningProvider(), [{"role": "user", "content": "x"}], max_steps=3, tools=[], console=console
    )
    assert result == "ok"
    out = buf.getvalue()
    assert "思考" in out
    assert "let me think" in out
    assert "think\nok" in out
    # reasoning should not leak into persisted assistant content
    messages = [{"role": "user", "content": "x"}]
    _ = run_agent(ReasoningProvider(), messages, max_steps=3, tools=[], console=quiet_console())
    assistant = [m for m in messages if m["role"] == "assistant"]
    assert len(assistant) == 1
    assert "let me think" not in (assistant[0].get("content") or "")


def test_assistant_tool_call_preserves_reasoning_as_separate_field() -> None:
    response = LLMResponse(
        text=None,
        reasoning_content="need to inspect",
        tool_calls=[ToolCall(id="c1", name="read_file", args={"path": "a.py"})],
        stop_reason=StopReason.TOOL_CALLS,
    )

    message = _assistant_message(response)

    assert message["content"] is None
    assert message["reasoning_content"] == "need to inspect"
    assert message["tool_calls"][0]["id"] == "c1"


def test_on_progress_fires_at_consistent_checkpoints(tmp_path) -> None:
    # turn 1 does a tool call; turn 2 answers. on_progress must fire after the
    # tool round (history already consistent) and after the final answer — so an
    # interrupt mid-task leaves a resumable, paired session.
    provider = FakeProvider(
        [
            LLMResponse(
                tool_calls=[ToolCall(id="c1", name="list_files", args={"path": str(tmp_path)})],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            LLMResponse(text="done", stop_reason=StopReason.END_TURN),
        ]
    )
    messages = [{"role": "user", "content": "q"}]
    checkpoints: list[list[dict]] = []
    run_agent(
        provider,
        messages,
        max_steps=5,
        tools=[],
        console=quiet_console(),
        on_progress=lambda: checkpoints.append([dict(m) for m in messages]),
    )
    assert len(checkpoints) >= 2  # after tool round + after final answer
    first = checkpoints[0]
    # first checkpoint is already consistent: assistant tool_call + its tool result
    assert any(m["role"] == "assistant" and m.get("tool_calls") for m in first)
    assert any(m["role"] == "tool" and m.get("tool_call_id") == "c1" for m in first)


def test_token_usage_accumulated_and_printed() -> None:
    buf = io.StringIO()
    console = Console(file=buf, width=200)
    provider = FakeProvider(
        [
            LLMResponse(
                tool_calls=[ToolCall(id="c1", name="list_files", args={"path": "."})],
                stop_reason=StopReason.TOOL_CALLS,
                usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            ),
            LLMResponse(
                text="done",
                stop_reason=StopReason.END_TURN,
                usage=Usage(prompt_tokens=20, completion_tokens=8, total_tokens=28),
            ),
        ]
    )
    run_agent(provider, [{"role": "user", "content": "x"}], max_steps=5, tools=[], console=console)
    out = buf.getvalue()
    assert "token" in out.lower()
    assert "43" in out  # cumulative total: (10+20) input + (5+8) output = 43


def test_cached_tokens_accumulated_and_shown() -> None:
    buf = io.StringIO()
    console = Console(file=buf, width=200)
    provider = FakeProvider(
        [
            LLMResponse(
                tool_calls=[ToolCall(id="c1", name="list_files", args={"path": "."})],
                stop_reason=StopReason.TOOL_CALLS,
                usage=Usage(prompt_tokens=50, completion_tokens=5, total_tokens=55, cached_tokens=0),
            ),
            LLMResponse(
                text="done",
                stop_reason=StopReason.END_TURN,
                usage=Usage(prompt_tokens=60, completion_tokens=8, total_tokens=68, cached_tokens=40),
            ),
        ]
    )
    run_agent(provider, [{"role": "user", "content": "x"}], max_steps=5, tools=[], console=console)
    out = buf.getvalue()
    assert "命中缓存" in out
    assert "40" in out  # cumulative cached: 0 + 40


def test_error_signature_classifies_results() -> None:
    from mycode.agent.loop import _error_signature

    assert _error_signature("错误:文件不存在:x") is not None
    assert _error_signature("[退出码 1]\nFAILED") is not None
    assert _error_signature("[退出码 0]\nok") is None
    assert _error_signature("已编辑:a.py(+1 -1 行)") is None


def test_stuck_on_repeated_edit_file(monkeypatch) -> None:
    # two consecutive identical edit_file calls -> stop early, not run to max_steps
    monkeypatch.setattr("mycode.agent.runner.dispatch_tool", _stub_dispatch("已编辑:a.py(+1 -1 行)"))
    edit = LLMResponse(
        tool_calls=[ToolCall(id="c", name="edit_file", args={"path": "a.py", "old_str": "x", "new_str": "y"})],
        stop_reason=StopReason.TOOL_CALLS,
    )
    provider = FakeProvider([edit, edit, edit])
    buf = io.StringIO()
    result = run_agent(
        provider, [{"role": "user", "content": "修"}], max_steps=20, tools=[],
        console=Console(file=buf, width=200),
    )
    assert result is None
    assert provider.call_count == 2  # stopped after the 2nd identical edit, not 20
    out = buf.getvalue()
    assert "卡住" in out and "edit_file" in out


def test_stuck_on_repeated_error(monkeypatch) -> None:
    # same command error 3x (model keeps "fixing" but the failure never changes)
    monkeypatch.setattr("mycode.agent.runner.dispatch_tool", _stub_dispatch("[退出码 1]\nE   assert 1 == 2"))
    bash = LLMResponse(
        tool_calls=[ToolCall(id="c", name="run_bash", args={"command": "pytest"})],
        stop_reason=StopReason.TOOL_CALLS,
    )
    provider = FakeProvider([bash] * 5)
    buf = io.StringIO()
    result = run_agent(
        provider, [{"role": "user", "content": "修"}], max_steps=20, tools=[],
        console=Console(file=buf, width=200),
    )
    assert result is None
    assert provider.call_count == 3  # stopped after the 3rd identical failure
    out = buf.getvalue()
    assert "卡住" in out and "3 次" in out


def test_no_stuck_on_varied_work(monkeypatch) -> None:
    monkeypatch.setattr("mycode.agent.runner.dispatch_tool", _stub_dispatch("已编辑:ok"))
    r1 = LLMResponse(
        tool_calls=[ToolCall(id="1", name="edit_file", args={"path": "a.py", "old_str": "x", "new_str": "y"})],
        stop_reason=StopReason.TOOL_CALLS,
    )
    r2 = LLMResponse(
        tool_calls=[ToolCall(id="2", name="edit_file", args={"path": "b.py", "old_str": "x", "new_str": "z"})],
        stop_reason=StopReason.TOOL_CALLS,
    )
    r3 = LLMResponse(text="搞定", stop_reason=StopReason.END_TURN)
    provider = FakeProvider([r1, r2, r3])
    result = run_agent(provider, [{"role": "user", "content": "q"}], max_steps=20, tools=[], console=quiet_console())
    assert result == "搞定"
    assert provider.call_count == 3


def test_no_stuck_when_identical_edits_not_consecutive(monkeypatch) -> None:
    monkeypatch.setattr("mycode.agent.runner.dispatch_tool", _stub_dispatch("ok"))
    edit = LLMResponse(
        tool_calls=[ToolCall(id="e", name="edit_file", args={"path": "a.py", "old_str": "x", "new_str": "y"})],
        stop_reason=StopReason.TOOL_CALLS,
    )
    read = LLMResponse(
        tool_calls=[ToolCall(id="r", name="read_file", args={"path": "a.py"})],
        stop_reason=StopReason.TOOL_CALLS,
    )
    final = LLMResponse(text="done", stop_reason=StopReason.END_TURN)
    provider = FakeProvider([edit, read, edit, final])  # identical edits, but a read in between
    result = run_agent(provider, [{"role": "user", "content": "q"}], max_steps=20, tools=[], console=quiet_console())
    assert result == "done"
    assert provider.call_count == 4


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


def test_complex_task_prints_plan_and_injects_temporary_context() -> None:
    provider = FakeProvider(
        [
            LLMResponse(text="1. read files\n2. edit code\n3. run tests"),
            LLMResponse(text="done", stop_reason=StopReason.END_TURN),
        ]
    )
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "fix failing tests and explain the reason"},
    ]
    buf = io.StringIO()

    result = run_agent(
        provider,
        messages,
        max_steps=5,
        tools=[],
        console=Console(file=buf, width=200),
        planning="auto",
        planning_max_steps=3,
    )

    assert result == "done"
    out = buf.getvalue()
    assert "\u8ba1\u5212:" in out
    assert "1. read files" in out
    assert provider.tools_seen == [None, []]
    execution_messages = provider.snapshots[1]
    assert any(
        m["role"] == "system" and "\u5f53\u524d\u4efb\u52a1\u6267\u884c\u8ba1\u5212" in m["content"]
        for m in execution_messages
    )
    assert not any(
        "\u5f53\u524d\u4efb\u52a1\u6267\u884c\u8ba1\u5212" in (m.get("content") or "")
        for m in messages
    )


def test_simple_task_does_not_plan_in_auto_mode() -> None:
    provider = FakeProvider([LLMResponse(text="hi", stop_reason=StopReason.END_TURN)])
    buf = io.StringIO()

    result = run_agent(
        provider,
        [{"role": "user", "content": "hi"}],
        max_steps=3,
        tools=[],
        console=Console(file=buf, width=200),
        planning="auto",
    )

    assert result == "hi"
    assert provider.call_count == 1
    assert "\u8ba1\u5212:" not in buf.getvalue()


def test_plan_only_stops_after_creating_plan() -> None:
    provider = FakeProvider([LLMResponse(text="1. inspect files\n2. propose changes")])
    messages = [{"role": "user", "content": "refactor the service"}]
    events = []

    result = AgentRunner(sinks=[lambda event, attachments: events.append(event)]).run(
        RunRequest(
            provider=provider,
            messages=messages,
            planning="always",
            plan_only=True,
        )
    )

    assert result.final_text == "计划:\n1. inspect files\n2. propose changes"
    assert provider.call_count == 1
    assert messages[-1] == {"role": "assistant", "content": result.final_text}
    assert EventType.PLAN_CREATED in [event.type for event in events]
    assert events[-2].type == EventType.RUN_FINISHED
    assert events[-1].type == EventType.USAGE_REPORTED


def test_plan_failure_continues_original_task() -> None:
    class FailingPlannerProvider(FakeProvider):
        def chat(self, messages, tools=None):
            if self.call_count == 0:
                self.call_count += 1
                self.tools_seen.append(tools)
                self.snapshots.append([dict(m) for m in messages])
                raise RuntimeError("planner down")
            return super().chat(messages, tools)

    provider = FailingPlannerProvider([LLMResponse(text="done", stop_reason=StopReason.END_TURN)])
    buf = io.StringIO()

    result = run_agent(
        provider,
        [{"role": "user", "content": "fix failing tests and explain the reason"}],
        max_steps=3,
        tools=[],
        console=Console(file=buf, width=200),
        planning="auto",
    )

    assert result == "done"
    assert "\u7ee7\u7eed\u6267\u884c\u539f\u4efb\u52a1" in buf.getvalue()


def test_plan_context_keeps_tool_call_result_pairing(tmp_path) -> None:
    f = tmp_path / "notes.txt"
    f.write_text("hello\n", encoding="utf-8")
    provider = FakeProvider(
        [
            LLMResponse(text="1. read file\n2. summarize"),
            LLMResponse(
                tool_calls=[ToolCall(id="call_1", name="read_file", args={"path": str(f)})],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            LLMResponse(text="done", stop_reason=StopReason.END_TURN),
        ]
    )
    messages = [{"role": "user", "content": "fix failing tests and explain the reason"}]

    result = run_agent(
        provider,
        messages,
        max_steps=5,
        tools=[],
        console=quiet_console(),
        planning="always",
    )

    assert result == "done"
    _assert_tool_pairing(messages)
    assert not any(
        "\u5f53\u524d\u4efb\u52a1\u6267\u884c\u8ba1\u5212" in (m.get("content") or "")
        for m in messages
    )


def test_plan_status_updates_after_tool_call_and_stays_temporary(tmp_path) -> None:
    f = tmp_path / "notes.txt"
    f.write_text("hello\n", encoding="utf-8")
    provider = FakeProvider(
        [
            LLMResponse(text="1. read file\n2. summarize\n3. test"),
            LLMResponse(
                tool_calls=[ToolCall(id="call_1", name="read_file", args={"path": str(f)})],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            LLMResponse(text="完成内容:done\n验证命令/结果:not needed\n未完成事项:none", stop_reason=StopReason.END_TURN),
        ]
    )
    messages = [{"role": "user", "content": "fix failing tests and explain the reason"}]
    buf = io.StringIO()

    result = run_agent(
        provider,
        messages,
        max_steps=5,
        tools=[],
        console=Console(file=buf, width=200),
        planning="always",
    )

    assert "完成内容:" in (result or "")
    out = buf.getvalue()
    assert "进度: 1/3 summarize" in out
    second_execution = provider.snapshots[2]
    status_context = "\n".join(m.get("content") or "" for m in second_execution if m["role"] == "system")
    assert "[done] read file" in status_context
    assert "[in_progress] summarize" in status_context
    assert "最终回答必须包含" in status_context
    assert "本任务需要验证" in status_context
    assert not any("进度:" in (m.get("content") or "") for m in messages)
    assert not any("[done] read file" in (m.get("content") or "") for m in messages)


def test_validation_context_injected_without_persisting_when_planning_off() -> None:
    provider = FakeProvider([LLMResponse(text="完成内容:x\n验证命令/结果:y\n未完成事项:z", stop_reason=StopReason.END_TURN)])
    messages = [{"role": "user", "content": "fix failing tests"}]

    result = run_agent(
        provider,
        messages,
        max_steps=3,
        tools=[],
        console=quiet_console(),
        planning="off",
    )

    assert "验证命令/结果" in (result or "")
    context = "\n".join(m.get("content") or "" for m in provider.snapshots[0] if m["role"] == "system")
    assert "本任务需要验证" in context
    assert "最终回答必须包含" in context
    assert not any("本任务需要验证" in (m.get("content") or "") for m in messages)


def test_budget_stops_after_consistent_tool_round(tmp_path) -> None:
    provider = FakeProvider(
        [
            LLMResponse(
                tool_calls=[ToolCall(id="c1", name="list_files", args={"path": str(tmp_path)})],
                stop_reason=StopReason.TOOL_CALLS,
                usage=Usage(prompt_tokens=1_000_000, completion_tokens=1_000_000),
            ),
            LLMResponse(text="should not run", stop_reason=StopReason.END_TURN),
        ]
    )
    messages = [{"role": "user", "content": "fix failing tests"}]
    buf = io.StringIO()

    result = run_agent(
        provider,
        messages,
        max_steps=5,
        tools=[],
        console=Console(file=buf, width=200),
        budget_usd=0.01,
        model="gpt-4o-mini",
    )

    assert result is None
    assert provider.call_count == 1
    assert any(m["role"] == "tool" for m in messages)
    assert "预算已超过" in buf.getvalue()
