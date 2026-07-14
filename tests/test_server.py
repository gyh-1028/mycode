from __future__ import annotations

import io
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import jsonschema

from mycode.agent.events import EventType, RunStatus, make_event
from mycode.agent.runner import AgentRunResult
from mycode.approvals import ApprovalRequest
from mycode.runtime import RuntimeInfo
from mycode.server.stdio import RpcWriter, StdioServer
from mycode.session import Session


class _FakeRuntime:
    def __init__(self, root: Path, *, ask: bool = False) -> None:
        self.info = RuntimeInfo(root, "fake-model", "fake", ())
        self.root = root
        self.ask = ask
        self.sessions: list[Session] = []
        self.last_run_kwargs: dict = {}
        self.compacted_session: Session | None = None
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1

    def new_session(self, *, persist: bool = True) -> Session:
        session = Session.new(
            model="fake-model",
            provider="fake",
            messages=[{"role": "system", "content": "test"}],
            base_dir=self.root / ".mycode" / "sessions",
        )
        if persist:
            session.save(session.messages)
        self.sessions.insert(0, session)
        return session

    def get_session(self, session_id=None, *, persist: bool = True) -> Session:
        if session_id:
            for session in self.sessions:
                if session.id == session_id:
                    return session
            raise LookupError(f"找不到会话:{session_id}")
        return self.new_session(persist=persist)

    def compact_session(self, session: Session) -> bool:
        self.compacted_session = session
        return True

    def list_sessions(self):
        return list(self.sessions)

    def session_payload(self, session, *, include_messages=False):
        payload = {"id": session.id, "model": session.model, "provider": session.provider}
        if include_messages:
            payload["messages"] = session.messages
        return payload

    def run_prompt(self, session, prompt, *, sink, approval, run_id, **kwargs):
        self.last_run_kwargs = dict(kwargs)
        sink(make_event(run_id, 1, EventType.RUN_STARTED, 1.0, payload={"model": "fake-model"}), {})
        if self.ask:
            approved = approval(ApprovalRequest(kind="command", prompt="确认?", command="pytest", risk="read"))
            if not approved:
                return AgentRunResult(run_id=run_id, status="cancelled", final_text=None)
        sink(make_event(run_id, 2, EventType.RUN_FINISHED, 2.0, payload={"status": "completed", "final_text": "ok"}), {})
        return AgentRunResult(run_id=run_id, status=RunStatus.COMPLETED, final_text="ok")


class _BlockingRuntime(_FakeRuntime):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.entered = threading.Event()

    def run_prompt(self, session, prompt, *, cancellation_token, run_id, **kwargs):
        self.entered.set()
        for _ in range(200):
            if cancellation_token.cancelled:
                return AgentRunResult(run_id=run_id, status=RunStatus.CANCELLED, final_text=None)
            time.sleep(0.005)
        return AgentRunResult(run_id=run_id, status=RunStatus.COMPLETED, final_text="late")


class _DoubleApprovalRuntime(_FakeRuntime):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.approvals: list[bool] = []

    def run_prompt(self, session, prompt, *, sink, approval, run_id, **kwargs):
        sink(make_event(run_id, 1, EventType.RUN_STARTED, 1.0), {})
        request = ApprovalRequest(kind="command", prompt="确认?", command="pytest", risk="read")
        for _ in range(2):
            self.approvals.append(approval(request))
        sink(make_event(run_id, 2, EventType.RUN_FINISHED, 2.0, payload={"status": "completed"}), {})
        return AgentRunResult(run_id=run_id, status=RunStatus.COMPLETED, final_text="ok")


def _request(method: str, params=None, request_id=1):
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def _lines(output: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in output.getvalue().splitlines() if line]


def test_initialize_and_session_contract(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    output = io.StringIO()
    runtime = _FakeRuntime(tmp_path)
    server = StdioServer(writer=RpcWriter(output), runtime=runtime)  # type: ignore[arg-type]

    initialized = server.handle(_request("initialize", {"workspace": str(tmp_path)}))
    assert initialized["result"]["protocolVersion"] == 1
    created = server.handle(_request("session/new", request_id=2))
    assert created["result"]["session"]["model"] == "fake-model"
    listed = server.handle(_request("session/list", request_id=3))
    assert len(listed["result"]["sessions"]) == 1


def test_workspace_must_match_process_cwd(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    output = io.StringIO()
    server = StdioServer(
        writer=RpcWriter(output),
        runtime_factory=lambda **kwargs: _FakeRuntime(tmp_path),
    )
    response = server.handle(_request("initialize", {"workspace": str(tmp_path.parent)}))
    assert response["error"]["code"] == -32001


def test_run_events_and_permission_round_trip(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    output = io.StringIO()
    runtime = _FakeRuntime(tmp_path, ask=True)
    server = StdioServer(writer=RpcWriter(output), runtime=runtime, approval_timeout=2.0)  # type: ignore[arg-type]
    server.handle(_request("initialize", {"workspace": str(tmp_path)}))

    started = server.handle(_request("run/start", {"prompt": "test"}, request_id=2))
    assert started["result"]["runId"]
    approval_id = None
    for _ in range(100):
        requests = [item for item in _lines(output) if item.get("method") == "permission/request"]
        if requests:
            approval_id = requests[-1]["params"]["approvalId"]
            break
        time.sleep(0.01)
    assert approval_id is not None

    accepted = server.handle(_request("permission/respond", {"approvalId": approval_id, "approved": True}, request_id=3))
    assert accepted["result"]["accepted"] is True
    server._run_thread.join(timeout=2.0)
    messages = _lines(output)
    assert [item["params"]["type"] for item in messages if item.get("method") == "agent/event"] == [
        EventType.RUN_STARTED,
        EventType.RUN_FINISHED,
    ]
    assert any(item.get("method") == "run/result" and item["params"]["status"] == "completed" for item in messages)


def test_permission_can_be_remembered_for_current_run(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    output = io.StringIO()
    runtime = _DoubleApprovalRuntime(tmp_path)
    server = StdioServer(writer=RpcWriter(output), runtime=runtime, approval_timeout=2.0)  # type: ignore[arg-type]
    server.handle(_request("initialize", {"workspace": str(tmp_path)}))
    server.handle(_request("run/start", {"prompt": "test"}, request_id=2))

    approval_id = None
    for _ in range(100):
        requests = [item for item in _lines(output) if item.get("method") == "permission/request"]
        if requests:
            approval_id = requests[-1]["params"]["approvalId"]
            break
        time.sleep(0.01)
    assert approval_id is not None
    server.handle(
        _request(
            "permission/respond",
            {"approvalId": approval_id, "approved": True, "scope": "run"},
            request_id=3,
        )
    )
    server._run_thread.join(timeout=2.0)

    requests = [item for item in _lines(output) if item.get("method") == "permission/request"]
    assert len(requests) == 1
    assert runtime.approvals == [True, True]


def test_run_start_passes_collaboration_and_permission_modes(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    output = io.StringIO()
    runtime = _FakeRuntime(tmp_path)
    server = StdioServer(writer=RpcWriter(output), runtime=runtime)  # type: ignore[arg-type]
    server.handle(_request("initialize", {"workspace": str(tmp_path)}))

    response = server.handle(
        _request(
            "run/start",
            {
                "prompt": "review",
                "collaborationMode": "review",
                "permissionMode": "read-only",
            },
            request_id=2,
        )
    )
    assert response["result"]["runId"]
    server._run_thread.join(timeout=2.0)
    assert runtime.last_run_kwargs["collaboration_mode"] == "review"
    assert runtime.last_run_kwargs["permission_mode"] == "read-only"


def test_run_start_rejects_invalid_modes(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    server = StdioServer(
        writer=RpcWriter(io.StringIO()),
        runtime=_FakeRuntime(tmp_path),  # type: ignore[arg-type]
    )
    server.handle(_request("initialize", {"workspace": str(tmp_path)}))

    bad_collaboration = server.handle(_request("run/start", {"prompt": "x", "collaborationMode": "unknown"}))
    bad_permission = server.handle(_request("run/start", {"prompt": "x", "permissionMode": "unknown"}, request_id=2))

    assert bad_collaboration["error"]["code"] == -32602
    assert bad_permission["error"]["code"] == -32602


def test_parse_error_and_unknown_method(tmp_path) -> None:
    output = io.StringIO()
    runtime = _FakeRuntime(tmp_path)
    server = StdioServer(writer=RpcWriter(output), runtime=runtime)  # type: ignore[arg-type]
    input_stream = io.StringIO("{bad json}\n" + json.dumps(_request("unknown")) + "\n")
    server.serve(input_stream)
    responses = _lines(output)
    assert responses[0]["error"]["code"] == -32700
    assert responses[1]["error"]["code"] == -32601


def test_active_run_can_be_cancelled(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    output = io.StringIO()
    runtime = _BlockingRuntime(tmp_path)
    server = StdioServer(writer=RpcWriter(output), runtime=runtime)  # type: ignore[arg-type]
    server.handle(_request("initialize", {"workspace": str(tmp_path)}))
    started = server.handle(_request("run/start", {"prompt": "wait"}, request_id=2))
    assert runtime.entered.wait(timeout=1.0)
    cancelled = server.handle(_request("run/cancel", {"runId": started["result"]["runId"]}, request_id=3))
    assert cancelled["result"]["cancelled"] is True
    server._run_thread.join(timeout=2.0)
    assert any(message.get("method") == "run/result" and message["params"]["status"] == RunStatus.CANCELLED for message in _lines(output))


def test_active_run_rejects_session_switch(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    output = io.StringIO()
    runtime = _BlockingRuntime(tmp_path)
    session = runtime.new_session()
    server = StdioServer(writer=RpcWriter(output), runtime=runtime)  # type: ignore[arg-type]
    server.handle(_request("initialize", {"workspace": str(tmp_path)}))
    started = server.handle(_request("run/start", {"prompt": "wait", "sessionId": session.id}, request_id=2))
    assert runtime.entered.wait(timeout=1.0)

    opened = server.handle(_request("session/open", {"sessionId": session.id}, request_id=3))

    assert opened["error"]["code"] == -32010
    server.handle(_request("run/cancel", {"runId": started["result"]["runId"]}, request_id=4))
    server._run_thread.join(timeout=2.0)


def test_protocol_examples_validate_against_schema() -> None:
    schema = json.loads((Path(__file__).parents[1] / "schemas" / "protocol-v1.json").read_text(encoding="utf-8"))
    jsonschema.validate(_request("initialize", {"workspace": "C:/repo"}), schema)
    jsonschema.validate({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": 1}}, schema)
    jsonschema.validate(
        {"jsonrpc": "2.0", "method": "agent/event", "params": {"type": "run.started"}},
        schema,
    )
    jsonschema.validate(
        {
            "jsonrpc": "2.0",
            "method": "agent/event",
            "params": {
                "schema_version": 1,
                "run_id": "r1",
                "seq": 3,
                "type": "tool.call.started",
                "timestamp": 1.0,
                "payload": {"name": "read_file", "args_preview": "{}", "step": 1, "tool_call_id": "c1"},
            },
        },
        schema,
    )
    jsonschema.validate(
        {
            "jsonrpc": "2.0",
            "method": "agent/event",
            "params": {
                "schema_version": 1,
                "run_id": "r1",
                "seq": 4,
                "type": "tool.call.finished",
                "timestamp": 1.1,
                "payload": {
                    "name": "read_file",
                    "result_len": 12,
                    "is_error": False,
                    "error_signature": None,
                    "tool_call_id": "c1",
                    "duration_ms": 42,
                },
            },
        },
        schema,
    )


def test_stdio_subprocess_stdout_is_json_only(tmp_path) -> None:
    config_dir = tmp_path / ".mycode"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        'default_model = "gpt-4o-mini"\n[provider]\napi_key_env = "MYCODE_TEST_KEY"\n',
        encoding="utf-8",
    )
    payload = "\n".join(
        [
            json.dumps(_request("initialize", {"workspace": str(tmp_path)})),
            json.dumps(_request("shutdown", request_id=2)),
            "",
        ]
    )
    env = dict(__import__("os").environ)
    env["MYCODE_TEST_KEY"] = "test-not-real"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent / "src")
    process = subprocess.run(
        [sys.executable, "-m", "mycode", "serve", "--stdio"],
        cwd=tmp_path,
        env=env,
        input=payload,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    responses = [json.loads(line) for line in process.stdout.splitlines()]
    assert responses[0]["result"]["protocolVersion"] == 1
    assert responses[1]["id"] == 2
