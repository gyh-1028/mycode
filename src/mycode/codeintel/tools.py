"""Agent tools backed by the project code-intelligence service."""

from __future__ import annotations

import json
from pathlib import Path

from mycode.codeintel.service import CodeIntelService
from mycode.tools.registry import register

_SERVICES: dict[Path, CodeIntelService] = {}


def get_service(root: Path | None = None) -> CodeIntelService:
    resolved = (root or Path.cwd()).resolve()
    if resolved not in _SERVICES:
        _SERVICES[resolved] = CodeIntelService(resolved)
    return _SERVICES[resolved]


def close_service(root: Path | None = None) -> None:
    resolved = (root or Path.cwd()).resolve()
    service = _SERVICES.pop(resolved, None)
    if service is not None:
        service.close()


@register(
    name="search_symbols",
    description="Search indexed Python/TypeScript symbols by name. Returns paths, kinds, signatures, and source ranges.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "kind": {"type": "string"},
            "path": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "required": ["query"],
    },
)
def search_symbols(query: str, kind: str | None = None, path: str | None = None, limit: int = 20) -> str:
    if not query.strip():
        return "错误:query 不能为空"
    try:
        symbols = get_service().search_symbols(query, kind=kind, path=path, limit=limit)
        return json.dumps([symbol.to_dict() for symbol in symbols], ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001 - tool errors are returned to the model
        return f"错误:符号搜索失败:{type(exc).__name__}: {exc}"


@register(
    name="find_definition",
    description="Find the definition at a one-based source line and zero-based column using LSP with local-index fallback.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "line": {"type": "integer", "minimum": 1},
            "column": {"type": "integer", "minimum": 0},
        },
        "required": ["path", "line"],
    },
)
def find_definition(path: str, line: int, column: int = 0) -> str:
    try:
        values = get_service().definitions(path, line, column)
        return json.dumps([value.to_dict() for value in values], ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        return f"错误:定义查询失败:{type(exc).__name__}: {exc}"


@register(
    name="find_references",
    description="Find references at a one-based source line and zero-based column using the configured language server.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "line": {"type": "integer", "minimum": 1},
            "column": {"type": "integer", "minimum": 0},
            "include_declaration": {"type": "boolean"},
        },
        "required": ["path", "line"],
    },
)
def find_references(path: str, line: int, column: int = 0, include_declaration: bool = False) -> str:
    try:
        values = get_service().references(path, line, column, include_declaration=include_declaration)
        return json.dumps([value.to_dict() for value in values], ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        return f"错误:引用查询失败:{type(exc).__name__}: {exc}"


@register(
    name="get_diagnostics",
    description="Get language-server diagnostics for one project file. Returns an empty list when LSP is unavailable.",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
)
def get_diagnostics(path: str) -> str:
    try:
        values = get_service().diagnostics(path)
        return json.dumps([value.to_dict() for value in values], ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        return f"错误:诊断查询失败:{type(exc).__name__}: {exc}"


__all__ = ["close_service", "find_definition", "find_references", "get_diagnostics", "get_service", "search_symbols"]
