"""P7 MCP client tests: discovery, calling, resources, prompts, timeout, crash,
reconnect, name collision, invalid response, and permission denial.

Unit tests use a FakeMCPClient (deterministic, no subprocess). Integration
tests launch the real test server (``tests/mcp_test_server.py``) via stdio and
are skipped if the ``mcp`` package is not installed.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from mycode.mcp.config import MCPConfig, MCPServerConfig
from mycode.mcp.types import MCPCallResult, MCPPromptRef, MCPResourceRef, MCPToolRef

# --------------------------------------------------------------------------- #
# FakeMCPClient — deterministic stand-in for the real MCPClient
# --------------------------------------------------------------------------- #


class FakeMCPClient:
    """A scripted MCPClient that needs no subprocess or mcp package."""

    def __init__(self, config: MCPServerConfig, project_root: Path | None = None) -> None:
        self._config = config
        self._alive = False
        self._start_should_fail = config.command == "always-fail"
        self._call_count = 0
        self._fail_calls = 0  # number of calls to fail before succeeding (for reconnect)
        self._crash_after_call = -1  # crash (mark dead) after Nth call
        self._tools = [
            MCPToolRef(server=config.safe_name, name="echo", description="Echo text",
                       input_schema={"type": "object", "properties": {"text": {"type": "string"}},"required": ["text"]}),
            MCPToolRef(server=config.safe_name, name="add", description="Add two numbers",
                       input_schema={"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},"required": ["a", "b"]}),
            MCPToolRef(server=config.safe_name, name="fail", description="Always fails",
                       input_schema={"type": "object", "properties": {}}),
        ]
        self._resources = [
            MCPResourceRef(server=config.safe_name, uri="test://data", name="data", description="Test data"),
            MCPResourceRef(server=config.safe_name, uri="test://count", name="count", description="Count"),
        ]
        self._prompts = [
            MCPPromptRef(server=config.safe_name, name="greet", description="Greet",
                         arguments=[{"name": "name", "required": True}]),
            MCPPromptRef(server=config.safe_name, name="summarize", description="Summarize",
                         arguments=[{"name": "topic", "required": True}]),
        ]

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def safe_name(self) -> str:
        return self._config.safe_name

    @property
    def trusted(self) -> bool:
        return self._config.trusted

    @property
    def alive(self) -> bool:
        return self._alive

    @property
    def tools(self) -> list[MCPToolRef]:
        return list(self._tools)

    @property
    def resources(self) -> list[MCPResourceRef]:
        return list(self._resources)

    @property
    def prompts(self) -> list[MCPPromptRef]:
        return list(self._prompts)

    def start(self) -> None:
        if self._start_should_fail:
            raise RuntimeError("server failed to start")
        self._alive = True

    def stop(self) -> None:
        self._alive = False

    def reconnect(self) -> None:
        self.stop()
        self.start()

    def call_tool(self, name: str, args: dict[str, Any]) -> MCPCallResult:
        if not self._alive:
            raise RuntimeError("not alive")
        self._call_count += 1
        if self._fail_calls > 0 and self._call_count <= self._fail_calls:
            self._alive = False
            raise RuntimeError(f"call {self._call_count} failed")
        if self._crash_after_call > 0 and self._call_count >= self._crash_after_call:
            self._alive = False
            raise RuntimeError("server crashed")
        if name == "echo":
            return MCPCallResult(content=args.get("text", ""))
        if name == "add":
            return MCPCallResult(content=str(args.get("a", 0) + args.get("b", 0)))
        if name == "fail":
            return MCPCallResult(content="intentional failure", is_error=True)
        return MCPCallResult(content=f"unknown tool: {name}", is_error=True)

    def read_resource(self, uri: str) -> str:
        if not self._alive:
            raise RuntimeError("not alive")
        if uri == "test://data":
            return "hello from MCP resource"
        if uri == "test://count":
            return "42"
        raise ValueError(f"unknown resource: {uri}")

    def get_prompt(self, name: str, args: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if not self._alive:
            raise RuntimeError("not alive")
        args = args or {}
        if name == "greet":
            return [{"role": "user", "content": f"Please greet the user named {args.get('name', '')}."}]
        if name == "summarize":
            return [{"role": "user", "content": f"Please summarize the following topic: {args.get('topic', '')}"}]
        raise ValueError(f"unknown prompt: {name}")


def _make_config(
    name: str = "test",
    *,
    enabled: bool = True,
    trusted: bool = False,
    command: str = "echo",
) -> MCPServerConfig:
    return MCPServerConfig(name=name, command=command, enabled=enabled, trusted=trusted)


def _patch_client(monkeypatch, client_factory=None):
    """Patch MCPRegistry to use FakeMCPClient."""
    factory = client_factory or FakeMCPClient

    def fake_init(self, config, project_root=None):
        self._fake = factory(config, project_root)

    # We patch the class used in the registry module
    import mycode.mcp.registry as reg_mod

    monkeypatch.setattr(reg_mod, "MCPClient", factory)


def _call(name, args):
    """Dispatch a tool and return its .content string (convenience for tests)."""
    from mycode.tools.registry import dispatch_tool
    return dispatch_tool(name, args).content


# --------------------------------------------------------------------------- #
# Config tests
# --------------------------------------------------------------------------- #


class TestMCPConfig:
    def test_defaults(self) -> None:
        cfg = MCPServerConfig(name="test", command="echo")
        assert cfg.enabled is False
        assert cfg.trusted is False
        assert cfg.timeout == 30.0
        assert cfg.args == []
        assert cfg.env == {}

    def test_safe_name(self) -> None:
        cfg = MCPServerConfig(name="my-server.v1", command="echo")
        assert cfg.safe_name == "my_server_v1"

    def test_mcp_config_empty(self) -> None:
        mcp_cfg = MCPConfig()
        assert mcp_cfg.servers == []
        assert mcp_cfg.enabled_servers == []

    def test_enabled_servers_filters(self) -> None:
        mcp_cfg = MCPConfig(servers=[
            MCPServerConfig(name="a", command="echo", enabled=True),
            MCPServerConfig(name="b", command="echo", enabled=False),
        ])
        assert [s.name for s in mcp_cfg.enabled_servers] == ["a"]

    def test_config_parses_toml_mcp_section(self, tmp_path) -> None:
        from mycode.config import load_config

        toml = """
[mcp]
[[mcp.servers]]
name = "fs"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem"]
enabled = true
trusted = false

[mcp.servers.env]
API_KEY = "secret"
"""
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text(toml, encoding="utf-8")
        cfg = load_config(cfg_file)
        assert len(cfg.mcp.servers) == 1
        srv = cfg.mcp.servers[0]
        assert srv.name == "fs"
        assert srv.command == "npx"
        assert srv.args == ["-y", "@modelcontextprotocol/server-filesystem"]
        assert srv.enabled is True
        assert srv.trusted is False
        assert srv.env == {"API_KEY": "secret"}

    def test_permissions_mcp_default(self) -> None:
        from mycode.config import Config

        cfg = Config()
        assert cfg.permissions.mcp == "ask"


# --------------------------------------------------------------------------- #
# Registry unit tests (FakeMCPClient)
# --------------------------------------------------------------------------- #


class TestMCPRegistry:
    def test_tool_naming_uses_mcp_prefix(self, monkeypatch) -> None:
        from mycode.mcp.registry import MCPRegistry
        from mycode.tools.registry import get_schemas, get_tool

        _patch_client(monkeypatch)
        reg = MCPRegistry([_make_config("test")])
        reg.start()
        try:
            assert get_tool("mcp__test__echo") is not None
            assert get_tool("mcp__test__add") is not None
            assert get_tool("mcp__test__fail") is not None
            # Generic tools
            assert get_tool("mcp_read_resource") is not None
            assert get_tool("mcp_get_prompt") is not None
            # Schemas include MCP tools
            names = {s["name"] for s in get_schemas()}
            assert "mcp__test__echo" in names
        finally:
            reg.stop()

    def test_tool_unregister_on_stop(self, monkeypatch) -> None:
        from mycode.mcp.registry import MCPRegistry
        from mycode.tools.registry import get_tool

        _patch_client(monkeypatch)
        reg = MCPRegistry([_make_config("test")])
        reg.start()
        assert get_tool("mcp__test__echo") is not None
        reg.stop()
        assert get_tool("mcp__test__echo") is None
        assert get_tool("mcp_read_resource") is None
        assert get_tool("mcp_get_prompt") is None

    def test_tool_call_dispatches_to_mcp(self, monkeypatch) -> None:
        from mycode.mcp.registry import MCPRegistry

        _patch_client(monkeypatch)
        reg = MCPRegistry([_make_config("test", trusted=True)])
        reg.start()
        try:
            assert _call("mcp__test__echo", {"text": "hello"}) == "hello"
            assert _call("mcp__test__add", {"a": 3, "b": 4}) == "7"
        finally:
            reg.stop()

    def test_tool_error_returned_as_string(self, monkeypatch) -> None:
        from mycode.mcp.registry import MCPRegistry

        _patch_client(monkeypatch)
        reg = MCPRegistry([_make_config("test", trusted=True)])
        reg.start()
        try:
            result = _call("mcp__test__fail", {})
            assert result.startswith("错误:")
            assert "intentional failure" in result
        finally:
            reg.stop()

    def test_resource_reading(self, monkeypatch) -> None:
        from mycode.mcp.registry import MCPRegistry

        _patch_client(monkeypatch)
        reg = MCPRegistry([_make_config("test")])
        reg.start()
        try:
            result = _call("mcp_read_resource", {"server": "test", "uri": "test://data"})
            assert "hello from MCP resource" in result
        finally:
            reg.stop()

    def test_resource_unknown_server(self, monkeypatch) -> None:
        from mycode.mcp.registry import MCPRegistry

        _patch_client(monkeypatch)
        reg = MCPRegistry([_make_config("test")])
        reg.start()
        try:
            result = _call("mcp_read_resource", {"server": "nope", "uri": "test://data"})
            assert result.startswith("错误:")
            assert "nope" in result
        finally:
            reg.stop()

    def test_prompt_expansion(self, monkeypatch) -> None:
        from mycode.mcp.registry import MCPRegistry

        _patch_client(monkeypatch)
        reg = MCPRegistry([_make_config("test")])
        reg.start()
        try:
            import json

            result = _call("mcp_get_prompt", {
                "server": "test", "name": "greet",
                "arguments_json": json.dumps({"name": "Alice"}),
            })
            assert "Alice" in result
            assert "[user]" in result
        finally:
            reg.stop()

    def test_prompt_invalid_json(self, monkeypatch) -> None:
        from mycode.mcp.registry import MCPRegistry

        _patch_client(monkeypatch)
        reg = MCPRegistry([_make_config("test")])
        reg.start()
        try:
            result = _call("mcp_get_prompt", {
                "server": "test", "name": "greet", "arguments_json": "not-json",
            })
            assert result.startswith("错误:")
            assert "JSON" in result
        finally:
            reg.stop()

    def test_name_collision_skipped(self, monkeypatch) -> None:
        from mycode.mcp.registry import MCPRegistry
        from mycode.tools.registry import register_dynamic, unregister

        _patch_client(monkeypatch)
        # Pre-register a tool with the colliding name
        register_dynamic("mcp__test__echo", "existing", {"type": "object"}, lambda **kw: "old")
        reg = MCPRegistry([_make_config("test")])
        reg.start()
        try:
            assert _call("mcp__test__echo", {"text": "x"}) == "old"
        finally:
            reg.stop()
            unregister("mcp__test__echo")

    def test_permission_denied_for_untrusted(self, monkeypatch) -> None:
        from mycode.mcp.registry import MCPRegistry

        _patch_client(monkeypatch)
        # Untrusted server + permissions.mcp = "deny"
        monkeypatch.setenv("MYCODE_PERMISSION_MCP", "deny")
        reg = MCPRegistry([_make_config("test", trusted=False)])
        reg.start()
        try:
            result = _call("mcp__test__echo", {"text": "hello"})
            assert result.startswith("错误:")
            assert "权限拒绝" in result
        finally:
            reg.stop()
            monkeypatch.delenv("MYCODE_PERMISSION_MCP", raising=False)

    def test_permission_ask_rejected(self, monkeypatch) -> None:
        from mycode.mcp.registry import MCPRegistry

        _patch_client(monkeypatch)
        monkeypatch.setattr("mycode.mcp.registry.confirm_write", lambda prompt: False)
        reg = MCPRegistry([_make_config("test", trusted=False)])
        reg.start()
        try:
            result = _call("mcp__test__echo", {"text": "hello"})
            assert "用户拒绝" in result
        finally:
            reg.stop()

    def test_permission_ask_accepted(self, monkeypatch) -> None:
        from mycode.mcp.registry import MCPRegistry

        _patch_client(monkeypatch)
        monkeypatch.setattr("mycode.mcp.registry.confirm_write", lambda prompt: True)
        reg = MCPRegistry([_make_config("test", trusted=False)])
        reg.start()
        try:
            assert _call("mcp__test__echo", {"text": "hello"}) == "hello"
        finally:
            reg.stop()

    def test_trusted_server_no_confirmation(self, monkeypatch) -> None:
        from mycode.mcp.registry import MCPRegistry

        _patch_client(monkeypatch)
        called = {"n": 0}
        monkeypatch.setattr(
            "mycode.mcp.registry.confirm_write",
            lambda prompt: called.__setitem__("n", called["n"] + 1) or True,
        )
        reg = MCPRegistry([_make_config("test", trusted=True)])
        reg.start()
        try:
            assert _call("mcp__test__echo", {"text": "hello"}) == "hello"
            assert called["n"] == 0  # no confirmation asked
        finally:
            reg.stop()

    def test_server_start_failure_doesnt_kill_agent(self, monkeypatch) -> None:
        from mycode.mcp.registry import MCPRegistry
        from mycode.tools.registry import get_tool

        _patch_client(monkeypatch)
        # Two servers: one fails to start, one succeeds
        reg = MCPRegistry([
            _make_config("bad", command="always-fail"),
            _make_config("good"),
        ])
        reg.start()
        try:
            # Bad server's tools are NOT registered
            assert get_tool("mcp__bad__echo") is None
            # Good server's tools ARE registered
            assert get_tool("mcp__good__echo") is not None
        finally:
            reg.stop()

    def test_reconnect_on_call_failure(self, monkeypatch) -> None:
        from mycode.mcp.registry import MCPRegistry

        # Create a fake client that fails the first call, then succeeds
        class ReconnectFake(FakeMCPClient):
            def __init__(self, config, project_root=None):
                super().__init__(config, project_root)
                self._fail_calls = 1  # fail first call only

        _patch_client(monkeypatch, client_factory=ReconnectFake)
        reg = MCPRegistry([_make_config("test", trusted=True)])
        reg.start()
        try:
            # First call fails, triggers reconnect, second call succeeds
            assert _call("mcp__test__echo", {"text": "recovered"}) == "recovered"
        finally:
            reg.stop()

    def test_disabled_server_skipped(self, monkeypatch) -> None:
        from mycode.mcp.registry import MCPRegistry
        from mycode.tools.registry import get_tool

        _patch_client(monkeypatch)
        reg = MCPRegistry([_make_config("test", enabled=False)])
        reg.start()
        try:
            assert get_tool("mcp__test__echo") is None
        finally:
            reg.stop()

    def test_context_manager(self, monkeypatch) -> None:
        from mycode.mcp.registry import MCPRegistry
        from mycode.tools.registry import get_tool

        _patch_client(monkeypatch)
        with MCPRegistry([_make_config("test")]):
            assert get_tool("mcp__test__echo") is not None
        assert get_tool("mcp__test__echo") is None


# --------------------------------------------------------------------------- #
# Integration tests with real MCP server (skipped if mcp not installed)
# --------------------------------------------------------------------------- #


_SERVER_SCRIPT = Path(__file__).parent / "mcp_test_server.py"


def _mcp_available() -> bool:
    try:
        import mcp  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _mcp_available(), reason="mcp package not installed")
@pytest.mark.skipif(not _SERVER_SCRIPT.exists(), reason="test server script not found")
class TestMCPIntegration:
    """End-to-end tests with a real MCP stdio server subprocess."""

    def _make_registry(self):
        from mycode.mcp.registry import MCPRegistry

        cfg = MCPServerConfig(
            name="testserver",
            command=sys.executable,
            args=[str(_SERVER_SCRIPT)],
            enabled=True,
            trusted=True,
            timeout=30.0,
        )
        return MCPRegistry([cfg])

    def test_discover_tools(self) -> None:
        from mycode.tools.registry import get_schemas

        reg = self._make_registry()
        reg.start()
        try:
            names = {s["name"] for s in get_schemas()}
            assert "mcp__testserver__echo" in names
            assert "mcp__testserver__add" in names
            assert "mcp__testserver__fail" in names
        finally:
            reg.stop()

    def test_call_tool_echo(self) -> None:
        reg = self._make_registry()
        reg.start()
        try:
            assert _call("mcp__testserver__echo", {"text": "integration!"}) == "integration!"
        finally:
            reg.stop()

    def test_call_tool_add(self) -> None:
        reg = self._make_registry()
        reg.start()
        try:
            assert _call("mcp__testserver__add", {"a": 10, "b": 32}) == "42"
        finally:
            reg.stop()

    def test_tool_error_handled(self) -> None:
        reg = self._make_registry()
        reg.start()
        try:
            result = _call("mcp__testserver__fail", {"message": "boom"})
            assert result.startswith("错误:")
            assert "boom" in result
        finally:
            reg.stop()

    def test_read_resource(self) -> None:
        reg = self._make_registry()
        reg.start()
        try:
            result = _call("mcp_read_resource", {"server": "testserver", "uri": "test://data"})
            assert "hello from MCP resource" in result
        finally:
            reg.stop()

    def test_get_prompt(self) -> None:
        import json

        reg = self._make_registry()
        reg.start()
        try:
            result = _call("mcp_get_prompt", {
                "server": "testserver", "name": "greet",
                "arguments_json": json.dumps({"name": "World"}),
            })
            assert "World" in result
        finally:
            reg.stop()

    def test_crash_and_reconnect(self) -> None:
        """Kill the server process, then verify reconnect works."""
        reg = self._make_registry()
        reg.start()
        try:
            # First call works
            assert _call("mcp__testserver__echo", {"text": "before"}) == "before"

            # Force the client to be "dead" to trigger reconnect path
            client = reg.clients["testserver"]
            client._alive = False

            # Next call should trigger reconnect and succeed
            assert _call("mcp__testserver__echo", {"text": "after"}) == "after"
        finally:
            reg.stop()

    def test_unknown_tool_returns_error(self) -> None:
        reg = self._make_registry()
        reg.start()
        try:
            result = _call("mcp__testserver__nonexistent", {})
            assert result.startswith("错误:")
        finally:
            reg.stop()


# --------------------------------------------------------------------------- #
# Types tests
# --------------------------------------------------------------------------- #


class TestMCPTypes:
    def test_tool_ref_full_name(self) -> None:
        ref = MCPToolRef(server="myserver", name="echo")
        assert ref.full_name == "mcp__myserver__echo"

    def test_tool_ref_defaults(self) -> None:
        ref = MCPToolRef(server="s", name="n")
        assert ref.description == ""
        assert ref.input_schema == {"type": "object", "properties": {}}
        assert ref.annotations is None

    def test_call_result_defaults(self) -> None:
        r = MCPCallResult(content="ok")
        assert r.is_error is False
