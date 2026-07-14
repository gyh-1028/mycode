from __future__ import annotations

import io
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from mycode.agent.runner import AgentRunner, RunRequest
from mycode.codeintel.context import ContextSelector
from mycode.codeintel.index import SymbolIndex
from mycode.codeintel.lsp import LSPClient, read_message, write_message
from mycode.config import CodeIntelConfig
from mycode.evals.fakes import FakeProvider
from mycode.llm.base import LLMResponse, StopReason, ToolCall


def test_index_build_is_incremental_and_extracts_python_symbols(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_text("import json\n\nclass App:\n    def run(self, value: int) -> int:\n        return value\n", encoding="utf-8")
    index = SymbolIndex(tmp_path)
    first = index.build()
    second = index.build()
    assert first.indexed == 1
    assert second.unchanged == 1
    symbols = index.search_symbols("run")
    assert symbols[0].name == "run"
    assert symbols[0].container == "App"
    assert index.dependencies_for("app.py") == ["json"]


def test_index_removes_deleted_files_and_skips_sensitive_paths(tmp_path: Path) -> None:
    source = tmp_path / "a.py"
    source.write_text("def visible():\n    pass\n", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=x", encoding="utf-8")
    index = SymbolIndex(tmp_path)
    index.build()
    source.unlink()
    result = index.build()
    assert result.removed == 1
    assert index.status()["files"] == 0


def test_index_closes_every_sqlite_connection(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "app.py"
    source.write_text("def run():\n    return 1\n", encoding="utf-8")
    real_connect = sqlite3.connect
    connections: list[sqlite3.Connection] = []

    def tracking_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr("mycode.codeintel.index.sqlite3.connect", tracking_connect)
    index = SymbolIndex(tmp_path)
    index.build()
    index.search_symbols("run")
    index.status()

    assert connections
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")


def test_index_updates_explicit_paths_without_repository_scan(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "app.py"
    source.write_text("def before():\n    return 1\n", encoding="utf-8")
    index = SymbolIndex(tmp_path)
    index.build()
    source.write_text("def after_change():\n    return 2\n", encoding="utf-8")
    monkeypatch.setattr(index, "discover_files", lambda: pytest.fail("unexpected full scan"))

    result = index.update_paths(["app.py"])

    assert result.indexed == 1
    assert index.search_symbols("after_change")[0].location.path == "app.py"
    assert index.search_symbols("before") == []


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
def test_index_build_uses_git_change_set_when_head_is_unchanged(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("mycode.codeintel.index._GIT_BLOB_MIN_FILES", 1)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    source = tmp_path / "app.py"
    source.write_text("def before():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=MyCode Test", "-c", "user.email=test@example.com", "commit", "-qm", "initial"],
        cwd=tmp_path,
        check=True,
    )
    index = SymbolIndex(tmp_path)
    index.build()
    source.write_text("def after_change():\n    return 200\n", encoding="utf-8")
    monkeypatch.setattr(index, "discover_files", lambda: pytest.fail("unexpected full scan"))

    result = index.build()

    assert result.indexed == 1
    assert index.search_symbols("after_change")


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
def test_git_incremental_detects_existing_untracked_file_changes(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    tracked = tmp_path / "tracked.py"
    tracked.write_text("TRACKED = True\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=MyCode Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "initial",
        ],
        cwd=tmp_path,
        check=True,
    )
    source = tmp_path / "scratch.py"
    source.write_text("def before():\n    return 1\n", encoding="utf-8")
    index = SymbolIndex(tmp_path)
    index.build()

    source.write_text("def after_change():\n    return 2\n", encoding="utf-8")
    result = index.build()

    assert result.indexed == 1
    assert index.search_symbols("after_change")
    assert index.search_symbols("before") == []


def test_context_selector_is_ephemeral_and_budgeted(tmp_path: Path) -> None:
    (tmp_path / "pricing.py").write_text(
        "def apply_discount(price: float, percent: int) -> float:\n    return price\n",
        encoding="utf-8",
    )
    messages = [{"role": "user", "content": "Fix apply_discount in pricing.py"}]
    original = [dict(message) for message in messages]
    selector = ContextSelector(tmp_path, CodeIntelConfig(max_context_tokens=1000), context_limit=10_000)
    packet = selector.select(messages)
    assert "apply_discount" in packet.content
    assert packet.estimated_tokens <= 1000
    assert messages == original
    assert packet.event_payload()["paths"] == ["pricing.py"]


def test_lsp_frame_roundtrip() -> None:
    stream = io.BytesIO()
    write_message(stream, {"jsonrpc": "2.0", "id": 1, "result": "ok"})
    stream.seek(0)
    assert read_message(stream) == {"jsonrpc": "2.0", "id": 1, "result": "ok"}


def test_lsp_subprocess_definition_references_diagnostics_and_reuse(tmp_path: Path) -> None:
    source = tmp_path / "demo.py"
    source.write_text("x = 1\n", encoding="utf-8")
    server = Path(__file__).with_name("lsp_test_server.py")
    client = LSPClient(tmp_path, "python", [sys.executable, str(server)], timeout=3)
    try:
        client.start()
        process = client._process
        assert client.definition(source, 1, 0)
        assert client.references(source, 1, 0)
        assert client.diagnostics(source)[0]["message"] == "demo warning"
        client.start()
        assert client._process is process
    finally:
        client.close()


def test_runner_tool_allowlist_denies_unexposed_tool() -> None:
    messages = [{"role": "user", "content": "q"}]
    provider = FakeProvider(
        [
            LLMResponse(
                tool_calls=[ToolCall(id="x", name="run_bash", args={"command": "echo forbidden"})],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            LLMResponse(text="done", stop_reason=StopReason.END_TURN),
        ]
    )
    result = AgentRunner().run(
        RunRequest(provider=provider, messages=messages, tools=[], allowed_tool_names=set())
    )
    assert result.status == "completed"
    tool_message = next(message for message in messages if message["role"] == "tool")
    assert "权限拒绝" in tool_message["content"]
