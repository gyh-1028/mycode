"""Runtime task planning helpers for the agent loop."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from mycode.llm.base import BaseProvider

PLANNING_MODES = {"auto", "always", "off"}
AUTO_MIN_CHARS = 28
AUTO_KEYWORDS = (
    "实现",
    "修复",
    "重构",
    "优化",
    "测试",
    "添加",
    "迁移",
    "排查",
    "调试",
    "implement",
    "fix",
    "refactor",
    "optimize",
    "test",
    "add",
    "migrate",
    "debug",
)

_BULLET_RE = re.compile(r"^\s*(?:[-*]\s+|\d+[\.)、]\s*)")


@dataclass
class PlanStep:
    text: str
    status: str = "pending"


@dataclass
class PlanState:
    steps: list[PlanStep]

    @classmethod
    def from_text(cls, plan_text: str | None, max_steps: int = 5) -> PlanState | None:
        steps = [PlanStep(text=s) for s in parse_plan_steps(plan_text, max_steps)]
        if not steps:
            return None
        steps[0].status = "in_progress"
        return cls(steps=steps)

    @property
    def total(self) -> int:
        return len(self.steps)

    @property
    def done_count(self) -> int:
        return sum(1 for step in self.steps if step.status == "done")

    def _current_index(self) -> int | None:
        for idx, step in enumerate(self.steps):
            if step.status == "in_progress":
                return idx
        for idx, step in enumerate(self.steps):
            if step.status == "pending":
                step.status = "in_progress"
                return idx
        return None

    def record_tool_result(self, *, success: bool) -> None:
        idx = self._current_index()
        if idx is None:
            return
        if not success:
            self.steps[idx].status = "in_progress"
            return
        self.steps[idx].status = "done"
        if idx + 1 < len(self.steps) and self.steps[idx + 1].status == "pending":
            self.steps[idx + 1].status = "in_progress"

    def mark_remaining_skipped(self) -> None:
        for step in self.steps:
            if step.status in {"pending", "in_progress"}:
                step.status = "skipped"

    def display_text(self) -> str:
        return "\n".join(f"{idx}. {step.text}" for idx, step in enumerate(self.steps, start=1))

    def status_text(self) -> str:
        return "\n".join(
            f"{idx}. [{step.status}] {step.text}"
            for idx, step in enumerate(self.steps, start=1)
        )

    def next_step_text(self) -> str:
        idx = self._current_index()
        if idx is None:
            return "(无,计划步骤已处理完)"
        return self.steps[idx].text

    def progress_line(self) -> str:
        return f"进度: {self.done_count}/{self.total} {self.next_step_text()}"


def normalize_planning_mode(mode: str | None) -> str:
    normalized = (mode or "auto").strip().lower()
    return normalized if normalized in PLANNING_MODES else "auto"


def latest_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content")
            return content if isinstance(content, str) else ""
    return ""


def should_plan_task(mode: str | None, task_text: str) -> bool:
    mode = normalize_planning_mode(mode)
    task = (task_text or "").strip()
    if not task or mode == "off":
        return False
    if mode == "always":
        return True
    lowered = task.lower()
    return len(task) >= AUTO_MIN_CHARS or any(keyword in lowered for keyword in AUTO_KEYWORDS)


def needs_validation(task_text: str) -> bool:
    task = (task_text or "").strip().lower()
    return any(
        keyword in task
        for keyword in (
            "实现",
            "修复",
            "重构",
            "优化",
            "测试",
            "implement",
            "fix",
            "refactor",
            "optimize",
            "test",
        )
    )


def parse_plan_steps(raw_text: str | None, max_steps: int = 5) -> list[str]:
    if not raw_text:
        return []
    limit = max(1, min(int(max_steps or 5), 10))
    steps: list[str] = []
    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.rstrip(":：").lower() in {"plan", "计划"}:
            continue
        line = _BULLET_RE.sub("", line).strip()
        if line:
            steps.append(line)
        if len(steps) >= limit:
            break
    return steps


def format_plan(raw_text: str | None, max_steps: int = 5) -> str | None:
    steps = parse_plan_steps(raw_text, max_steps)
    if not steps:
        return None
    return "\n".join(f"{idx}. {step}" for idx, step in enumerate(steps, start=1))


def create_task_plan(
    provider: BaseProvider,
    messages: list[dict[str, Any]],
    max_steps: int = 5,
) -> str | None:
    """Ask the model for a short plan without exposing tools or mutating history."""
    task = latest_user_text(messages)
    if not task.strip():
        return None

    limit = max(1, min(int(max_steps or 5), 10))
    planner_messages = [
        {
            "role": "system",
            "content": (
                "你是一个代码任务规划器。只输出简短执行计划,不要调用工具,不要解释。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"请为下面的代码任务生成 {limit} 步以内的执行计划。"
                "每步一句话,聚焦要检查、修改、验证的动作。\n\n"
                f"任务:\n{task}"
            ),
        },
    ]
    try:
        response = provider.chat(planner_messages, tools=None)
    except Exception:  # noqa: BLE001 - planning must never block the real task.
        return None
    return format_plan(response.text, limit)


def inject_plan_context(
    messages: list[dict[str, Any]],
    plan: str | PlanState | None,
    *,
    validation_required: bool = False,
) -> list[dict[str, Any]]:
    """Return a temporary message list with runtime plan/validation guidance."""
    if not plan and not validation_required:
        return messages

    content_parts: list[str] = []
    if isinstance(plan, PlanState):
        content_parts.append(
            "当前任务执行计划和状态如下。请按计划推进,但如果工具结果或代码证据显示计划不合适,"
            "可以调整并说明原因。\n"
            f"{plan.status_text()}\n\n"
            f"已完成:{plan.done_count}/{plan.total}\n"
            f"下一步:{plan.next_step_text()}"
        )
    elif isinstance(plan, str) and plan:
        content_parts.append(
            "当前任务执行计划如下。请按计划推进,但如果工具结果或代码证据显示计划不合适,"
            "可以调整并说明原因。\n"
            f"{plan}"
        )

    content_parts.append(
        "最终回答必须包含:完成内容、验证命令/结果、未完成事项。"
    )
    if validation_required:
        content_parts.append(
            "本任务需要验证:优先运行相关测试或检查;如果无法验证,最终回答必须说明原因。"
            "如果测试失败,必须根据失败输出继续修复,不要忽略失败。"
        )

    plan_message = {
        "role": "system",
        "content": "\n\n".join(content_parts),
    }
    insert_at = 0
    for idx, message in enumerate(messages):
        if message.get("role") != "system":
            break
        insert_at = idx + 1
    return [*messages[:insert_at], plan_message, *messages[insert_at:]]
