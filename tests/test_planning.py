"""Tests for runtime task planning."""

from __future__ import annotations

import pytest

from mycode.agent.planning import (
    PlanState,
    create_task_plan,
    format_plan,
    inject_plan_context,
    latest_user_text,
    should_plan_task,
)
from mycode.llm.base import BaseProvider, LLMResponse


class PlannerProvider(BaseProvider):
    def __init__(self, response: LLMResponse | Exception) -> None:
        self.response = response
        self.tools_seen: list[list | None] = []
        self.messages_seen: list[list[dict]] = []

    def chat(self, messages, tools=None):
        self.tools_seen.append(tools)
        self.messages_seen.append([dict(m) for m in messages])
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.mark.parametrize(
    ("mode", "task", "expected"),
    [
        ("off", "fix failing tests", False),
        ("always", "hi", True),
        ("auto", "hi", False),
        ("auto", "please fix failing tests and explain the reason", True),
        ("auto", "please refactor this module", True),
        ("auto", "this task description is intentionally long enough", True),
    ],
)
def test_should_plan_task(mode: str, task: str, expected: bool) -> None:
    assert should_plan_task(mode, task) is expected


def test_should_plan_task_supports_chinese_keywords() -> None:
    assert should_plan_task("auto", "\u4fee\u590d\u6d4b\u8bd5\u5931\u8d25") is True


def test_latest_user_text_reads_last_user_message() -> None:
    messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "second"},
    ]
    assert latest_user_text(messages) == "second"


def test_format_plan_limits_and_normalizes_steps() -> None:
    text = "Plan:\n1. read code\n- edit implementation\n3) run tests\n4. update docs\n"
    assert format_plan(text, max_steps=3) == "1. read code\n2. edit implementation\n3. run tests"


def test_create_task_plan_success_uses_chat_without_tools() -> None:
    provider = PlannerProvider(LLMResponse(text="1. read code\n2. edit\n3. test"))
    messages = [{"role": "user", "content": "fix failing tests"}]

    plan = create_task_plan(provider, messages, max_steps=2)

    assert plan == "1. read code\n2. edit"
    assert provider.tools_seen == [None]
    assert "fix failing tests" in provider.messages_seen[0][-1]["content"]


def test_create_task_plan_empty_or_exception_returns_none() -> None:
    assert create_task_plan(PlannerProvider(LLMResponse(text="")), [{"role": "user", "content": "fix"}]) is None
    assert create_task_plan(PlannerProvider(RuntimeError("down")), [{"role": "user", "content": "fix"}]) is None


def test_inject_plan_context_is_temporary() -> None:
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "fix failing tests"},
    ]
    injected = inject_plan_context(messages, "1. read code")

    assert messages == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "fix failing tests"},
    ]
    assert injected[1]["role"] == "system"
    assert "1. read code" in injected[1]["content"]


def test_plan_state_tracks_progress_and_status_context() -> None:
    state = PlanState.from_text("1. read code\n2. edit\n3. test", max_steps=3)
    assert state is not None
    assert state.progress_line() == "进度: 0/3 read code"

    state.record_tool_result(success=True)
    assert state.progress_line() == "进度: 1/3 edit"
    state.record_tool_result(success=False)
    assert "[in_progress] edit" in state.status_text()
    state.record_tool_result(success=True)
    assert state.progress_line() == "进度: 2/3 test"

    state.mark_remaining_skipped()
    assert "[skipped] test" in state.status_text()


def test_inject_plan_context_includes_status_and_validation() -> None:
    state = PlanState.from_text("1. read code\n2. test", max_steps=2)
    assert state is not None
    state.record_tool_result(success=True)
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "fix"}]

    injected = inject_plan_context(messages, state, validation_required=True)

    content = injected[1]["content"]
    assert "当前任务执行计划和状态" in content
    assert "[done] read code" in content
    assert "下一步:test" in content
    assert "最终回答必须包含" in content
    assert "本任务需要验证" in content
    assert not any("当前任务执行计划和状态" in (m.get("content") or "") for m in messages)
