"""MCP stdio client: manages one server session via the official ``mcp`` SDK.

The SDK is fully async; mycode is synchronous. This client bridges the two by
running a persistent :mod:`asyncio` event loop in a background daemon thread.
All public methods are synchronous and submit coroutines to that loop via
:func:`asyncio.run_coroutine_threadsafe`.

Lifecycle::

    client = MCPClient(config, project_root)
    client.start()          # spawn subprocess, initialise, discover capabilities
    client.call_tool(...)   # reuse the live session
    client.stop()           # close session, kill subprocess, stop loop

If a call fails (server crash, timeout), the client marks itself dead. The
wrapper in :mod:`mycode.mcp.registry` will attempt one ``reconnect()`` before
giving up, satisfying the roadmap's reconnection requirement.

The ``mcp`` package is imported lazily so that the rest of mycode works without
it installed. Calling :meth:`start` without the package raises a clear error.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

from mycode.mcp.config import MCPServerConfig
from mycode.mcp.types import MCPCallResult, MCPPromptRef, MCPResourceRef, MCPToolRef

_LOGGER = logging.getLogger("mycode.mcp.client")

# System env vars that the subprocess needs to function (finding executables,
# Windows system DLLs, locale). These are NOT secrets and are safe to pass.
_ESSENTIAL_ENV_KEYS = frozenset({
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP",
    "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
    "LANG", "LC_ALL", "LC_CTYPE",
    "VIRTUAL_ENV",  # so the subprocess uses the same venv if active
})


def _build_subprocess_env(configured: dict[str, str]) -> dict[str, str]:
    """Build the subprocess environment: essential system vars + configured.

    This is 'explicit' in the roadmap's sense: only a curated set of non-secret
    system vars plus the user's configured keys are passed. API keys, tokens,
    and other host env vars are NOT inherited unless explicitly configured.
    """
    import os

    env: dict[str, str] = {}
    for key in _ESSENTIAL_ENV_KEYS:
        val = os.environ.get(key)
        if val:
            env[key] = val
    # Configured env takes precedence over inherited.
    env.update(configured)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _require_mcp() -> Any:
    """Lazily import the ``mcp`` SDK or raise a helpful error."""
    try:
        import mcp.types as mcp_types  # noqa: F401
        from mcp import ClientSession, StdioServerParameters  # noqa: F401
        from mcp.client.stdio import stdio_client  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "MCP 功能需要安装 mcp 包。请运行: pip install 'mycode[mcp]' 或 pip install mcp"
        ) from exc
    import mcp.types as mcp_types
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    return ClientSession, StdioServerParameters, stdio_client, mcp_types


class MCPClient:
    """Manages a single MCP stdio server session.

    Single-use: create, start, use, stop. Thread-safe for concurrent tool
    calls (the event loop serialises them).
    """

    def __init__(self, config: MCPServerConfig, project_root: Path) -> None:
        self._config = config
        self._project_root = project_root
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session: Any = None  # ClientSession
        self._stdio_cm: Any = None
        self._session_cm: Any = None
        self._alive = False
        self._tools: list[MCPToolRef] = []
        self._resources: list[MCPResourceRef] = []
        self._prompts: list[MCPPromptRef] = []

    # -- properties -------------------------------------------------------- #
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

    # -- lifecycle --------------------------------------------------------- #
    def start(self) -> None:
        """Spawn the server subprocess, initialise the session, discover tools."""
        if self._alive:
            return
        ClientSession, StdioServerParameters, stdio_client, mcp_types = _require_mcp()

        self._loop = asyncio.new_event_loop()
        # Run the startup coroutine directly (in this thread) so we get clear
        # errors and no race with the background loop. After startup, switch to
        # background mode for subsequent call_tool / read_resource calls.
        try:
            self._loop.run_until_complete(
                self._start_async(StdioServerParameters, stdio_client)
            )
        except Exception:
            self._cleanup_loop()
            raise

        # Now start the loop in a background thread for subsequent calls.
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name=f"mcp-{self.name}")
        self._thread.start()

    async def _start_async(self, StdioServerParameters, stdio_client) -> None:
        from mcp import ClientSession

        # Explicit env: essential system vars (so the subprocess can function)
        # plus configured keys, plus UTF-8 defaults. Sensitive host env vars
        # (API keys, tokens) are NOT inherited unless explicitly configured.
        env = _build_subprocess_env(self._config.env)

        params = StdioServerParameters(
            command=self._config.command,
            args=list(self._config.args),
            env=env,
        )

        self._stdio_cm = stdio_client(params)
        read, write = await self._stdio_cm.__aenter__()
        self._session_cm = ClientSession(read, write)
        self._session = await self._session_cm.__aenter__()
        await self._session.initialize()
        await self._discover()
        self._alive = True
        _LOGGER.info("MCP server %s 已启动 (%d tools, %d resources, %d prompts)",
                      self.name, len(self._tools), len(self._resources), len(self._prompts))

    async def _discover(self) -> None:
        # Tools
        try:
            result = await self._session.list_tools()
            self._tools = [self._convert_tool(t) for t in result.tools]
        except Exception as exc:
            _LOGGER.warning("MCP server %s 工具发现失败: %s", self.name, exc)
            self._tools = []
        # Resources
        try:
            result = await self._session.list_resources()
            self._resources = [self._convert_resource(r) for r in result.resources]
        except Exception:
            self._resources = []
        # Prompts
        try:
            result = await self._session.list_prompts()
            self._prompts = [self._convert_prompt(p) for p in result.prompts]
        except Exception:
            self._prompts = []

    def stop(self) -> None:
        if self._loop is None:
            return
        # If the background thread is running, use it to close the session.
        if self._thread is not None and self._thread.is_alive():
            try:
                fut = asyncio.run_coroutine_threadsafe(self._stop_async(), self._loop)
                fut.result(timeout=10)
            except Exception:
                pass
        else:
            # No background thread — close synchronously (startup-path cleanup).
            try:
                self._loop.run_until_complete(self._stop_async())
            except Exception:
                pass
        self._cleanup_loop()

    async def _stop_async(self) -> None:
        self._alive = False
        self._session = None
        if self._session_cm is not None:
            try:
                await self._session_cm.__aexit__(None, None, None)
            except Exception:
                pass
            self._session_cm = None
        if self._stdio_cm is not None:
            try:
                await self._stdio_cm.__aexit__(None, None, None)
            except Exception:
                pass
            self._stdio_cm = None

    def reconnect(self) -> None:
        """Stop and restart the server (used after a crash/timeout)."""
        self.stop()
        self.start()

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _cleanup_loop(self) -> None:
        self._alive = False
        self._session = None
        loop = self._loop
        thread = self._thread
        self._loop = None
        self._thread = None
        if thread is not None and thread.is_alive():
            if loop is not None:
                try:
                    loop.call_soon_threadsafe(loop.stop)
                except RuntimeError:
                    pass
            thread.join(timeout=5)
        if loop is not None:
            try:
                if loop.is_running():
                    loop.stop()
            except RuntimeError:
                pass
            try:
                loop.close()
            except Exception:
                pass

    # -- operations -------------------------------------------------------- #
    def call_tool(self, name: str, args: dict[str, Any]) -> MCPCallResult:
        if not self._alive or self._session is None:
            raise RuntimeError(f"MCP server {self.name} 未运行")
        loop = self._loop
        if loop is None:
            raise RuntimeError(f"MCP server {self.name} 未运行")
        fut = asyncio.run_coroutine_threadsafe(
            self._session.call_tool(name, args), loop
        )
        try:
            result = fut.result(timeout=self._config.timeout)
        except Exception:
            self._alive = False
            raise
        return self._convert_call_result(result)

    def read_resource(self, uri: str) -> str:
        if not self._alive or self._session is None:
            raise RuntimeError(f"MCP server {self.name} 未运行")
        loop = self._loop
        if loop is None:
            raise RuntimeError(f"MCP server {self.name} 未运行")
        fut = asyncio.run_coroutine_threadsafe(
            self._session.read_resource(uri), loop
        )
        try:
            result = fut.result(timeout=self._config.timeout)
        except Exception:
            self._alive = False
            raise
        return self._convert_resource_result(result)

    def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if not self._alive or self._session is None:
            raise RuntimeError(f"MCP server {self.name} 未运行")
        loop = self._loop
        if loop is None:
            raise RuntimeError(f"MCP server {self.name} 未运行")
        fut = asyncio.run_coroutine_threadsafe(
            self._session.get_prompt(name, arguments or {}), loop
        )
        try:
            result = fut.result(timeout=self._config.timeout)
        except Exception:
            self._alive = False
            raise
        return self._convert_prompt_result(result)

    # -- conversion -------------------------------------------------------- #
    def _convert_tool(self, tool: Any) -> MCPToolRef:
        return MCPToolRef(
            server=self.safe_name,
            name=tool.name,
            description=tool.description or "",
            input_schema=tool.inputSchema or {"type": "object", "properties": {}},
            annotations=getattr(tool, "annotations", None) and tool.annotations.model_dump()
            if hasattr(getattr(tool, "annotations", None), "model_dump")
            else (getattr(tool, "annotations", None) if tool.annotations else None),
        )

    def _convert_resource(self, resource: Any) -> MCPResourceRef:
        return MCPResourceRef(
            server=self.safe_name,
            uri=str(resource.uri),
            name=resource.name or "",
            description=getattr(resource, "description", "") or "",
            mime_type=getattr(resource, "mimeType", None),
        )

    def _convert_prompt(self, prompt: Any) -> MCPPromptRef:
        args = []
        for arg in (prompt.arguments or []):
            args.append({
                "name": arg.name,
                "description": getattr(arg, "description", "") or "",
                "required": getattr(arg, "required", False),
            })
        return MCPPromptRef(
            server=self.safe_name,
            name=prompt.name,
            description=prompt.description or "",
            arguments=args,
        )

    def _convert_call_result(self, result: Any) -> MCPCallResult:
        parts: list[str] = []
        for content in result.content:
            text = getattr(content, "text", None)
            if text is not None:
                parts.append(text)
            else:
                parts.append(str(content))
        return MCPCallResult(
            content="\n".join(parts) if parts else "(MCP 工具未返回内容)",
            is_error=bool(getattr(result, "isError", False)),
        )

    def _convert_resource_result(self, result: Any) -> str:
        parts: list[str] = []
        for item in result.contents:
            text = getattr(item, "text", None)
            if text is not None:
                parts.append(text)
            else:
                parts.append(str(item))
        return "\n".join(parts) if parts else "(资源为空)"

    def _convert_prompt_result(self, result: Any) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for msg in result.messages:
            content = msg.content
            text = getattr(content, "text", None)
            messages.append({
                "role": msg.role,
                "content": text if text is not None else str(content),
            })
        return messages


__all__ = ["MCPClient"]
