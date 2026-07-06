"""Project-scoped facade combining the local index and optional LSP servers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlparse

from mycode.approvals import effective_permission
from mycode.codeintel.index import SymbolIndex
from mycode.codeintel.lsp import LSPClient, LSPError, discover_server
from mycode.codeintel.models import CodeLocation, Diagnostic, Symbol, language_for_path
from mycode.permissions import check_read_path
from mycode.ui import request_command_approval

if TYPE_CHECKING:
    from mycode.config import CodeIntelConfig

_KIND_NAMES = {
    1: "file",
    2: "module",
    5: "class",
    6: "method",
    12: "function",
    13: "variable",
    14: "constant",
    23: "struct",
}


class CodeIntelService:
    def __init__(self, root: Path, config: CodeIntelConfig | None = None) -> None:
        from mycode.config import load_config

        self.root = root.resolve()
        self.config = config or load_config().codeintel
        self.index = SymbolIndex(self.root)
        self._clients: dict[str, LSPClient] = {}
        self.degraded: dict[str, str] = {}

    def ensure_index(self) -> None:
        if not self.index.db_path.exists():
            self.index.build()

    def client_for(self, language: str) -> LSPClient | None:
        from mycode.config import load_config

        if language in self._clients and self._clients[language].running:
            return self._clients[language]
        command = discover_server(self.root, language, self.config.language_servers.get(language))
        if command is None:
            self.degraded[language] = "language server not found"
            return None
        display = " ".join(command)
        if not request_command_approval(
            display,
            mode=effective_permission(load_config().permissions.command),
            risk="read",
        ):
            self.degraded[language] = "language server launch denied"
            return None
        client = LSPClient(self.root, language, command, timeout=self.config.lsp_timeout)
        try:
            client.start()
        except LSPError as exc:
            self.degraded[language] = str(exc)
            client.close()
            return None
        self._clients[language] = client
        self.degraded.pop(language, None)
        return client

    def search_symbols(self, query: str, *, kind: str | None = None, path: str | None = None, limit: int = 20) -> list[Symbol]:
        self.ensure_index()
        local = self.index.search_symbols(query, kind=kind, path=path, limit=limit)
        if local:
            return local
        symbols: list[Symbol] = []
        for language in ("python", "typescript"):
            client = self.client_for(language)
            if client is None:
                continue
            try:
                symbols.extend(_symbol_from_lsp(item, self.root) for item in client.workspace_symbols(query))
            except (LSPError, ValueError) as exc:
                self.degraded[language] = str(exc)
        return [symbol for symbol in symbols if (not kind or symbol.kind == kind) and (not path or symbol.location.path.startswith(path))][:limit]

    def definitions(self, path: str, line: int, column: int = 0) -> list[CodeLocation]:
        resolved = self._checked_path(path)
        client = self.client_for(language_for_path(resolved))
        if client is None:
            return self._local_definition(path, line)
        try:
            return [_location_from_lsp(item, self.root) for item in client.definition(resolved, line, column)]
        except (LSPError, ValueError) as exc:
            self.degraded[language_for_path(resolved)] = str(exc)
            return self._local_definition(path, line)

    def references(self, path: str, line: int, column: int = 0, *, include_declaration: bool = False) -> list[CodeLocation]:
        resolved = self._checked_path(path)
        client = self.client_for(language_for_path(resolved))
        if client is None:
            return []
        try:
            return [
                _location_from_lsp(item, self.root)
                for item in client.references(resolved, line, column, include_declaration)
            ]
        except (LSPError, ValueError) as exc:
            self.degraded[language_for_path(resolved)] = str(exc)
            return []

    def diagnostics(self, path: str) -> list[Diagnostic]:
        resolved = self._checked_path(path)
        client = self.client_for(language_for_path(resolved))
        if client is None:
            return []
        try:
            return [_diagnostic_from_lsp(item, path) for item in client.diagnostics(resolved)]
        except (LSPError, OSError, ValueError) as exc:
            self.degraded[language_for_path(resolved)] = str(exc)
            return []

    def _local_definition(self, path: str, line: int) -> list[CodeLocation]:
        self.ensure_index()
        symbols = self.index.symbols_for_file(Path(path).as_posix())
        containing = [
            symbol.location
            for symbol in symbols
            if symbol.location.start_line <= line <= (symbol.location.end_line or symbol.location.start_line)
        ]
        return containing[-1:]

    def _checked_path(self, path: str) -> Path:
        resolved, error = check_read_path(path, self.root)
        if error or resolved is None:
            raise PermissionError(error or f"path denied: {path}")
        if not resolved.is_file():
            raise FileNotFoundError(path)
        return resolved

    def close(self) -> None:
        for client in self._clients.values():
            client.close()
        self._clients.clear()

    def status_json(self) -> str:
        return json.dumps({"index": self.index.status(), "degraded": self.degraded}, ensure_ascii=False, indent=2)


def _uri_path(uri: str, root: Path) -> str:
    parsed = urlparse(uri)
    raw = unquote(parsed.path)
    if parsed.netloc:
        raw = f"//{parsed.netloc}{raw}"
    if len(raw) >= 3 and raw[0] == "/" and raw[2] == ":":
        raw = raw[1:]
    return Path(raw).resolve().relative_to(root).as_posix()


def _location_from_lsp(item: dict[str, Any], root: Path) -> CodeLocation:
    target = item.get("targetUri") and {
        "uri": item["targetUri"],
        "range": item.get("targetSelectionRange") or item.get("targetRange") or {},
    } or item
    range_value = target.get("range") or {}
    start = range_value.get("start") or {}
    end = range_value.get("end") or {}
    return CodeLocation(
        path=_uri_path(str(target.get("uri", "")), root),
        start_line=int(start.get("line", 0)) + 1,
        start_column=int(start.get("character", 0)),
        end_line=int(end.get("line", 0)) + 1,
        end_column=int(end.get("character", 0)),
    )


def _symbol_from_lsp(item: dict[str, Any], root: Path) -> Symbol:
    location = _location_from_lsp(item.get("location") or item, root)
    return Symbol(
        name=str(item.get("name", "")),
        kind=_KIND_NAMES.get(int(item.get("kind", 0)), "symbol"),
        container=item.get("containerName"),
        signature=item.get("detail"),
        location=location,
        source="lsp",
    )


def _diagnostic_from_lsp(item: dict[str, Any], path: str) -> Diagnostic:
    range_value = item.get("range") or {}
    start = range_value.get("start") or {}
    end = range_value.get("end") or {}
    return Diagnostic(
        location=CodeLocation(
            path=Path(path).as_posix(),
            start_line=int(start.get("line", 0)) + 1,
            start_column=int(start.get("character", 0)),
            end_line=int(end.get("line", 0)) + 1,
            end_column=int(end.get("character", 0)),
        ),
        message=str(item.get("message", "")),
        severity=item.get("severity"),
        source=item.get("source"),
    )


__all__ = ["CodeIntelService"]
