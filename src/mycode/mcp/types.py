"""MCP data types: capability references and call results.

These are the UI-agnostic structures that the MCP client produces and the
registry/tool-adapter consumes. They decouple the rest of mycode from the
``mcp`` SDK's own types (which may change between SDK versions).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MCPToolRef:
    """A reference to an MCP tool discovered from a server."""

    server: str
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    # Raw annotations from the server (readOnlyHint, destructiveHint, etc.).
    # These are HINTS only and never bypass permission confirmation.
    annotations: dict[str, Any] | None = None

    @property
    def full_name(self) -> str:
        """The namespaced tool name used in mycode's tool registry."""
        return f"mcp__{self.server}__{self.name}"


@dataclass(frozen=True)
class MCPResourceRef:
    """A reference to an MCP resource discovered from a server."""

    server: str
    uri: str
    name: str = ""
    description: str = ""
    mime_type: str | None = None


@dataclass(frozen=True)
class MCPPromptRef:
    """A reference to an MCP prompt discovered from a server."""

    server: str
    name: str
    description: str = ""
    arguments: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class MCPCallResult:
    """The normalised result of calling an MCP tool."""

    content: str
    is_error: bool = False


__all__ = ["MCPCallResult", "MCPPromptRef", "MCPResourceRef", "MCPToolRef"]
