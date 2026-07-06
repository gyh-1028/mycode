"""Tool subsystem: registry plus the read-only file/search tools.

Importing this package registers all built-in tools (via the side effects of
importing ``files`` and ``search``), so ``get_schemas()`` / ``dispatch()`` see
them immediately.
"""

# Imported for their registration side effects.
from mycode.codeintel import tools as codeintel_tools  # noqa: F401  (symbol/LSP tools)
from mycode.tools import files as files  # noqa: F401  (list/read/edit/write)
from mycode.tools import git as git  # noqa: F401  (git_status/log/branch)
from mycode.tools import search as search  # noqa: F401  (search_code)
from mycode.tools import shell as shell  # noqa: F401  (run_bash)
from mycode.tools.registry import (
    Tool,
    ToolResult,
    dispatch,
    dispatch_tool,
    get_schemas,
    get_tool,
    register,
    register_dynamic,
    unregister,
)

__all__ = [
    "Tool",
    "ToolResult",
    "dispatch",
    "dispatch_tool",
    "get_schemas",
    "get_tool",
    "register",
    "register_dynamic",
    "unregister",
]
