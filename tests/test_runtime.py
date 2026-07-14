import pytest

from mycode.agent.events import EventType, RunStatus
from mycode.checkpoint import current_checkpoint
from mycode.config import Config, ConfigLoadResult, ProviderConfig
from mycode.llm.base import LLMResponse, StopReason, ToolCall
from mycode.runtime import MyCodeRuntime
from mycode.session import Session
from tests.fakes import FakeProvider


def test_new_session_includes_kimi_persona_when_provider_is_kimi(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    runtime = MyCodeRuntime(
        config_result=ConfigLoadResult(
            config=Config(
                planning="off",
                provider=ProviderConfig(type="kimi"),
                default_model="kimi-k2.7-code",
            )
        ),
        provider=FakeProvider([LLMResponse(text="done", stop_reason=StopReason.END_TURN)]),
        project_root=tmp_path,
    )
    session = runtime.new_session(persist=False)
    content = session.messages[0]["content"]
    assert "Kimi" in content
    assert "256K" in content
    assert "优先通过工具调用" in content


def test_runtime_runs_and_persists_session(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    provider = FakeProvider([LLMResponse(text="done", stop_reason=StopReason.END_TURN)])
    runtime = MyCodeRuntime(
        config_result=ConfigLoadResult(config=Config(planning="off")),
        provider=provider,
        project_root=tmp_path,
    )
    session = runtime.new_session()
    assert Session.load(session.id) is not None
    events = []
    result = runtime.run_prompt(session, "test", sink=lambda event, attachments: events.append(event))

    assert result.status == RunStatus.COMPLETED
    assert result.final_text == "done"
    assert [event.type for event in events][0] == EventType.RUN_STARTED
    loaded = runtime.get_session(session.id)
    assert loaded.messages[-1] == {"role": "assistant", "content": "done"}


def test_runtime_reuses_services_across_prompts_and_closes_once(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    provider = FakeProvider(
        [
            LLMResponse(text="first", stop_reason=StopReason.END_TURN),
            LLMResponse(text="second", stop_reason=StopReason.END_TURN),
        ]
    )
    runtime = MyCodeRuntime(
        config_result=ConfigLoadResult(config=Config(planning="off")),
        provider=provider,
        project_root=tmp_path,
    )

    class Registry:
        started = False
        starts = 0
        stops = 0

        def start(self):
            self.started = True
            self.starts += 1

        def stop(self):
            if self.started:
                self.started = False
                self.stops += 1

    registry = Registry()
    runtime._mcp_registry = registry  # type: ignore[assignment]
    closed_roots = []
    monkeypatch.setattr("mycode.runtime.close_service", lambda root: closed_roots.append(root))
    session = runtime.new_session()

    runtime.run_prompt(session, "first")
    runtime.run_prompt(session, "second")

    assert registry.starts == 1
    assert registry.stops == 0
    runtime.close()
    runtime.close()
    assert registry.stops == 1
    assert closed_roots == [tmp_path]


def test_runtime_resets_checkpoint_if_service_start_fails(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    runtime = MyCodeRuntime(
        config_result=ConfigLoadResult(config=Config(planning="off")),
        provider=FakeProvider([LLMResponse(text="unused")]),
        project_root=tmp_path,
    )

    class FailingRegistry:
        started = False

        def start(self):
            raise RuntimeError("registry failed")

        def stop(self):
            pass

    runtime._mcp_registry = FailingRegistry()  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="registry failed"):
        runtime.run_prompt(runtime.new_session(), "test")

    assert current_checkpoint() is None


def test_runtime_new_session_can_be_transient(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    runtime = MyCodeRuntime(
        config_result=ConfigLoadResult(config=Config(planning="off")),
        provider=FakeProvider([LLMResponse(text="done", stop_reason=StopReason.END_TURN)]),
        project_root=tmp_path,
    )
    session = runtime.new_session(persist=False)
    assert Session.load(session.id) is None
    transient = runtime.get_session(persist=False)
    assert Session.load(transient.id) is None
    assert transient.id != session.id or transient is not session


def test_runtime_routes_tool_approval_to_frontend(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    provider = FakeProvider(
        [
            LLMResponse(
                tool_calls=[ToolCall(id="c1", name="run_bash", args={"command": "python -c \"print('ok')\""})],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            LLMResponse(text="finished", stop_reason=StopReason.END_TURN),
        ]
    )
    runtime = MyCodeRuntime(
        config_result=ConfigLoadResult(config=Config(planning="off")),
        provider=provider,
        project_root=tmp_path,
    )
    approvals = []
    result = runtime.run_prompt(
        runtime.new_session(),
        "run test",
        approval=lambda request: approvals.append(request) or True,
    )

    assert result.final_text == "finished"
    assert approvals and approvals[0].kind == "command"


def test_runtime_plan_mode_returns_plan_without_executing_tools(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    provider = FakeProvider([LLMResponse(text="1. inspect\n2. change\n3. verify")])
    runtime = MyCodeRuntime(
        config_result=ConfigLoadResult(config=Config(planning="off")),
        provider=provider,
        project_root=tmp_path,
    )

    result = runtime.run_prompt(
        runtime.new_session(),
        "refactor this project",
        collaboration_mode="plan",
        permission_mode="full-access",
    )

    assert result.final_text == "计划:\n1. inspect\n2. change\n3. verify"
    assert provider.call_count == 1


def test_runtime_review_mode_only_exposes_read_tools(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    provider = FakeProvider([LLMResponse(text="no findings", stop_reason=StopReason.END_TURN)])
    runtime = MyCodeRuntime(
        config_result=ConfigLoadResult(config=Config(planning="off")),
        provider=provider,
        project_root=tmp_path,
    )

    result = runtime.run_prompt(
        runtime.new_session(),
        "review the current changes",
        collaboration_mode="review",
        permission_mode="full-access",
    )

    exposed = {schema["name"] for schema in provider.tools_seen[0] or []}
    assert result.final_text == "no findings"
    assert "read_file" in exposed
    assert "git_diff" in exposed
    assert "write_file" not in exposed
    assert "edit_file" not in exposed
    assert "run_bash" not in exposed
    assert any("代码审查模式" in (message.get("content") or "") for message in provider.snapshots[0])


def test_runtime_read_only_permission_blocks_write_tools(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "note.txt"
    target.write_text("before", encoding="utf-8")
    provider = FakeProvider(
        [
            LLMResponse(
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="write_file",
                        args={"path": str(target), "content": "after"},
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            LLMResponse(text="blocked", stop_reason=StopReason.END_TURN),
        ]
    )
    runtime = MyCodeRuntime(
        config_result=ConfigLoadResult(config=Config(planning="off")),
        provider=provider,
        project_root=tmp_path,
    )

    result = runtime.run_prompt(
        runtime.new_session(),
        "change note",
        collaboration_mode="default",
        permission_mode="read-only",
    )

    assert result.final_text == "blocked"
    assert target.read_text(encoding="utf-8") == "before"
    assert "write_file" not in {
        schema["name"] for schema in provider.tools_seen[0] or []
    }
