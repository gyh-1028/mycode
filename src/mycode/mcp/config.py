"""MCP server configuration model.

MCP servers are defined in ``.mycode/config.toml`` under ``[[mcp.servers]]``::

    [[mcp.servers]]
    name = "filesystem"
    command = "npx"
    args = ["-y", "@modelcontextprotocol/server-filesystem", "/project"]
    enabled = true
    trusted = false

    [mcp.servers.env]
    API_KEY = "..."

Security defaults (per the P7 roadmap):

* ``enabled`` defaults to **False** — servers must be explicitly opted in.
* ``trusted`` defaults to **False** — untrusted servers' tool calls go through
  the permission confirmation flow (``permissions.mcp``).
* ``env`` is **explicit** — the subprocess does NOT inherit the host
  environment; only the keys listed in ``env`` are passed (plus a few UTF-8
  defaults for reliable output decoding).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP stdio server."""

    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    timeout: float = 30.0
    enabled: bool = False
    trusted: bool = False

    @property
    def safe_name(self) -> str:
        """A sanitised name suitable for use in tool namespacing."""
        return self.name.replace("-", "_").replace(".", "_").replace("/", "_")


class MCPConfig(BaseModel):
    """Top-level MCP configuration section."""

    servers: list[MCPServerConfig] = Field(default_factory=list)

    @property
    def enabled_servers(self) -> list[MCPServerConfig]:
        return [s for s in self.servers if s.enabled]


__all__ = ["MCPConfig", "MCPServerConfig"]
