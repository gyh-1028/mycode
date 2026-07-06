"""Structured, read-only workspace operations for machine frontends."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mycode.permissions import check_read_path, is_sensitive_path

MAX_FILE_BYTES = 1024 * 1024
MAX_SEARCH_RESULTS = 200
_IGNORED_DIRS = {
    ".git",
    ".mycode",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}


class WorkspaceError(RuntimeError):
    """A safe, user-facing workspace access failure."""


def _checked(path: str, root: Path) -> Path:
    resolved, error = check_read_path(path, root)
    if error or resolved is None:
        raise WorkspaceError(error or f"无法读取路径:{path}")
    return resolved


def _relative(path: Path, root: Path) -> str:
    relative = path.relative_to(root).as_posix()
    return relative or "."


def _is_binary(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return b"\0" in handle.read(8192)
    except OSError as exc:
        raise WorkspaceError(f"无法读取文件:{path.name}: {exc}") from exc


def _language(path: Path) -> str:
    return {
        ".py": "python",
        ".pyi": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".json": "json",
        ".md": "markdown",
        ".toml": "toml",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".css": "css",
        ".html": "html",
    }.get(path.suffix.lower(), "text")


def list_workspace(path: str, root: Path) -> dict[str, Any]:
    directory = _checked(path, root)
    if not directory.is_dir():
        raise WorkspaceError(f"不是目录:{path}")
    entries: list[dict[str, Any]] = []
    try:
        children = sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
    except OSError as exc:
        raise WorkspaceError(f"无法列出目录:{path}: {exc}") from exc
    for child in children:
        if child.name in _IGNORED_DIRS or is_sensitive_path(child):
            continue
        resolved, error = check_read_path(child, root)
        if error or resolved is None:
            continue
        try:
            is_dir = resolved.is_dir()
            is_file = resolved.is_file()
            if not (is_dir or is_file):
                continue
            if is_file and _is_binary(resolved):
                continue
            item: dict[str, Any] = {
                "name": child.name,
                "path": child.relative_to(root).as_posix(),
                "type": "directory" if is_dir else "file",
            }
            if is_file:
                item["size"] = resolved.stat().st_size
                item["language"] = _language(resolved)
            entries.append(item)
        except (OSError, ValueError):
            continue
    return {"path": _relative(directory, root), "entries": entries}


def read_workspace_file(path: str, root: Path) -> dict[str, Any]:
    target = _checked(path, root)
    if not target.is_file():
        raise WorkspaceError(f"不是文件:{path}")
    size = target.stat().st_size
    if size > MAX_FILE_BYTES:
        raise WorkspaceError(f"文件超过 1 MiB 预览上限:{path}")
    if _is_binary(target):
        raise WorkspaceError(f"拒绝预览二进制文件:{path}")
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise WorkspaceError(f"文件不是 UTF-8 文本:{path}") from exc
    except OSError as exc:
        raise WorkspaceError(f"无法读取文件:{path}: {exc}") from exc
    return {
        "path": _relative(target, root),
        "language": _language(target),
        "content": content,
        "size": size,
        "lines": content.count("\n") + 1,
    }


def search_workspace(query: str, path: str, root: Path, limit: int = 100) -> dict[str, Any]:
    if not query:
        raise WorkspaceError("搜索内容不能为空")
    if limit < 1 or limit > MAX_SEARCH_RESULTS:
        raise WorkspaceError(f"limit 必须在 1 到 {MAX_SEARCH_RESULTS} 之间")
    base = _checked(path, root)
    if not base.is_dir():
        raise WorkspaceError(f"搜索路径不是目录:{path}")
    matches: list[dict[str, Any]] = []
    for current, dirnames, filenames in os.walk(base):
        dirnames[:] = [
            name
            for name in dirnames
            if name not in _IGNORED_DIRS and not is_sensitive_path(Path(current) / name)
        ]
        for filename in filenames:
            candidate = Path(current) / filename
            if is_sensitive_path(candidate):
                continue
            resolved, error = check_read_path(candidate, root)
            if error or resolved is None:
                continue
            try:
                if resolved.stat().st_size > MAX_FILE_BYTES or _is_binary(resolved):
                    continue
                lines = resolved.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError, WorkspaceError):
                continue
            for number, line in enumerate(lines, start=1):
                column = line.find(query)
                if column < 0:
                    continue
                matches.append(
                    {
                        "path": _relative(resolved, root),
                        "line": number,
                        "column": column + 1,
                        "preview": line.strip()[:300],
                    }
                )
                if len(matches) >= limit:
                    return {"query": query, "matches": matches, "truncated": True}
    return {"query": query, "matches": matches, "truncated": False}


__all__ = [
    "MAX_FILE_BYTES",
    "MAX_SEARCH_RESULTS",
    "WorkspaceError",
    "list_workspace",
    "read_workspace_file",
    "search_workspace",
]
