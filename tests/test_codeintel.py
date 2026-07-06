from __future__ import annotations

import io
import sys
from pathlib import Path

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
