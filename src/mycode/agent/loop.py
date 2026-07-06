"""Agent loop: backward-compatible Rich console adapter over :class:`AgentRunner`.

Historically ``run_agent`` drove the tool-use loop and printed directly to a
Rich console. P5 factors the UI-agnostic core into ``agent/runner.py`` (which
emits :class:`~mycode.agent.events.AgentEvent` objects) and keeps this module as
the familiar entry point: it builds a console event sink that reproduces the
exact same output, runs the :class:`AgentRunner`, and returns the final text
(or ``None``) just like before.

All previously public helpers (``_assistant_message``, ``_format_model_error``,
``_error_signature``) are re-exported from ``runner`` so existing imports keep
working.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rich.console import Console

from mycode.agent.events import AgentEvent, EventType
from mycode.agent.runner import (
    AgentRunner,
    RunRequest,
)
from mycode.agent.runner import (
    assistant_message as _assistant_message,
)
from mycode.agent.runner import (
    error_signature as _error_signature,
)
from mycode.agent.runner import (
    format_model_error as _format_model_error,
)
from mycode.agent.runner import (
    stuck_message as _stuck_message,
)
from mycode.ui import print_reasoning_chunk, print_stream_chunk, print_usage

_DEFAULT_CONSOLE = Console()


class _RichConsoleSink:
    """Translates AgentEvents into the same Rich output the old loop produced.

    Holds per-run display state (streaming flags) that resets on
    ``run.started``. Attach one instance per run.
    """

    def __init__(self, console: Console) -> None:
        self._out = console
        self._reasoning_started = False
        self._streamed_any = False

    def __call__(self, event: AgentEvent, attachments: dict[str, Any]) -> None:  # noqa: ARG002
        t = event.type
        p = event.payload
        out = self._out

        if t == EventType.RUN_STARTED:
            self._reasoning_started = False
            self._streamed_any = False
            return
        if t == EventType.PLAN_CREATED:
            out.print("计划:", style="cyan", markup=False)
            out.print(p.get("plan_text", ""), markup=False)
            return
        if t == EventType.PLAN_FAILED:
            out.print("(计划生成失败或为空,继续执行原任务)", style="yellow", markup=False)
            return
        if t == EventType.CONTEXT_COMPACTED:
            out.print("(已将较早的对话压缩为摘要以节省上下文)", style="yellow", markup=False)
            return
        if t == EventType.MODEL_STREAM_REASONING:
            print_reasoning_chunk(
                p.get("content", ""),
                is_first=p.get("is_first", False),
                console=out,
            )
            self._reasoning_started = True
            return
        if t == EventType.MODEL_STREAM_TEXT:
            if p.get("needs_separator") and self._reasoning_started and not self._streamed_any:
                out.print()  # 思考链和最终答案分行显示
            print_stream_chunk(p.get("content", ""), console=out)
            self._streamed_any = True
            return
        if t == EventType.MODEL_STREAM_END:
            if p.get("streamed_any"):
                out.print()  # 流式文本/思考链结束后补一个换行
            return
        if t == EventType.MODEL_CALL_ERROR:
            out.print(p.get("detail", ""), style="red", markup=False)
            return
        if t == EventType.TOOL_CALL_STARTED:
            out.print(
                f"· 调用 {p.get('name', '')}({p.get('args_preview', '')})",
                style="cyan",
                markup=False,
            )
            return
        if t == EventType.TOOL_CALL_FINISHED:
            out.print(f"  ↳ 返回 {p.get('result_len', 0)} 字符", style="dim", markup=False)
            return
        if t == EventType.PLAN_PROGRESS:
            out.print(p.get("line", ""), style="green", markup=False)
            return
        if t == EventType.RUN_STUCK:
            out.print(
                _stuck_message(
                    p.get("reason", ""),
                    p.get("last_error_sig"),
                    p.get("recent_calls", []),
                ),
                style="yellow",
                markup=False,
            )
            return
        if t == EventType.RUN_MAX_STEPS:
            out.print(
                f"已达到最大步数 {p.get('max_steps', 0)},仍未得到最终回答,提前停止。",
                style="yellow",
                markup=False,
            )
            out.print(
                _stuck_message(
                    "max_steps",
                    p.get("last_error_sig"),
                    p.get("recent_calls", []),
                ),
                style="yellow",
                markup=False,
            )
            return
        if t == EventType.RUN_FINISHED:
            if p.get("no_text"):
                out.print("(模型没有返回文本)", markup=False)
            return
        if t == EventType.RUN_BUDGET_EXCEEDED:
            if p.get("unusable"):
                model = p.get("model") or "(未知)"
                out.print(
                    f"(模型 {model} 没有价格表,无法执行 --budget ${p.get('budget')};仅显示 token 用量)",
                    style="yellow",
                    markup=False,
                )
            else:
                est = p.get("estimated")
                out.print(
                    f"预算已超过:估算 ${est:.6f} > 上限 ${p.get('budget'):.6f},已在一致状态点停止。",
                    style="yellow",
                    markup=False,
                )
            return
        if t == EventType.USAGE_REPORTED:
            print_usage(
                p.get("prompt_tokens", 0),
                p.get("completion_tokens", 0),
                p.get("total_tokens", 0),
                console=out,
                cached_tokens=p.get("cached_tokens", 0),
                estimated_cost=p.get("estimated_cost"),
            )
            return
        # Events with no console rendering (model.call.started, model.usage,
        # checkpoint, run.cancelled, run.failed, plan.remaining_skipped, retry)
        # are intentionally silent to preserve the old output exactly.


def run_agent(
    provider: Any,
    messages: list[dict[str, Any]],
    max_steps: int = 20,
    *,
    tools: list[dict[str, Any]] | None = None,
    console: Console | None = None,
    on_progress: Callable[[], None] | None = None,
    context_limit: int | None = None,
    planning: str = "off",
    planning_max_steps: int = 5,
    budget_usd: float | None = None,
    model: str | None = None,
    pricing_overrides: dict[str, Any] | None = None,
) -> str | None:
    """让 provider 自主调用工具直到给出最终回答或达到步数上限。

    兼容适配器:内部委托给 :class:`AgentRunner`,用 Rich 控制台事件 sink 复现旧的
    输出。返回最终回答文本;达到 max_steps 仍未结束、出错、卡住或超预算时返回 None。
    messages 会被原地追加(assistant 整块 + 每个工具结果)。on_progress 在每个一致
    状态点被调用,供会话持久化即时存档。
    """
    out = console or _DEFAULT_CONSOLE
    sink = _RichConsoleSink(out)
    runner = AgentRunner(sinks=[sink])
    request = RunRequest(
        provider=provider,
        messages=messages,
        max_steps=max_steps,
        tools=tools,
        on_progress=on_progress,
        context_limit=context_limit,
        planning=planning,
        planning_max_steps=planning_max_steps,
        budget_usd=budget_usd,
        model=model,
        pricing_overrides=pricing_overrides,
    )
    result = runner.run(request)
    return result.final_text


__all__ = ["run_agent", "_assistant_message", "_error_signature", "_format_model_error"]
