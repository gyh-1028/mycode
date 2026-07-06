"""UI-agnostic agent runner: emits a stable event stream instead of printing.

This is the P5 reliability core. :class:`AgentRunner` drives the same native
tool-use loop as the legacy ``run_agent``, but instead of printing to a Rich
console it emits :class:`AgentEvent` objects to attached sinks. That makes a
run:

* **Observable** — every model call, tool call, retry, compaction and stop
  reason is a discrete, ordered event (``run_id`` + ``seq``).
* **Cancellable** — a :class:`CancellationToken` can interrupt the loop between
  steps (and between tool calls in a round) without corrupting the message list.
* **Reproducible** — a :class:`~mycode.trace.TraceWriter` persists the event
  stream as JSONL so the execution path can be replayed offline.
* **Resilient** — transient provider errors (429 / 5xx / timeouts) can be
  retried at the runner level, and a total deadline bounds a run.

The legacy ``run_agent`` is now a thin adapter (see ``loop.py``) that wires a
Rich-console event sink, so existing callers and tests keep their exact output.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mycode.codeintel.context import ContextSelector
    from mycode.trace import TraceWriter

from mycode.agent.events import (
    AgentEvent,
    EventSink,
    EventType,
    RunStatus,
    make_event,
)
from mycode.agent.planning import (
    PlanState,
    create_task_plan,
    inject_plan_context,
    latest_user_text,
    needs_validation,
    should_plan_task,
)
from mycode.context import estimate_tokens, maybe_compact
from mycode.llm.base import BaseProvider, LLMResponse, ReasoningChunk
from mycode.pricing import UsageTotals, estimate_cost_usd
from mycode.tools import dispatch_tool, get_schemas

_LOGGER = logging.getLogger("mycode.agent.loop")

# Same heuristic as before: a tool error repeated this many times in a row
# (with no successful result in between changing it) means we are stuck.
_STUCK_ERROR_REPEATS = 3

Clock = Callable[[], float]


class CancellationToken:
    """A minimal, thread-safe-enough cancellation flag for the runner.

    ``cancel()`` is set from outside (e.g. a KeyboardInterrupt handler or a
    future TUI button). The runner polls ``cancelled`` at well-defined points;
    it never raises mid-tool, so the message list stays consistent.
    """

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled


# --------------------------------------------------------------------------- #
# Transient-error classification for runner-level retry.
# --------------------------------------------------------------------------- #
def _is_transient(exc: Exception) -> bool:
    """Best-effort: should this provider error be retried?

    Matches rate limits, timeouts, connection issues and 5xx server errors by
    inspecting the exception type name and message. Auth / validation errors are
    not transient. This intentionally mirrors the user-facing hints produced by
    :func:`format_model_error`.
    """
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "ratelimit" in name or "rate_limit" in name or "429" in msg:
        return True
    if "timeout" in name or "timeout" in msg or "timed out" in msg:
        return True
    if "connection" in name or "connect" in msg or "apiconnection" in name:
        return True
    if any(code in msg for code in ("500", "502", "503", "504")) or "server error" in msg:
        return True
    if "overloaded" in msg:
        return True
    return False


def error_signature(result: str) -> str | None:
    """Extract an error signature from a tool result string (None if not an error).

    - Results prefixed with ``错误:`` (permission denial, bad args, missing file...).
    - ``run_bash`` non-zero exits (``[退出码 N]`` with N != 0).
    """
    r = result.strip()
    if r.startswith("错误:"):
        return r
    if r.startswith("[退出码 ") and not r.startswith("[退出码 0]"):
        return r
    return None


def stuck_message(reason: str, error_sig: str | None, recent_calls: list[str]) -> str:
    if reason == "repeated_edit":
        lines = ["", "⚠ 看起来卡住了,先停下来以免空转(没有真正的进展)。", "原因:连续两次提交了完全相同的 edit_file 改动。"]
    elif reason == "repeated_error":
        lines = ["", "⚠ 看起来卡住了,先停下来以免空转(没有真正的进展)。", f"原因:同一个报错连续出现 {_STUCK_ERROR_REPEATS} 次,始终没有变化。"]
    else:  # max_steps:试满步数仍未完成,也同步一下进展
        lines = ["", "试了很多步仍没能完成,先把进展同步给你。"]
    if error_sig:
        snippet = error_sig.strip()
        if len(snippet) > 500:
            snippet = snippet[:500] + "…(截断)"
        lines.append("一直卡在这个错误上:")
        lines.append(snippet)
    if recent_calls:
        lines.append("最近试过的操作:")
        lines.extend(f"  · {c}" for c in recent_calls[-5:])
    lines.append("需要你给个方向(换个思路 / 补充信息 / 指定要改的文件),再用 mycode --continue 继续。")
    return "\n".join(lines)


def assistant_message(response: LLMResponse) -> dict[str, Any]:
    """Rebuild an OpenAI-format assistant message from a normalised LLMResponse.

    Must include the whole ``tool_calls`` block (each ``arguments`` re-serialised
    to a JSON string), otherwise the model forgets it called tools next round.
    """
    message: dict[str, Any] = {"role": "assistant", "content": response.text}
    if response.tool_calls:
        # DeepSeek thinking mode requires this field to be sent back when the
        # assistant turn contains tool calls. Keep it separate from content so
        # it is never presented as the final answer.
        if response.reasoning_content:
            message["reasoning_content"] = response.reasoning_content
        message["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.args, ensure_ascii=False),
                },
            }
            for tc in response.tool_calls
        ]
    return message


def format_model_error(exc: Exception) -> str:
    name = type(exc).__name__
    detail = f"调用模型失败:{name}: {exc}"
    lower = name.lower()
    if "ratelimit" in lower or "rate_limit" in lower:
        return detail + "\n提示:可能触发了 provider 限速,请稍后重试或降低请求频率。"
    if "authentication" in lower or "permission" in lower or "unauthorized" in lower:
        return detail + "\n提示:请检查 API key、provider 类型、base_url 与模型名是否匹配。"
    if "apiConnection".lower() in lower or "timeout" in lower or "connect" in lower:
        return detail + "\n提示:请检查网络连接、base_url 和 provider 超时配置。"
    return detail


@dataclass
class RunRequest:
    """All inputs to a single agent run, UI concerns excluded."""

    provider: BaseProvider
    messages: list[dict[str, Any]]
    max_steps: int = 20
    tools: list[dict[str, Any]] | None = None
    allowed_tool_names: set[str] | None = None
    context_selector: ContextSelector | None = None
    on_progress: Callable[[], None] | None = None
    context_limit: int | None = None
    planning: str = "off"
    planning_max_steps: int = 5
    plan_only: bool = False
    mode_instruction: str | None = None
    budget_usd: float | None = None
    model: str | None = None
    pricing_overrides: dict[str, Any] | None = None

    # P5 reliability knobs (defaults preserve legacy behaviour).
    run_id: str | None = None
    cancellation_token: CancellationToken | None = None
    deadline_s: float | None = None
    retry_transient: bool = False
    max_retries: int = 0
    retry_backoff: float = 1.0


@dataclass
class AgentRunResult:
    """Structured outcome of a run."""

    run_id: str
    status: str
    final_text: str | None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    estimated_cost: float | None = None
    steps_taken: int = 0
    tool_calls: int = 0
    error: str | None = None
    events: list[AgentEvent] = field(default_factory=list)
    trace_path: str | None = None


class AgentRunner:
    """Runs an agent loop, broadcasting events to sinks + a trace writer.

    The runner is single-use: create one per :meth:`run` call. It holds mutable
    per-run state (seq counter, usage accumulators, stuck-detection windows).
    """

    def __init__(
        self,
        *,
        sinks: list[EventSink] | None = None,
        trace: TraceWriter | None = None,
        clock: Clock = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._sinks = list(sinks or [])
        self._trace = trace
        self._clock = clock
        self._sleep = sleep
        self._id_factory = id_factory or (lambda: _new_run_id())
        self._seq = 0

    def run(self, request: RunRequest) -> AgentRunResult:
        run_id = request.run_id or self._id_factory()
        # Reset seq for this run (a runner instance is single-use, but be safe).
        self._seq = 0
        result = AgentRunResult(run_id=run_id, status=RunStatus.COMPLETED, final_text=None)

        self._emit(
            run_id,
            EventType.RUN_STARTED,
            {
                "run_id": run_id,
                "max_steps": request.max_steps,
                "model": request.model or "",
            },
        )

        schemas = get_schemas() if request.tools is None else request.tools
        if request.allowed_tool_names is not None:
            schemas = [schema for schema in schemas if schema.get("name") in request.allowed_tool_names]
        out_messages = request.messages
        task_text = latest_user_text(out_messages)
        validation_required = needs_validation(task_text)
        plan_state: PlanState | None = None
        if should_plan_task(request.planning, task_text):
            task_plan = create_task_plan(request.provider, out_messages, request.planning_max_steps)
            if task_plan:
                plan_state = PlanState.from_text(task_plan, request.planning_max_steps)
                display = plan_state.display_text() if plan_state else task_plan
                self._emit(run_id, EventType.PLAN_CREATED, {"plan_text": display})
            else:
                self._emit(run_id, EventType.PLAN_FAILED, {})

        if request.plan_only:
            final_text = f"计划:\n{plan_state.display_text()}" if plan_state is not None else "未能生成可执行计划，请补充任务目标或范围后重试。"
            out_messages.append({"role": "assistant", "content": final_text})
            if request.on_progress is not None:
                request.on_progress()
            self._emit(run_id, EventType.CHECKPOINT, {})
            self._emit(
                run_id,
                EventType.RUN_FINISHED,
                {
                    "status": RunStatus.COMPLETED,
                    "final_text": final_text,
                    "final_text_len": len(final_text),
                    "plan_only": True,
                },
                attachments={"final_text": final_text},
            )
            self._emit(
                run_id,
                EventType.USAGE_REPORTED,
                {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "cached_tokens": 0,
                    "cache_write_tokens": 0,
                    "estimated_cost": None,
                },
            )
            result.final_text = final_text
            return result

        total_prompt = 0
        total_completion = 0
        total_cached = 0
        total_cache_write = 0
        budget_notice_emitted = False

        # Stuck-detection windows (accumulate across rounds).
        recent_fps: list[str] = []
        recent_calls: list[str] = []
        last_error_sig: str | None = None
        error_repeat = 0

        start_time = self._clock()

        def estimated_cost() -> float | None:
            if not request.model:
                return None
            return estimate_cost_usd(
                UsageTotals(
                    prompt_tokens=total_prompt,
                    completion_tokens=total_completion,
                    cached_tokens=total_cached,
                    cache_write_tokens=total_cache_write,
                ),
                model=request.model,
                overrides=request.pricing_overrides,
            )

        def budget_exceeded() -> bool:
            nonlocal budget_notice_emitted
            if request.budget_usd is None:
                return False
            est = estimated_cost()
            if est is None:
                if not budget_notice_emitted:
                    self._emit(
                        run_id,
                        EventType.RUN_BUDGET_EXCEEDED,
                        {
                            "estimated": None,
                            "budget": request.budget_usd,
                            "model": request.model or "",
                            "unusable": True,
                        },
                    )
                    budget_notice_emitted = True
                return False
            if est <= request.budget_usd:
                return False
            self._emit(
                run_id,
                EventType.RUN_BUDGET_EXCEEDED,
                {
                    "estimated": est,
                    "budget": request.budget_usd,
                    "model": request.model or "",
                },
            )
            return True

        def checkpoint() -> None:
            if request.on_progress is not None:
                request.on_progress()
            self._emit(run_id, EventType.CHECKPOINT, {})

        def cancelled() -> bool:
            return request.cancellation_token is not None and request.cancellation_token.cancelled

        def deadline_passed() -> bool:
            if request.deadline_s is None:
                return False
            return (self._clock() - start_time) >= request.deadline_s

        def emit_usage_terminal() -> None:
            result.prompt_tokens = total_prompt
            result.completion_tokens = total_completion
            result.cached_tokens = total_cached
            result.cache_write_tokens = total_cache_write
            result.estimated_cost = estimated_cost()
            self._emit(
                run_id,
                EventType.USAGE_REPORTED,
                {
                    "prompt_tokens": total_prompt,
                    "completion_tokens": total_completion,
                    "total_tokens": total_prompt + total_completion,
                    "cached_tokens": total_cached,
                    "cache_write_tokens": total_cache_write,
                    "estimated_cost": result.estimated_cost,
                },
            )

        step = 0
        for step in range(1, request.max_steps + 1):
            if cancelled():
                result.status = RunStatus.CANCELLED
                self._emit(run_id, EventType.RUN_CANCELLED, {"step": step})
                emit_usage_terminal()
                result.steps_taken = step - 1
                return result
            if deadline_passed():
                result.status = RunStatus.DEADLINE_EXCEEDED
                self._emit(
                    run_id,
                    EventType.RUN_FAILED,
                    {
                        "reason": RunStatus.DEADLINE_EXCEEDED,
                        "detail": f"总截止时间 {request.deadline_s}s 已到",
                    },
                )
                emit_usage_terminal()
                result.steps_taken = step - 1
                return result

            # Compress context before sending if needed (only whole turns).
            if request.context_limit and maybe_compact(request.provider, out_messages, context_limit=request.context_limit):
                self._emit(run_id, EventType.CONTEXT_COMPACTED, {})
                checkpoint()
                self._emit_capacity(run_id, out_messages, request.context_limit)
                if cancelled():
                    result.status = RunStatus.CANCELLED
                    self._emit(run_id, EventType.RUN_CANCELLED, {"step": step})
                    emit_usage_terminal()
                    result.steps_taken = step - 1
                    return result

            model_messages = inject_plan_context(
                out_messages,
                plan_state,
                validation_required=validation_required,
            )
            if request.mode_instruction:
                model_messages = list(model_messages)
                insert_at = 1 if model_messages and model_messages[0].get("role") == "system" else 0
                model_messages.insert(
                    insert_at,
                    {"role": "system", "content": request.mode_instruction},
                )
            if request.context_selector is not None:
                try:
                    packet = request.context_selector.select(out_messages)
                    if packet.content:
                        model_messages = list(model_messages)
                        insert_at = 1 if model_messages and model_messages[0].get("role") == "system" else 0
                        model_messages.insert(
                            insert_at,
                            {"role": "system", "content": packet.content},
                        )
                    self._emit(
                        run_id,
                        EventType.CONTEXT_SELECTED,
                        packet.event_payload(),
                        attachments={"selected_context": packet.content},
                    )
                except Exception as exc:  # noqa: BLE001 - context is an optional enhancement
                    _LOGGER.warning("context selection failed: %s", exc)
                    self._emit(
                        run_id,
                        EventType.CONTEXT_DEGRADED,
                        {"reason": str(exc), "error_type": type(exc).__name__},
                    )
            if request.context_limit:
                self._emit_capacity(run_id, out_messages, request.context_limit)
            self._emit(
                run_id,
                EventType.MODEL_CALL_STARTED,
                {"step": step},
                attachments={"prompt": model_messages},
            )

            try:
                response = self._call_provider_with_retry(run_id, step, request, model_messages, schemas)
            except Exception as exc:  # noqa: BLE001 - surface as a structured failure
                _LOGGER.warning("model call failed: %s", exc)
                error_text = format_model_error(exc)
                self._emit(
                    run_id,
                    EventType.MODEL_CALL_ERROR,
                    {
                        "reason": RunStatus.MODEL_ERROR,
                        "detail": error_text,
                        "error_type": type(exc).__name__,
                    },
                    attachments={},
                )
                result.status = RunStatus.MODEL_ERROR
                result.error = error_text
                emit_usage_terminal()
                result.steps_taken = step - 1
                return result

            total_prompt += response.usage.prompt_tokens
            total_completion += response.usage.completion_tokens
            total_cached += response.usage.cached_tokens
            total_cache_write += response.usage.cache_write_tokens
            _LOGGER.debug(
                "step usage prompt=%d completion=%d cached=%d cache_write=%d",
                response.usage.prompt_tokens,
                response.usage.completion_tokens,
                response.usage.cached_tokens,
                response.usage.cache_write_tokens,
            )
            self._emit(
                run_id,
                EventType.MODEL_USAGE,
                {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "cached_tokens": response.usage.cached_tokens,
                    "cache_write_tokens": response.usage.cache_write_tokens,
                },
            )

            # assistant message appended whole (with tool_calls).
            out_messages.append(assistant_message(response))

            if response.tool_calls:
                stuck_reason: str | None = None
                for tc in response.tool_calls:
                    if cancelled():
                        # Stop mid-round but keep messages consistent: the
                        # assistant turn is already appended; we simply do not
                        # execute the remaining tools. The next continue will
                        # need a tool result, so we also bail out of the run.
                        break
                    args_preview = json.dumps(tc.args, ensure_ascii=False)
                    tool_call_started_at = time.monotonic()
                    self._emit(
                        run_id,
                        EventType.TOOL_CALL_STARTED,
                        {
                            "name": tc.name,
                            "args_preview": args_preview,
                            "step": step,
                            "tool_call_id": tc.id,
                        },
                    )
                    _LOGGER.info("tool call: %s", tc.name)
                    if request.allowed_tool_names is not None and tc.name not in request.allowed_tool_names:
                        from mycode.tools.registry import ToolResult

                        content = f"错误:权限拒绝:本次运行不允许工具 {tc.name}"
                        tr = ToolResult(
                            name=tc.name,
                            args=dict(tc.args),
                            content=content,
                            is_error=True,
                            error_signature=content,
                        )
                    else:
                        tr = dispatch_tool(tc.name, tc.args)
                    if not tr.is_error and request.context_selector is not None and tc.name in {"edit_file", "write_file", "apply_patch"}:
                        request.context_selector.invalidate()
                    out_messages.append({"role": "tool", "tool_call_id": tc.id, "content": tr.content})
                    result.tool_calls += 1
                    duration_ms = int((time.monotonic() - tool_call_started_at) * 1000)
                    self._emit(
                        run_id,
                        EventType.TOOL_CALL_FINISHED,
                        {
                            "name": tc.name,
                            "result_len": len(tr.content),
                            "is_error": tr.is_error,
                            "error_signature": tr.error_signature,
                            "tool_call_id": tc.id,
                            "duration_ms": duration_ms,
                        },
                        attachments={"tool_args": tc.args, "tool_result": tr.content},
                    )
                    _LOGGER.info(
                        "tool result: %s status=%s chars=%d",
                        tc.name,
                        "error" if tr.is_error else "ok",
                        len(tr.content),
                    )

                    # --- stuck detection ---
                    fp = f"{tc.name}:{json.dumps(tc.args, sort_keys=True, ensure_ascii=False)}"
                    if tc.name == "edit_file" and recent_fps and recent_fps[-1] == fp:
                        stuck_reason = "repeated_edit"
                    recent_fps.append(fp)
                    recent_calls.append(f"{tc.name}({args_preview[:80]})")
                    if plan_state is not None:
                        plan_state.record_tool_result(success=not tr.is_error)
                        self._emit(run_id, EventType.PLAN_PROGRESS, {"line": plan_state.progress_line()})
                    if tr.is_error and tr.error_signature is not None:
                        if tr.error_signature == last_error_sig:
                            error_repeat += 1
                        else:
                            last_error_sig, error_repeat = tr.error_signature, 1
                        if error_repeat >= _STUCK_ERROR_REPEATS:
                            stuck_reason = "repeated_error"

                if cancelled():
                    result.status = RunStatus.CANCELLED
                    self._emit(run_id, EventType.RUN_CANCELLED, {"step": step})
                    emit_usage_terminal()
                    result.steps_taken = step - 1
                    return result

                checkpoint()  # tool results all paired -> consistent state

                if stuck_reason:
                    self._emit(
                        run_id,
                        EventType.RUN_STUCK,
                        {
                            "reason": stuck_reason,
                            "last_error_sig": last_error_sig,
                            "recent_calls": list(recent_calls),
                        },
                    )
                    result.status = RunStatus.STUCK
                    result.error = stuck_reason
                    emit_usage_terminal()
                    result.steps_taken = step
                    return result
                if budget_exceeded():
                    result.status = RunStatus.BUDGET_EXCEEDED
                    emit_usage_terminal()
                    result.steps_taken = step
                    return result
                continue

            # No tool calls -> final answer (text was streamed already).
            final_text = response.text or ""
            if not final_text.strip():
                self._emit(
                    run_id,
                    EventType.RUN_FINISHED,
                    {
                        "status": RunStatus.COMPLETED,
                        "final_text": "",
                        "final_text_len": 0,
                        "no_text": True,
                    },
                    attachments={"final_text": ""},
                )
            else:
                self._emit(
                    run_id,
                    EventType.RUN_FINISHED,
                    {
                        "status": RunStatus.COMPLETED,
                        "final_text": final_text,
                        "final_text_len": len(final_text),
                        "no_text": False,
                    },
                    attachments={"final_text": final_text},
                )
            if plan_state is not None:
                plan_state.mark_remaining_skipped()
                self._emit(run_id, EventType.PLAN_REMAINING_SKIPPED, {})
            checkpoint()
            budget_exceeded()  # may print a notice but we still return the text
            result.status = RunStatus.COMPLETED
            result.final_text = final_text
            emit_usage_terminal()
            result.steps_taken = step
            return result

        # Loop exhausted -> max_steps.
        checkpoint()
        self._emit(
            run_id,
            EventType.RUN_MAX_STEPS,
            {
                "max_steps": request.max_steps,
                "recent_calls": list(recent_calls),
                "last_error_sig": last_error_sig,
            },
        )
        result.status = RunStatus.MAX_STEPS
        result.error = "max_steps"
        emit_usage_terminal()
        result.steps_taken = step
        return result

    # ------------------------------------------------------------------ #
    # Provider call (with optional runner-level retry + stream consumption)
    # ------------------------------------------------------------------ #
    def _call_provider_with_retry(
        self,
        run_id: str,
        step: int,
        request: RunRequest,
        model_messages: list[dict[str, Any]],
        schemas: list[dict[str, Any]],
    ) -> LLMResponse:
        attempts = (request.max_retries + 1) if request.retry_transient else 1
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            if attempt > 1:
                self._emit(
                    run_id,
                    EventType.MODEL_CALL_RETRY,
                    {
                        "attempt": attempt,
                        "max_retries": request.max_retries,
                    },
                )
                if request.retry_backoff:
                    self._sleep(request.retry_backoff * (2 ** (attempt - 2)))
            try:
                return self._consume_stream(run_id, request.provider.stream(model_messages, schemas))
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if not request.retry_transient or attempt >= attempts or not _is_transient(exc):
                    raise
                _LOGGER.info("transient provider error, retrying: %s", exc)
        # Should be unreachable; re-raise the last error to keep semantics.
        assert last_exc is not None
        raise last_exc

    def _consume_stream(self, run_id: str, stream_gen: Any) -> LLMResponse:
        """Consume a provider stream, emitting display events for each delta.

        Mirrors the legacy ``_consume_stream``: reasoning chunks first (with a
        "思考:" prefix), then text deltas (with a separator newline if reasoning
        just ended), then a trailing newline marker. The console sink reproduces
        the exact same rendering from these events.
        """
        streamed_any = False
        reasoning_started = False
        response: LLMResponse | None = None
        try:
            while True:
                chunk = next(stream_gen)
                if isinstance(chunk, ReasoningChunk):
                    self._emit(
                        run_id,
                        EventType.MODEL_STREAM_REASONING,
                        {
                            "content": chunk.content,
                            "is_first": not reasoning_started,
                        },
                    )
                    reasoning_started = True
                elif chunk:
                    needs_separator = reasoning_started and not streamed_any
                    self._emit(
                        run_id,
                        EventType.MODEL_STREAM_TEXT,
                        {
                            "content": chunk,
                            "needs_separator": needs_separator,
                        },
                    )
                    streamed_any = True
        except StopIteration as stop:
            response = stop.value
        self._emit(
            run_id,
            EventType.MODEL_STREAM_END,
            {
                "streamed_any": streamed_any or reasoning_started,
                "reasoning_started": reasoning_started,
            },
        )
        assert response is not None
        return response

    # ------------------------------------------------------------------ #
    # Event emission
    # ------------------------------------------------------------------ #
    def _emit_capacity(
        self,
        run_id: str,
        messages: list[dict[str, Any]],
        limit: int | None,
    ) -> None:
        if limit is None:
            return
        used = estimate_tokens(messages)
        percent = min(100, int(used / limit * 100)) if limit else 0
        self._emit(
            run_id,
            EventType.CONTEXT_CAPACITY,
            {"used_tokens": used, "limit": limit, "percent": percent},
        )

    def _emit(
        self,
        run_id: str,
        type_: str,
        payload: dict[str, Any],
        *,
        attachments: dict[str, Any] | None = None,
    ) -> None:
        self._seq += 1
        event = make_event(run_id, self._seq, type_, self._clock(), payload=payload)
        atts = attachments or {}
        for sink in self._sinks:
            sink(event, atts)
        if self._trace is not None:
            self._trace.record(event, atts)


def _new_run_id() -> str:
    import uuid

    return uuid.uuid4().hex[:12]


__all__ = [
    "AgentRunner",
    "AgentRunResult",
    "CancellationToken",
    "RunRequest",
    "assistant_message",
    "error_signature",
    "format_model_error",
    "stuck_message",
]
