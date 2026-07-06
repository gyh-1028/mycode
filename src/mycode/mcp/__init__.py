"""MCP (Model Context Protocol) client integration.

Lets mycode use external MCP tools, resources, and prompts via local stdio
servers. The ``mcp`` Python SDK is an optional dependency (install with
``pip install 'mycode[mcp]'``); without it, the rest of mycode works normally
and no MCP servers are connected.

v1 scope (per the P7 roadmap):

* Local stdio transport only (no remote HTTP).
* Lazy server startup with session reuse.
* Tools namespaced as ``mcp__<server>__<tool>``.
* Resource reading and prompt expansion via generic built-in tools.
* Servers default disabled and untrusted; tool annotations are hints only.
"""

from mycode.mcp.client import MCPClient
from mycode.mcp.config import MCPConfig, MCPServerConfig
from mycode.mcp.registry import MCPRegistry, build_registry
from mycode.mcp.types import MCPCallResult, MCPPromptRef, MCPResourceRef, MCPToolRef

__all__ = [
    "MCPClient",
    "MCPConfig",
    "MCPCallResult",
    "MCPPromptRef",
    "MCPRegistry",
    "MCPResourceRef",
    "MCPServerConfig",
    "MCPToolRef",
    "build_registry",
]
