from __future__ import annotations

from pathlib import Path

import pytest

from mycode.agent.events import EventType, RunStatus, make_event
from mycode.agent.runner import AgentRunResult
from mycode.approvals import ApprovalRequest
from mycode.runtime import RuntimeInfo
from mycode.session import Session
from mycode.tui.app import MyCodeTUI
from mycode.tui.state import TUIState


def _event(seq: int, type_: str, payload: dict | None = None):
    return make_event("run-1", seq, type_, float(seq), payload=payload)


class _FakeRuntime:
    """Minimal runtime stand-in that drives the TUI through deterministic events."""

    def __init__(
        self,
        root: Path,
        *,
        ask: bool = False,
        event_script: list[tuple[int, str, dict]] | None = None,
    ) -> None:
        self.info = RuntimeInfo(root, "fake-model", "fake", ())
        self._root = root
        self._sessions: list[Session] = []
        self.ask = ask
        self.approved: bool | None = None
        self.event_script = event_script
        self.new_session_calls: list[bool] = []

    def new_session(self, *, persist: bool = True) -> Session:
        session = Session.new(
            model="fake-model",
            provider="fake",
            messages=[{"role": "system", "content": "test"}],
            base_dir=self._root / ".mycode" / "sessions",
        )
        if persist:
            session.save(session.messages)
            self._sessions.insert(0, session)
        self.new_session_calls.append(persist)
        return session

    def get_session(self, session_id: str | None = None, *, persist: bool = True) -> Session:
        if session_id:
            return next(session for session in self._sessions if session.id == session_id)
        return self.new_session(persist=persist)

    def list_sessions(self) -> list[Session]:
        return list(self._sessions)

    def run_prompt(self, session, prompt, *, sink, cancellation_token, approval, **kwargs):
        session.messages.append({"role": "user", "content": prompt})

        script = self.event_script or [
            (1, EventType.RUN_STARTED, {"model": "fake-model"}),
            (2, EventType.MODEL_STREAM_TEXT, {"content": "完成"}),
            (3, EventType.MODEL_STREAM_END, {"streamed_any": True}),
            (4, EventType.RUN_FINISHED, {"status": "completed", "final_text": "完成"}),
            (5, EventType.USAGE_REPORTED, {"prompt_tokens": 2, "completion_tokens": 1}),
        ]
        for item in script:
            if len(item) == 4:
                seq, type_, payload, attachments = item
            else:
                seq, type_, payload = item
                attachments = {}
            sink(_event(seq, type_, payload), attachments)

        if self.ask:
            self.approved = approval(
                ApprovalRequest(kind="command", prompt="运行测试?", command="pytest", risk="read")
            )

        session.messages.append({"role": "assistant", "content": "完成"})
        session.save(session.messages)
        return AgentRunResult(run_id="run-1", status=RunStatus.COMPLETED, final_text="完成")


def test_tui_state_projects_agent_events() -> None:
    state = TUIState(model="test-model")
    state.apply(_event(1, EventType.RUN_STARTED, {"model": "test-model"}))
    state.apply(_event(2, EventType.MODEL_STREAM_TEXT, {"content": "done"}))
    state.apply(_event(3, EventType.TOOL_CALL_STARTED, {"name": "read_file", "args_preview": "{}"}))
    state.apply(_event(4, EventType.TOOL_CALL_FINISHED, {"name": "read_file", "result_len": 2, "is_error": False}))
    state.apply(_event(5, EventType.RUN_FINISHED, {"status": "completed", "final_text": "done"}))
    state.apply(_event(6, EventType.USAGE_REPORTED, {"prompt_tokens": 10, "completion_tokens": 3, "cached_tokens": 2, "estimated_cost": 0.01}))

    assert state.status == "completed"
    assert state.final_text == "done"
    assert state.tool_activity[0].status == "done"
    assert state.prompt_tokens == 10
    assert state.estimated_cost == 0.01


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(120, 40), (80, 24)])
async def test_tui_mounts_and_runs_prompt(tmp_path, size) -> None:
    runtime = _FakeRuntime(tmp_path)
    app = MyCodeTUI(runtime)  # type: ignore[arg-type]
    async with app.run_test(size=size) as pilot:
        composer = app.query_one("#composer")
        composer.load_text("修复测试")
        await pilot.press("ctrl+enter")
        for _ in range(20):
            await pilot.pause(0.05)
            if app.state.status == "completed" and app.query_one("#send").disabled is False:
                break
        assert app.state.final_text == "完成"
        assert app.query_one("#send").disabled is False
        assert len(app.query(".user-message")) == 1
        assert len(app.query(".assistant-message")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("size", "sessions_display", "details_display"),
    [
        ((150, 45), "block", "block"),
        ((100, 32), "none", "block"),
        ((80, 24), "none", "none"),
    ],
)
async def test_tui_responsive_panels(
    tmp_path, size, sessions_display, details_display
) -> None:
    app = MyCodeTUI(_FakeRuntime(tmp_path))  # type: ignore[arg-type]
    async with app.run_test(size=size):
        assert app.query_one("#sessions-pane").styles.display == sessions_display
        assert app.query_one("#details-pane").styles.display == details_display


@pytest.mark.asyncio
async def test_tui_permission_modal_resolves_worker(tmp_path) -> None:
    runtime = _FakeRuntime(tmp_path, ask=True)
    app = MyCodeTUI(runtime)  # type: ignore[arg-type]
    async with app.run_test(size=(120, 40)) as pilot:
        app.query_one("#composer").load_text("运行测试")
        await pilot.press("ctrl+enter")
        for _ in range(20):
            await pilot.pause(0.05)
            if app.screen.query("#approve"):
                break
        await pilot.click("#approve")
        for _ in range(20):
            await pilot.pause(0.05)
            if runtime.approved is not None and app.query_one("#send").disabled is False:
                break
        assert runtime.approved is True


@pytest.mark.asyncio
async def test_tui_cancel_rejects_pending_approval(tmp_path) -> None:
    runtime = _FakeRuntime(tmp_path, ask=True)
    app = MyCodeTUI(runtime)  # type: ignore[arg-type]
    async with app.run_test(size=(120, 40)) as pilot:
        app.query_one("#composer").load_text("运行测试")
        await pilot.press("ctrl+enter")
        for _ in range(20):
            await pilot.pause(0.05)
            if app.screen.query("#approve"):
                break
        await pilot.click("#deny")
        for _ in range(20):
            await pilot.pause(0.05)
            if app.state.status == "completed" and app.query_one("#send").disabled is False:
                break
        assert runtime.approved is False
        assert app.query_one("#send").disabled is False


@pytest.mark.asyncio
async def test_tui_streaming_reasoning_and_activity(tmp_path) -> None:
    event_script = [
        (1, EventType.RUN_STARTED, {"model": "fake-model"}),
        (2, EventType.MODEL_CALL_STARTED, {"step": 1}),
        (3, EventType.MODEL_STREAM_REASONING, {"content": "think step"}),
        (4, EventType.MODEL_STREAM_TEXT, {"content": "answer"}),
        (5, EventType.TOOL_CALL_STARTED, {"name": "read_file", "args_preview": "{}", "tool_call_id": "t1"}),
        (6, EventType.TOOL_CALL_FINISHED, {"name": "read_file", "result_len": 2, "is_error": False, "tool_call_id": "t1", "duration_ms": 42}),
        (7, EventType.MODEL_STREAM_END, {}),
        (8, EventType.RUN_FINISHED, {"status": "completed", "final_text": "answer"}),
        (9, EventType.USAGE_REPORTED, {"prompt_tokens": 10, "completion_tokens": 2}),
    ]
    runtime = _FakeRuntime(tmp_path, event_script=event_script)
    app = MyCodeTUI(runtime)  # type: ignore[arg-type]
    async with app.run_test(size=(120, 40)) as pilot:
        app.query_one("#composer").load_text("stream test")
        await pilot.press("ctrl+enter")
        for _ in range(30):
            await pilot.pause(0.05)
            if app.state.status == "completed" and not app.query_one("#send").disabled:
                break

        md = app.query_one("#transcript Markdown")
        assert md is not None
        assert app.state.streamed_text == "answer"

        reasoning = app._reasoning_collapsible
        assert reasoning.styles.display == "block"
        assert app.state.reasoning_text == "think step"

        assert len(app.state.activity_entries) >= 2
        titles = [entry.title for entry in app.state.activity_entries]
        assert "模型调用" in titles
        assert "read_file" in titles


@pytest.mark.asyncio
async def test_tui_new_session_is_transient(tmp_path) -> None:
    runtime = _FakeRuntime(tmp_path)
    app = MyCodeTUI(runtime)  # type: ignore[arg-type]
    async with app.run_test(size=(120, 40)) as pilot:
        original_id = app.session.id
        original_count = len(runtime.list_sessions())
        await pilot.press("ctrl+n")
        assert app.session.id != original_id
        assert len(runtime.list_sessions()) == original_count
        assert runtime.new_session_calls[-1] is False


@pytest.mark.asyncio
async def test_tui_activity_detail_redacted_and_capped(tmp_path, monkeypatch) -> None:
    secret = "sk-live-12345"
    long_output = "x" * 25000
    event_script = [
        (1, EventType.RUN_STARTED, {"model": "fake-model"}),
        (2, EventType.TOOL_CALL_STARTED, {"name": "run_bash", "args_preview": f"echo {secret}", "tool_call_id": "t1"}),
        (
            3,
            EventType.TOOL_CALL_FINISHED,
            {"name": "run_bash", "result_len": len(long_output), "is_error": False, "tool_call_id": "t1", "duration_ms": 10},
            {"tool_args": {"command": f"echo {secret}"}, "tool_result": long_output},
        ),
        (4, EventType.RUN_FINISHED, {"status": "completed", "final_text": "done"}),
    ]
    runtime = _FakeRuntime(tmp_path, event_script=event_script)
    app = MyCodeTUI(runtime)  # type: ignore[arg-type]
    async with app.run_test(size=(120, 40)) as pilot:
        app.query_one("#composer").load_text("redact")
        await pilot.press("ctrl+enter")
        for _ in range(30):
            await pilot.pause(0.05)
            if app.state.status == "completed":
                break

        entry = app.state.activity_entries[0]
        detail_text = app._format_activity_detail(entry)
        assert secret not in detail_text
        assert "[REDACTED]" in detail_text
        assert len(detail_text) < len(long_output)


@pytest.mark.asyncio
async def test_tui_config_wizard_creates_runtime(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    app = MyCodeTUI(runtime=None, config_error="缺少 API key")  # type: ignore[arg-type]
    async with app.run_test(size=(120, 40)) as pilot:
        for _ in range(20):
            await pilot.pause(0.05)
            if app.screen.query("#wizard-save"):
                break
        app.screen.query_one("#key-input").value = "fake-key"
        await pilot.click("#wizard-save")
        for _ in range(30):
            await pilot.pause(0.05)
            if app.runtime is not None:
                break
        assert app.runtime is not None
        assert app.screen.query_one("#composer") is not None
