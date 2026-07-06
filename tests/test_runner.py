"""P5 AgentRunner tests: events, cancellation, deadline, retry, structured results.

All deterministic — FakeProvider, a fake monotonic clock, a no-op sleep, and an
explicit run_id. No network, no API key.
"""

from __future__ import annotations

from typing import Any

from mycode.agent.events import EventType, RunStatus
from mycode.agent.runner import (
    AgentRunner,
    CancellationToken,
    RunRequest,
    _is_transient,
    error_signature,
)
from mycode.llm.base import BaseProvider, LLMResponse, StopReason, ToolCall, Usage
from tests.fakes import FakeProvider


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #
class _ScriptedProvider(BaseProvider):
    """Returns a scripted list of responses; raises remaining scripts if they
    are exceptions. Records call count."""

    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.call_count = 0

    def chat(self, messages, tools=None):  # pragma: no cover - stream() used
        raise AssertionError("chat() should not be called; stream() is used")

    def stream(self, messages, tools=None):
        self.call_count += 1
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        if not isinstance(item, LLMResponse):
            raise AssertionError(f"unexpected script item: {item!r}")
        if item.text:
            yield item.text
        return item


class _RateLimitError(Exception):
    pass


class _AuthError(Exception):
    pass


class _ServerError(Exception):
    pass


def _fake_clock():
    t = [0.0]
    return lambda: t[0], t


def _collecting_sink(events: list, attachments: list | None = None):
    def _sink(event, atts):
        events.append(event)
        if attachments is not None:
            attachments.append(atts)

    return _sink


def _run(request: RunRequest, *, trace=None, clock=None, sleep=None, sinks=None):
    runner = AgentRunner(sinks=sinks, trace=trace, clock=clock or (lambda: 0.0), sleep=sleep or (lambda s: None))
    return runner.run(request)


# --------------------------------------------------------------------------- #
# Unique run_id + stable event sequence
# --------------------------------------------------------------------------- #
def test_each_run_has_unique_id_and_monotonic_seq(tmp_path) -> None:
    f = tmp_path / "n.txt"
    f.write_text("hi\n", encoding="utf-8")
    provider = FakeProvider(
        [
            LLMResponse(
                tool_calls=[ToolCall(id="c1", name="read_file", args={"path": str(f)})],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            LLMResponse(text="done", stop_reason=StopReason.END_TURN),
        ]
    )
    events: list = []
    res = _run(
        RunRequest(provider=provider, messages=[{"role": "user", "content": "q"}], max_steps=5, tools=[], run_id="run-xyz", on_progress=None),
        sinks=[_collecting_sink(events)],
    )

    assert res.run_id == "run-xyz"
    assert res.status == RunStatus.COMPLETED
    assert res.final_text == "done"
    # every event carries the same run_id and a strictly increasing seq
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)
    assert all(e.run_id == "run-xyz" for e in events)
    # schema version present
    assert all(e.schema_version >= 1 for e in events)
    # first event is run.started, last is usage.reported
    assert events[0].type == EventType.RUN_STARTED
    assert events[-1].type == EventType.USAGE_REPORTED


def test_two_runs_get_different_ids_when_unspecified() -> None:
    provider = FakeProvider([LLMResponse(text="ok", stop_reason=StopReason.END_TURN)])
    r1 = _run(RunRequest(provider=provider, messages=[{"role": "user", "content": "x"}], max_steps=3, tools=[]))
    provider2 = FakeProvider([LLMResponse(text="ok", stop_reason=StopReason.END_TURN)])
    r2 = _run(RunRequest(provider=provider2, messages=[{"role": "user", "content": "x"}], max_steps=3, tools=[]))
    assert r1.run_id != r2.run_id
    assert len(r1.run_id) >= 8


# --------------------------------------------------------------------------- #
# Structured failure: model error
# --------------------------------------------------------------------------- #
def test_model_error_is_structured_failure() -> None:
    provider = _ScriptedProvider([RuntimeError("network down")])
    events: list = []
    res = _run(
        RunRequest(provider=provider, messages=[{"role": "user", "content": "x"}], max_steps=3, tools=[], run_id="r1"),
        sinks=[_collecting_sink(events)],
    )
    assert res.status == RunStatus.MODEL_ERROR
    assert res.final_text is None
    assert res.error is not None and "调用模型失败" in res.error
    err_events = [e for e in events if e.type == EventType.MODEL_CALL_ERROR]
    assert len(err_events) == 1
    assert err_events[0].payload["reason"] == RunStatus.MODEL_ERROR
    assert "network down" in err_events[0].payload["detail"]


# --------------------------------------------------------------------------- #
# Cancellation: between tools in a round
# --------------------------------------------------------------------------- #
def test_cancellation_stops_between_tool_calls(tmp_path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("x\n", encoding="utf-8")
    # one response with two tool calls; cancel after the first starts
    provider = _ScriptedProvider(
        [
            LLMResponse(
                tool_calls=[
                    ToolCall(id="c1", name="read_file", args={"path": str(f)}),
                    ToolCall(id="c2", name="read_file", args={"path": str(f)}),
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
        ]
    )
    token = CancellationToken()
    messages = [{"role": "user", "content": "q"}]

    def cancelling_sink(event, atts):
        if event.type == EventType.TOOL_CALL_STARTED:
            token.cancel()

    res = _run(
        RunRequest(provider=provider, messages=messages, max_steps=5, tools=[], run_id="rc", cancellation_token=token),
        sinks=[cancelling_sink],
    )
    assert res.status == RunStatus.CANCELLED
    # only the first tool executed; the second was skipped before dispatch
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "c1"


def test_cancellation_before_first_model_call() -> None:
    provider = _ScriptedProvider([LLMResponse(text="never", stop_reason=StopReason.END_TURN)])
    token = CancellationToken()
    token.cancel()
    res = _run(
        RunRequest(provider=provider, messages=[{"role": "user", "content": "q"}], max_steps=5, tools=[], run_id="rc2", cancellation_token=token),
    )
    assert res.status == RunStatus.CANCELLED
    assert provider.call_count == 0


# --------------------------------------------------------------------------- #
# Deadline / total cutoff
# --------------------------------------------------------------------------- #
def test_deadline_stops_run(tmp_path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("x\n", encoding="utf-8")
    # always does a tool call -> many steps; deadline should fire on step 2.
    # _ScriptedProvider.stream is a real generator (yields text then returns).
    bash = LLMResponse(
        tool_calls=[ToolCall(id="c", name="read_file", args={"path": str(f)})],
        stop_reason=StopReason.TOOL_CALLS,
    )
    provider = _ScriptedProvider([bash, bash, bash])
    clock, t = _fake_clock()

    def advance_sink(event, atts):
        if event.type == EventType.MODEL_CALL_STARTED:
            t[0] += 100.0  # jump past the deadline

    res = _run(
        RunRequest(provider=provider, messages=[{"role": "user", "content": "q"}], max_steps=10, tools=[], run_id="rd", deadline_s=50.0),
        clock=clock,
        sinks=[advance_sink],
    )
    assert res.status == RunStatus.DEADLINE_EXCEEDED
    # only one model call completed before the deadline check on step 2
    assert provider.call_count == 1


# --------------------------------------------------------------------------- #
# Transient retry: 429 then success
# --------------------------------------------------------------------------- #
def test_transient_429_is_retried_then_succeeds() -> None:
    provider = _ScriptedProvider(
        [
            _RateLimitError("429 too many requests"),
            LLMResponse(text="recovered", stop_reason=StopReason.END_TURN),
        ]
    )
    slept: list[float] = []
    events: list = []
    res = _run(
        RunRequest(
            provider=provider,
            messages=[{"role": "user", "content": "q"}],
            max_steps=3,
            tools=[],
            run_id="rr",
            retry_transient=True,
            max_retries=2,
            retry_backoff=2.0,
        ),
        sleep=slept.append,
        sinks=[_collecting_sink(events)],
    )
    assert res.status == RunStatus.COMPLETED
    assert res.final_text == "recovered"
    assert provider.call_count == 2
    # a retry event was emitted between the two attempts
    retries = [e for e in events if e.type == EventType.MODEL_CALL_RETRY]
    assert len(retries) == 1
    assert retries[0].payload["attempt"] == 2
    # backoff slept once (2 ** 0 * backoff = 2.0)
    assert slept == [2.0]


def test_transient_5xx_exhausts_retries_then_fails() -> None:
    provider = _ScriptedProvider(
        [
            _ServerError("503 server error overloaded"),
            _ServerError("503 server error overloaded"),
            _ServerError("503 server error overloaded"),
        ]
    )
    res = _run(
        RunRequest(
            provider=provider,
            messages=[{"role": "user", "content": "q"}],
            max_steps=3,
            tools=[],
            run_id="rr2",
            retry_transient=True,
            max_retries=2,
            retry_backoff=0.0,
        ),
    )
    assert res.status == RunStatus.MODEL_ERROR
    assert provider.call_count == 3  # initial + 2 retries


def test_non_transient_auth_error_not_retried() -> None:
    provider = _ScriptedProvider([_AuthError("401 unauthorized")])
    res = _run(
        RunRequest(
            provider=provider,
            messages=[{"role": "user", "content": "q"}],
            max_steps=3,
            tools=[],
            run_id="rr3",
            retry_transient=True,
            max_retries=3,
            retry_backoff=0.0,
        ),
    )
    assert res.status == RunStatus.MODEL_ERROR
    assert provider.call_count == 1  # auth is not transient -> no retry


def test_retry_disabled_by_default() -> None:
    provider = _ScriptedProvider([_RateLimitError("429 rate limit")])
    res = _run(
        RunRequest(provider=provider, messages=[{"role": "user", "content": "q"}], max_steps=3, tools=[], run_id="rr4"),
    )
    assert res.status == RunStatus.MODEL_ERROR
    assert provider.call_count == 1  # default retry_transient=False


# --------------------------------------------------------------------------- #
# Transient classification unit
# --------------------------------------------------------------------------- #
def test_transient_classifier() -> None:
    assert _is_transient(_RateLimitError("429 too many requests"))
    assert _is_transient(RuntimeError("request timed out"))
    assert _is_transient(ConnectionError("failed to connect"))
    assert _is_transient(RuntimeError("server error 502"))
    assert _is_transient(RuntimeError("Overloaded"))
    # non-transient
    assert not _is_transient(_AuthError("401 unauthorized"))
    assert not _is_transient(ValueError("bad request 400"))
    assert not _is_transient(RuntimeError("not found"))


# --------------------------------------------------------------------------- #
# Tool exception is structured, never raised
# --------------------------------------------------------------------------- #
def test_tool_exception_becomes_error_result(monkeypatch) -> None:
    # dispatch_tool's contract is to NEVER raise; it converts internal tool
    # exceptions into an error ToolResult. This stub mimics that behaviour:
    # a tool that blew up is surfaced as a structured error result, and the
    # runner feeds it back to the model just like the legacy loop did.
    from mycode.tools.registry import ToolResult

    def boom(name, args):
        content = f"错误:工具 {name} 执行失败:RuntimeError: tool blew up"
        return ToolResult(
            name=name,
            args=dict(args),
            content=content,
            is_error=True,
            error_signature=content,
        )

    monkeypatch.setattr("mycode.agent.runner.dispatch_tool", boom)
    provider = _ScriptedProvider(
        [
            LLMResponse(
                tool_calls=[ToolCall(id="c1", name="read_file", args={"path": "x"})],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            LLMResponse(text="handled", stop_reason=StopReason.END_TURN),
        ]
    )
    messages = [{"role": "user", "content": "q"}]
    res = _run(
        RunRequest(provider=provider, messages=messages, max_steps=5, tools=[], run_id="rt"),
    )
    assert res.status == RunStatus.COMPLETED
    assert res.final_text == "handled"
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert tool_msgs[0]["content"].startswith("错误:")
    assert "tool blew up" in tool_msgs[0]["content"]


# --------------------------------------------------------------------------- #
# Stuck detection still works through the runner
# --------------------------------------------------------------------------- #
def test_stuck_on_repeated_error_via_runner(monkeypatch) -> None:
    from mycode.tools.registry import ToolResult

    def always_fail(name, args):
        return ToolResult(
            name=name,
            args=dict(args),
            content="[退出码 1]\nE assert 1 == 2",
            is_error=True,
            error_signature="[退出码 1]\nE assert 1 == 2",
        )

    monkeypatch.setattr("mycode.agent.runner.dispatch_tool", always_fail)
    bash = LLMResponse(
        tool_calls=[ToolCall(id="c", name="run_bash", args={"command": "pytest"})],
        stop_reason=StopReason.TOOL_CALLS,
    )
    provider = _ScriptedProvider([bash, bash, bash])
    res = _run(
        RunRequest(provider=provider, messages=[{"role": "user", "content": "fix"}], max_steps=20, tools=[], run_id="rs"),
    )
    assert res.status == RunStatus.STUCK
    assert provider.call_count == 3


# --------------------------------------------------------------------------- #
# Token usage accumulates into the structured result
# --------------------------------------------------------------------------- #
def test_usage_accumulated_in_result() -> None:
    provider = _ScriptedProvider(
        [
            LLMResponse(
                tool_calls=[ToolCall(id="c1", name="read_file", args={"path": "x.py"})],
                stop_reason=StopReason.TOOL_CALLS,
                usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            ),
            LLMResponse(
                text="done",
                stop_reason=StopReason.END_TURN,
                usage=Usage(prompt_tokens=20, completion_tokens=8, total_tokens=28, cached_tokens=4),
            ),
        ]
    )
    res = _run(
        RunRequest(provider=provider, messages=[{"role": "user", "content": "q"}], max_steps=5, tools=[], run_id="ru"),
    )
    assert res.prompt_tokens == 30
    assert res.completion_tokens == 13
    assert res.cached_tokens == 4
    assert res.tool_calls == 1
    assert res.steps_taken == 2


# --------------------------------------------------------------------------- #
# error_signature helper (re-exported)
# --------------------------------------------------------------------------- #
def test_error_signature_helper() -> None:
    assert error_signature("错误:文件不存在") is not None
    assert error_signature("[退出码 1]\nFAIL") is not None
    assert error_signature("[退出码 0]\nok") is None
    assert error_signature("已编辑:a.py") is None


def test_context_capacity_emitted_before_model_call(monkeypatch) -> None:
    from mycode.agent import runner as runner_mod

    monkeypatch.setattr(runner_mod, "maybe_compact", lambda _provider, _messages, context_limit: False)
    provider = _ScriptedProvider([LLMResponse(text="ok", stop_reason=StopReason.END_TURN)])
    events: list = []
    res = _run(
        RunRequest(
            provider=provider,
            messages=[{"role": "user", "content": "x" * 1000}],
            max_steps=3,
            tools=[],
            run_id="rcap",
            context_limit=100,
        ),
        sinks=[_collecting_sink(events)],
    )
    assert res.status == RunStatus.COMPLETED
    caps = [e for e in events if e.type == EventType.CONTEXT_CAPACITY]
    assert len(caps) >= 1
    assert caps[0].payload["used_tokens"] > 0
    assert caps[0].payload["limit"] == 100
    assert 0 <= caps[0].payload["percent"] <= 100


def test_context_compaction_emits_compacted_and_capacity(monkeypatch) -> None:
    from mycode.agent import runner as runner_mod

    def fake_compact(provider, messages, context_limit):
        return True

    monkeypatch.setattr(runner_mod, "maybe_compact", fake_compact)
    provider = _ScriptedProvider([LLMResponse(text="ok", stop_reason=StopReason.END_TURN)])
    events: list = []
    _run(
        RunRequest(
            provider=provider,
            messages=[{"role": "user", "content": "x" * 1000}],
            max_steps=2,
            tools=[],
            run_id="rcomp",
            context_limit=100,
        ),
        sinks=[_collecting_sink(events)],
    )
    types = [e.type for e in events]
    assert EventType.CONTEXT_COMPACTED in types
    compacted_idx = types.index(EventType.CONTEXT_COMPACTED)
    assert types[compacted_idx + 1] == EventType.CHECKPOINT
    assert types[compacted_idx + 2] == EventType.CONTEXT_CAPACITY
