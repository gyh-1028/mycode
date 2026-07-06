"""Minimal synchronous LSP client over JSON-RPC stdio."""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, BinaryIO, cast


class LSPError(RuntimeError):
    pass


def read_message(stream: BinaryIO) -> dict[str, Any] | None:
    """Read one Content-Length framed JSON-RPC message."""
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            return None
        if line in {b"\r\n", b"\n"}:
            break
        name, separator, value = line.decode("ascii", "replace").partition(":")
        if separator:
            headers[name.strip().lower()] = value.strip()
    try:
        length = int(headers["content-length"])
    except (KeyError, ValueError) as exc:
        raise LSPError("invalid LSP frame: missing Content-Length") from exc
    payload = stream.read(length)
    if len(payload) != length:
        raise LSPError("truncated LSP frame")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LSPError(f"invalid LSP JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise LSPError("LSP message must be an object")
    return value


def write_message(stream: BinaryIO, message: dict[str, Any]) -> None:
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    stream.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii"))
    stream.write(payload)
    stream.flush()


def discover_server(root: Path, language: str, override: list[str] | None = None) -> list[str] | None:
    if override:
        return list(override)
    if language == "python":
        names = ["pyright-langserver"]
        local_candidates = [
            root / ".venv" / "Scripts" / "pyright-langserver.exe",
            root / ".venv" / "bin" / "pyright-langserver",
        ]
    elif language == "typescript":
        names = ["typescript-language-server"]
        local_candidates = [
            root / "node_modules" / ".bin" / "typescript-language-server.cmd",
            root / "node_modules" / ".bin" / "typescript-language-server",
        ]
    else:
        return None
    for candidate in local_candidates:
        if candidate.is_file():
            return [str(candidate), "--stdio"]
    for name in names:
        found = shutil.which(name)
        if found:
            return [found, "--stdio"]
    return None


class LSPClient:
    """One reusable language-server process for a project and language."""

    def __init__(self, root: Path, language: str, command: list[str], *, timeout: float = 5.0) -> None:
        self.root = root.resolve()
        self.language = language
        self.command = list(command)
        self.timeout = timeout
        self._process: subprocess.Popen[bytes] | None = None
        self._messages: queue.Queue[dict[str, Any] | BaseException | None] = queue.Queue()
        self._pending: dict[int, dict[str, Any]] = {}
        self._diagnostics: dict[str, list[dict[str, Any]]] = {}
        self._request_id = 0
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        if self.running:
            return
        secret_markers = ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
        env = {
            key: value
            for key, value in os.environ.items()
            if not any(marker in key.upper() for marker in secret_markers)
        }
        try:
            self._process = subprocess.Popen(
                self.command,
                cwd=self.root,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
            )
        except OSError as exc:
            raise LSPError(f"cannot start language server: {exc}") from exc
        threading.Thread(target=self._reader, name=f"mycode-lsp-{self.language}", daemon=True).start()
        self._request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": self.root.as_uri(),
                "capabilities": {"textDocument": {"publishDiagnostics": {}}},
                "workspaceFolders": [{"uri": self.root.as_uri(), "name": self.root.name}],
            },
        )
        self._notify("initialized", {})

    def _reader(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        try:
            while True:
                message = read_message(cast(BinaryIO, self._process.stdout))
                if message is None:
                    self._messages.put(None)
                    return
                self._messages.put(message)
        except BaseException as exc:  # noqa: BLE001 - transfer reader failure to requester
            self._messages.put(exc)

    def _send(self, message: dict[str, Any]) -> None:
        if not self.running or self._process is None or self._process.stdin is None:
            raise LSPError("language server is not running")
        write_message(cast(BinaryIO, self._process.stdin), message)

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: dict[str, Any]) -> Any:
        with self._lock:
            self._request_id += 1
            request_id = self._request_id
            self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            if request_id in self._pending:
                message = self._pending.pop(request_id)
            else:
                while True:
                    try:
                        item = self._messages.get(timeout=self.timeout)
                    except queue.Empty as exc:
                        raise LSPError(f"LSP request timed out: {method}") from exc
                    if item is None:
                        raise LSPError("language server exited")
                    if isinstance(item, BaseException):
                        raise LSPError(str(item)) from item
                    if item.get("method") == "textDocument/publishDiagnostics":
                        params_value = item.get("params") or {}
                        self._diagnostics[str(params_value.get("uri", ""))] = list(params_value.get("diagnostics") or [])
                        continue
                    response_id = item.get("id")
                    if response_id == request_id:
                        message = item
                        break
                    if isinstance(response_id, int):
                        self._pending[response_id] = item
            if "error" in message:
                raise LSPError(f"LSP {method} failed: {message['error']}")
            return message.get("result")

    def workspace_symbols(self, query: str) -> list[dict[str, Any]]:
        self.start()
        result = self._request("workspace/symbol", {"query": query})
        return list(result or [])

    def definition(self, path: Path, line: int, column: int) -> list[dict[str, Any]]:
        return self._locations("textDocument/definition", path, line, column)

    def references(self, path: Path, line: int, column: int, include_declaration: bool = False) -> list[dict[str, Any]]:
        return self._locations(
            "textDocument/references",
            path,
            line,
            column,
            extra={"context": {"includeDeclaration": include_declaration}},
        )

    def _locations(
        self,
        method: str,
        path: Path,
        line: int,
        column: int,
        *,
        extra: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        self.start()
        params: dict[str, Any] = {
            "textDocument": {"uri": path.resolve().as_uri()},
            "position": {"line": max(0, line - 1), "character": max(0, column)},
        }
        params.update(extra or {})
        result = self._request(method, params)
        if not result:
            return []
        return list(result) if isinstance(result, list) else [result]

    def diagnostics(self, path: Path) -> list[dict[str, Any]]:
        self.start()
        uri = path.resolve().as_uri()
        text = path.read_text(encoding="utf-8", errors="replace")
        self._notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": self.language,
                    "version": 1,
                    "text": text,
                }
            },
        )
        try:
            result = self._request("textDocument/diagnostic", {"textDocument": {"uri": uri}})
            if isinstance(result, dict):
                return list(result.get("items") or [])
        except LSPError:
            pass
        return list(self._diagnostics.get(uri, []))

    def close(self) -> None:
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            try:
                self._request("shutdown", {})
                self._notify("exit", {})
                process.wait(timeout=2)
            except (LSPError, subprocess.TimeoutExpired):
                process.kill()
        self._process = None


__all__ = ["LSPClient", "LSPError", "discover_server", "read_message", "write_message"]
