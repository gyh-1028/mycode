"""Shared code-intelligence value objects."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CodeLocation:
    path: str
    start_line: int
    start_column: int = 0
    end_line: int | None = None
    end_column: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IndexedFile:
    path: str
    language: str
    mtime_ns: int
    content_hash: str
    size: int


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str
    location: CodeLocation
    container: str | None = None
    signature: str | None = None
    source: str = "ast"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["location"] = self.location.to_dict()
        return data


@dataclass(frozen=True)
class DependencyEdge:
    source_path: str
    target: str
    kind: str = "import"


@dataclass(frozen=True)
class Diagnostic:
    location: CodeLocation
    message: str
    severity: int | None = None
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["location"] = self.location.to_dict()
        return data


def language_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".py", ".pyi"}:
        return "python"
    if suffix in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}:
        return "typescript"
    if suffix in {".toml", ".json", ".yaml", ".yml"}:
        return "config"
    if suffix in {".md", ".rst", ".txt"}:
        return "text"
    return "unknown"


__all__ = [
    "CodeLocation",
    "DependencyEdge",
    "Diagnostic",
    "IndexedFile",
    "Symbol",
    "language_for_path",
]
