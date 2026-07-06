"""Local symbol index, LSP integration, and precise context selection.

The package initializer intentionally imports no submodules. Tool registration
is reached while configuration is still importing through MCP, so eager
re-exports here would create a config/tools/codeintel cycle.
"""
