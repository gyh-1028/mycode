"""MCP registry: manage all configured servers and bridge them into mycode's tool registry.

The registry is the integration point between MCP servers and the agent:

* On :meth:`start`, it connects to each enabled server, discovers its tools /
  resources / prompts, and registers each tool into the global
  :mod:`mycode.tools.registry` under the namespaced name ``mcp__<server>__<tool>``.
* Resource reading and prompt expansion are exposed as two generic built-in
  tools (``mcp_read_resource`` and ``mcp_get_prompt``) that take the server
  name as a parameter.
* On :meth:`stop`, it unregisters every MCP tool and closes all sessions.

Permission model (per the P7 roadmap):

* **Untrusted servers** (default): every tool call goes through the
  ``permissions.mcp`` confirmation flow — even tools whose annotations claim
  ``readOnlyHint``. Annotations are hints, never authority.
* **Trusted servers**: tool calls proceed without confirmation, but errors are
  still caught and returned as ``错误:`` strings.

Server failures (crash, timeout, invalid response) never terminate the agent:
the wrapper returns an error string and attempts one reconnection before
giving up.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from mycode.approvals import ApprovalRequest, decide, effective_permission
from mycode.mcp.client import MCPClient
from mycode.mcp.config import MCPServerConfig
from mycode.tools.registry import get_tool, register_dynamic, unregister
from mycode.ui import confirm_write

_LOGGER = logging.getLogger("mycode.mcp.registry")

_MCP_TOOL_PREFIX = "mcp__"
_RESOURCE_TOOL = "mcp_read_resource"
_PROMPT_TOOL = "mcp_get_prompt"


class MCPRegistry:
    """Manages all configured MCP servers for a single agent run."""

    def __init__(
        self,
        servers: list[MCPServerConfig],
        project_root: Path | None = None,
    ) -> None:
        self._configs = servers
        self._project_root = project_root or Path.cwd()
        self._clients: dict[str, MCPClient] = {}
        self._registered: set[str] = set()
        self._started = False

    @property
    def started(self) -> bool:
        return self._started

    @property
    def clients(self) -> dict[str, MCPClient]:
        return dict(self._clients)

    # -- context manager --------------------------------------------------- #
    def __enter__(self) -> MCPRegistry:
        self.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.stop()

    # -- lifecycle --------------------------------------------------------- #
    def start(self) -> None:
        """Connect to all enabled servers and register their tools."""
        if self._started:
            return
        self._started = True
        for cfg in self._configs:
            if not cfg.enabled:
                _LOGGER.info("MCP server %s 已禁用,跳过", cfg.name)
                continue
            self._start_server(cfg)

        # Register generic resource / prompt tools if any server is alive.
        if self._clients:
            self._register_generic_tools()

    def _start_server(self, cfg: MCPServerConfig) -> None:
        client = MCPClient(cfg, self._project_root)
        try:
            client.start()
        except Exception as exc:
            # Server failure must not terminate the agent.
            _LOGGER.warning("MCP server %s 启动失败: %s: %s", cfg.name, type(exc).__name__, exc)
            return
        self._clients[client.safe_name] = client
        self._register_server_tools(client)

    def stop(self) -> None:
        """Close all sessions and unregister MCP tools."""
        if not self._started:
            return
        for name in list(self._registered):
            unregister(name)
        self._registered.clear()
        for client in self._clients.values():
            try:
                client.stop()
            except Exception as exc:
                _LOGGER.warning("MCP server %s 停止失败: %s", client.name, exc)
        self._clients.clear()
        self._started = False

    # -- tool registration ------------------------------------------------- #
    def _register_server_tools(self, client: MCPClient) -> None:
        server = client.safe_name
        for tool in client.tools:
            full_name = tool.full_name
            if get_tool(full_name) is not None:
                _LOGGER.warning("MCP 工具名冲突,跳过: %s", full_name)
                continue
            try:
                register_dynamic(
                    name=full_name,
                    description=f"[MCP:{server}] {tool.description}" if tool.description else f"[MCP:{server}] {tool.name}",
                    parameters=tool.input_schema,
                    func=self._make_tool_wrapper(client, tool.name, server),
                )
                self._registered.add(full_name)
            except ValueError:
                _LOGGER.warning("MCP 工具注册失败(名冲突): %s", full_name)

    def _register_generic_tools(self) -> None:
        """Register mcp_read_resource and mcp_get_prompt (if not already present)."""
        if get_tool(_RESOURCE_TOOL) is None:
            register_dynamic(
                name=_RESOURCE_TOOL,
                description="读取指定 MCP server 的资源。server 是已配置的服务器名,uri 是资源 URI。",
                parameters={
                    "type": "object",
                    "properties": {
                        "server": {"type": "string", "description": "MCP 服务器名"},
                        "uri": {"type": "string", "description": "资源 URI"},
                    },
                    "required": ["server", "uri"],
                },
                func=self._resource_wrapper,
            )
            self._registered.add(_RESOURCE_TOOL)

        if get_tool(_PROMPT_TOOL) is None:
            register_dynamic(
                name=_PROMPT_TOOL,
                description="获取指定 MCP server 的 prompt 并返回为文本。arguments_json 是 JSON 字符串格式的参数。",
                parameters={
                    "type": "object",
                    "properties": {
                        "server": {"type": "string", "description": "MCP 服务器名"},
                        "name": {"type": "string", "description": "prompt 名称"},
                        "arguments_json": {"type": "string", "description": "JSON 格式的 prompt 参数,默认 {}"},
                    },
                    "required": ["server", "name"],
                },
                func=self._prompt_wrapper,
            )
            self._registered.add(_PROMPT_TOOL)

    # -- tool wrappers ----------------------------------------------------- #
    def _make_tool_wrapper(self, client: MCPClient, tool_name: str, server: str):
        """Create a dispatch-compatible wrapper for one MCP tool."""

        def wrapper(**kwargs: Any) -> str:
            if not client.alive:
                # Try one reconnect.
                try:
                    client.reconnect()
                except Exception as exc:
                    return f"错误:MCP server {server} 重连失败:{type(exc).__name__}: {exc}"

            # Permission check for untrusted servers.
            if not client.trusted:
                mode = self._mcp_permission_mode()
                if mode == "deny":
                    return f"错误:权限拒绝:MCP 工具 {server}__{tool_name} 被配置为拒绝"
                if mode == "ask":
                    prompt = f"确认调用 MCP 工具 {server}__{tool_name}?"
                    approved = decide(
                        ApprovalRequest(
                            kind="mcp",
                            prompt=prompt,
                            action=f"{server}__{tool_name}",
                        ),
                        lambda: self._confirm_terminal(prompt),
                    )
                    if not approved:
                        return f"用户拒绝了 MCP 工具调用:{server}__{tool_name}"

            try:
                result = client.call_tool(tool_name, kwargs)
            except Exception:
                # Try one reconnect + retry.
                try:
                    client.reconnect()
                    result = client.call_tool(tool_name, kwargs)
                except Exception as exc2:
                    return (
                        f"错误:MCP 工具 {server}__{tool_name} 调用失败"
                        f"(重连后仍失败):{type(exc2).__name__}: {exc2}"
                    )

            if result.is_error:
                return f"错误:MCP 工具 {server}__{tool_name} 返回错误:{result.content}"
            return result.content

        return wrapper

    @staticmethod
    def _confirm_terminal(prompt: str) -> bool:
        return confirm_write(prompt)

    def _resource_wrapper(self, **kwargs: Any) -> str:
        server = kwargs.get("server", "")
        uri = kwargs.get("uri", "")
        client = self._clients.get(server)
        if client is None:
            available = ", ".join(sorted(self._clients)) or "(无)"
            return f"错误:未找到 MCP server {server!r}。可用:{available}"
        if not client.alive:
            try:
                client.reconnect()
            except Exception as exc:
                return f"错误:MCP server {server} 重连失败:{type(exc).__name__}: {exc}"
        try:
            return client.read_resource(uri)
        except Exception:
            try:
                client.reconnect()
                return client.read_resource(uri)
            except Exception as exc2:
                return f"错误:读取 MCP 资源 {server}:{uri} 失败:{type(exc2).__name__}: {exc2}"

    def _prompt_wrapper(self, **kwargs: Any) -> str:
        server = kwargs.get("server", "")
        name = kwargs.get("name", "")
        args_json = kwargs.get("arguments_json", "{}")
        try:
            arguments = json.loads(args_json) if args_json else {}
        except json.JSONDecodeError:
            return f"错误:arguments_json 不是有效的 JSON:{args_json}"
        client = self._clients.get(server)
        if client is None:
            available = ", ".join(sorted(self._clients)) or "(无)"
            return f"错误:未找到 MCP server {server!r}。可用:{available}"
        if not client.alive:
            try:
                client.reconnect()
            except Exception as exc:
                return f"错误:MCP server {server} 重连失败:{type(exc).__name__}: {exc}"
        try:
            messages = client.get_prompt(name, arguments)
        except Exception:
            try:
                client.reconnect()
                messages = client.get_prompt(name, arguments)
            except Exception as exc2:
                return f"错误:获取 MCP prompt {server}:{name} 失败:{type(exc2).__name__}: {exc2}"
        # Render messages as text for the agent to read.
        lines: list[str] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            lines.append(f"[{role}] {content}")
        return "\n".join(lines) if lines else "(prompt 为空)"

    def _mcp_permission_mode(self) -> str:
        try:
            from mycode.config import load_config
            cfg = load_config()
            return effective_permission((cfg.permissions.mcp or "ask").strip().lower())
        except Exception:
            return "ask"


def build_registry(project_root: Path | None = None) -> MCPRegistry:
    """Build an MCPRegistry from the current mycode config."""
    try:
        from mycode.config import load_config
        cfg = load_config()
    except Exception:
        return MCPRegistry([], project_root or Path.cwd())
    return MCPRegistry(cfg.mcp.servers, project_root or Path.cwd())


__all__ = ["MCPRegistry", "build_registry"]
