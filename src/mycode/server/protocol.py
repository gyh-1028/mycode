"""JSON-RPC 2.0 helpers and the MyCode protocol v1 wire shapes."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from mycode.agent.events import AgentEvent

JSONRPC_VERSION = "2.0"
PROTOCOL_VERSION = 1


class ProtocolError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def success(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": {"code": code, "message": message},
    }
    if data is not None:
        payload["error"]["data"] = data
    return payload


def notification(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "method": method, "params": params}


def event_payload(event: AgentEvent) -> dict[str, Any]:
    return asdict(event)


def validate_request(message: Any) -> tuple[Any, str, dict[str, Any]]:
    if not isinstance(message, dict):
        raise ProtocolError(-32600, "Invalid Request")
    if message.get("jsonrpc") != JSONRPC_VERSION:
        raise ProtocolError(-32600, "jsonrpc must be '2.0'")
    method = message.get("method")
    if not isinstance(method, str) or not method:
        raise ProtocolError(-32600, "method must be a non-empty string")
    params = message.get("params", {})
    if not isinstance(params, dict):
        raise ProtocolError(-32602, "params must be an object")
    return message.get("id"), method, params


__all__ = [
    "JSONRPC_VERSION",
    "PROTOCOL_VERSION",
    "ProtocolError",
    "error",
    "event_payload",
    "notification",
    "success",
    "validate_request",
]
