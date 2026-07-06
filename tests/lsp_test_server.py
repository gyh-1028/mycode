"""Small deterministic LSP server used by code-intelligence tests."""

from __future__ import annotations

import json
import sys


def read_message():
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in {b"\r\n", b"\n"}:
            break
        name, _, value = line.decode("ascii").partition(":")
        headers[name.lower()] = value.strip()
    return json.loads(sys.stdin.buffer.read(int(headers["content-length"])))


def write_message(message):
    payload = json.dumps(message, separators=(",", ":")).encode()
    sys.stdout.buffer.write(f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload)
    sys.stdout.buffer.flush()


while True:
    message = read_message()
    if message is None:
        break
    method = message.get("method")
    if "id" not in message:
        if method == "exit":
            break
        continue
    result = None
    if method == "initialize":
        result = {"capabilities": {"workspaceSymbolProvider": True}}
    elif method == "workspace/symbol":
        root = message["params"]["query"]
        result = [
            {
                "name": root,
                "kind": 12,
                "location": {
                    "uri": "file:///tmp/demo.py",
                    "range": {
                        "start": {"line": 1, "character": 0},
                        "end": {"line": 2, "character": 0},
                    },
                },
            }
        ]
    elif method in {"textDocument/definition", "textDocument/references"}:
        uri = message["params"]["textDocument"]["uri"]
        result = [{"uri": uri, "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 3}}}]
    elif method == "textDocument/diagnostic":
        result = {
            "items": [
                {
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 0, "character": 1},
                    },
                    "severity": 2,
                    "source": "test",
                    "message": "demo warning",
                }
            ]
        }
    write_message({"jsonrpc": "2.0", "id": message["id"], "result": result})
