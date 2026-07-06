"""Projection of AgentEvent objects into renderable TUI state."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from mycode.agent.events import AgentEvent, EventType


@dataclass
class ActivityEntry:
    """A single row in the TUI activity list."""

    id: str
    kind: Literal["model", "plan", "tool"]
    title: str
    subtitle: str = ""
    status: str = "running"  # running / done / error
    is_error: bool = False
    duration_ms: int | None = None
    tool_call_id: str | None = None
    detail: str = ""
    tool_args: Any = None
    tool_result: Any = None


@dataclass
class TUIState:
    session_id: str = ""
    run_id: str = ""
    status: str = "idle"
    model: str = ""
    streamed_text: str = ""
    reasoning_text: str = ""
    final_text: str = ""
    plan_text: str = ""
    tool_activity: list[ActivityEntry] = field(default_factory=list)
    activity_entries: list[ActivityEntry] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    estimated_cost: float | None = None
    last_diff: str = ""
    error: str = ""

    def begin_user_turn(self) -> None:
        self.status = "running"
        self.streamed_text = ""
        self.reasoning_text = ""
        self.final_text = ""
        self.error = ""
        self.tool_activity.clear()
        self.activity_entries.clear()
        self.last_diff = ""

    def apply(self, event: AgentEvent, attachments: dict[str, Any] | None = None) -> None:
        """Project one event. Session messages remain the source of truth."""

        attachments = attachments or {}
        self.run_id = event.run_id
        payload = event.payload

        if event.type == EventType.RUN_STARTED:
            self.status = "running"
            self.model = str(payload.get("model", self.model))
        elif event.type == EventType.PLAN_CREATED:
            self.plan_text = str(payload.get("plan_text", ""))
            self.activity_entries.append(
                ActivityEntry(
                    id=f"plan-{event.seq}",
                    kind="plan",
                    title="计划",
                    subtitle=self.plan_text.splitlines()[0][:60] if self.plan_text else "",
                    status="done",
                    detail=self.plan_text,
                )
            )
        elif event.type == EventType.MODEL_CALL_STARTED:
            step = payload.get("step", "")
            self.activity_entries.append(
                ActivityEntry(
                    id=f"model-{event.seq}",
                    kind="model",
                    title="模型调用",
                    subtitle=f"step {step}" if step else "",
                    status="running",
                )
            )
        elif event.type == EventType.MODEL_STREAM_TEXT:
            self.streamed_text += str(payload.get("content", ""))
        elif event.type == EventType.MODEL_STREAM_REASONING:
            self.reasoning_text += str(payload.get("content", ""))
        elif event.type == EventType.MODEL_STREAM_END:
            for entry in reversed(self.activity_entries):
                if entry.kind == "model" and entry.status == "running":
                    entry.status = "done"
                    break
        elif event.type == EventType.MODEL_CALL_ERROR:
            detail = str(payload.get("detail", "模型调用失败"))
            for entry in reversed(self.activity_entries):
                if entry.kind == "model" and entry.status == "running":
                    entry.status = "error"
                    entry.is_error = True
                    entry.detail = detail
                    break
        elif event.type == EventType.TOOL_CALL_STARTED:
            tcid = str(payload.get("tool_call_id", event.seq))
            self.tool_activity.append(
                ActivityEntry(
                    id=f"tool-{tcid}",
                    kind="tool",
                    title=str(payload.get("name", "tool")),
                    subtitle=str(payload.get("args_preview", ""))[:80],
                    status="running",
                    tool_call_id=tcid,
                )
            )
            self.activity_entries.append(self.tool_activity[-1])
        elif event.type == EventType.TOOL_CALL_FINISHED:
            tcid = str(payload.get("tool_call_id", ""))
            target: ActivityEntry | None = None
            if tcid:
                target = next(
                    (item for item in self.tool_activity if item.tool_call_id == tcid),
                    None,
                )
            if target is None:
                target = next(
                    (item for item in reversed(self.tool_activity) if item.status == "running"),
                    None,
                )
            if target is None:
                target = ActivityEntry(id=f"tool-{event.seq}", kind="tool", title=str(payload.get("name", "tool")))
                self.tool_activity.append(target)
                self.activity_entries.append(target)
            target.status = "error" if payload.get("is_error") else "done"
            target.is_error = bool(payload.get("is_error"))
            duration_ms = payload.get("duration_ms")
            target.duration_ms = int(duration_ms) if duration_ms is not None else None
            target.tool_args = attachments.get("tool_args")
            target.tool_result = attachments.get("tool_result")
            result = attachments.get("tool_result")
            if isinstance(result, str) and result.startswith("--- a/"):
                self.last_diff = result
        elif event.type == EventType.RUN_FINISHED:
            self.status = str(payload.get("status", "completed"))
            self.final_text = str(payload.get("final_text", self.streamed_text))
        elif event.type == EventType.RUN_CANCELLED:
            self.status = "cancelled"
        elif event.type in {
            EventType.RUN_FAILED,
            EventType.RUN_STUCK,
            EventType.RUN_MAX_STEPS,
            EventType.RUN_BUDGET_EXCEEDED,
        }:
            self.status = str(payload.get("reason", event.type.rsplit(".", 1)[-1]))
            self.error = str(payload.get("detail") or payload.get("reason") or "")
        elif event.type == EventType.USAGE_REPORTED:
            self.prompt_tokens = int(payload.get("prompt_tokens", 0))
            self.completion_tokens = int(payload.get("completion_tokens", 0))
            self.cached_tokens = int(payload.get("cached_tokens", 0))
            cost = payload.get("estimated_cost")
            self.estimated_cost = float(cost) if cost is not None else None


__all__ = ["TUIState", "ActivityEntry"]
