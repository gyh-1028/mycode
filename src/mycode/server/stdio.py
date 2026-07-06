"""Line-delimited JSON-RPC transport for editor integrations."""

from __future__ import annotations

import json
import logging
import sys
import threading
from typing import Any, TextIO

from mycode.server.protocol import ProtocolError, error, success, validate_request
from mycode.server.session import RpcSession

_LOGGER = logging.getLogger("mycode.server")


class RpcWriter:
    def __init__(self, stream: TextIO) -> None:
        self.stream = stream
        self._lock = threading.Lock()

    def send(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self.stream.write(line + "\n")
            self.stream.flush()


class StdioServer(RpcSession):
    """Compatibility adapter retaining the existing stdio server API."""

    def handle(self, message: Any) -> dict[str, Any] | None:
        request_id = message.get("id") if isinstance(message, dict) else None
        try:
            request_id, method, params = validate_request(message)
            result = self.dispatch(method, params)
            if request_id is None:
                return None
            return success(request_id, result)
        except ProtocolError as exc:
            return error(request_id, exc.code, exc.message, exc.data)
        except Exception as exc:  # noqa: BLE001 - protocol boundary
            _LOGGER.exception("rpc method failed")
            return error(request_id, -32603, "Internal error", {"type": type(exc).__name__, "detail": str(exc)})

    def serve(self, stream: TextIO) -> None:
        for raw in stream:
            if self.shutdown_requested:
                break
            raw = raw.strip()
            if not raw:
                continue
            try:
                message = json.loads(raw)
            except json.JSONDecodeError as exc:
                self.writer.send(error(None, -32700, "Parse error", {"detail": str(exc)}))
                continue
            response = self.handle(message)
            if response is not None:
                self.writer.send(response)
        self.close()


def run_stdio_server(stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
    input_stream = stdin or sys.stdin
    output_stream = stdout or sys.stdout
    StdioServer(writer=RpcWriter(output_stream)).serve(input_stream)


__all__ = ["RpcWriter", "StdioServer", "run_stdio_server"]
