from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mycode.agent.events import RunStatus
from mycode.web.server import create_web_app, run_web_server
from tests.test_server import _BlockingRuntime, _FakeRuntime


def _static(tmp_path: Path) -> Path:
    root = tmp_path / "static"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>MyCode</title>", encoding="utf-8")
    return root


def _app(tmp_path: Path):
    return create_web_app(
        token="test-token",
        allowed_origin="http://testserver",
        static_dir=_static(tmp_path),
        runtime_factory=lambda **kwargs: _FakeRuntime(tmp_path),
    )


def test_web_serves_static_with_security_headers(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert response.headers["cache-control"] == "no-store"


def test_no_open_prints_authenticated_url(monkeypatch, capsys) -> None:
    import uvicorn

    class FakeListener:
        def getsockname(self):
            return ("127.0.0.1", 8765)

    class FakeServer:
        def __init__(self, _config) -> None:
            pass

        def run(self, *, sockets) -> None:
            assert len(sockets) == 1

    monkeypatch.setattr("mycode.web.server._listen_socket", lambda _port: FakeListener())
    monkeypatch.setattr("mycode.web.server.create_web_app", lambda **_kwargs: object())
    monkeypatch.setattr("mycode.web.server.secrets.token_urlsafe", lambda _size: "test-token")
    monkeypatch.setattr(uvicorn, "Config", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(uvicorn, "Server", FakeServer)

    run_web_server(port=8765, open_browser=False)

    assert "http://127.0.0.1:8765/#token=test-token" in capsys.readouterr().out


def test_websocket_auth_and_rpc_round_trip(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    with TestClient(_app(tmp_path)) as client:
        with client.websocket_connect("/ws", headers={"origin": "http://testserver"}) as websocket:
            websocket.send_json({"type": "auth", "token": "test-token"})
            assert websocket.receive_json() == {"type": "authenticated"}
            websocket.send_json({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            initialized = websocket.receive_json()
            assert initialized["result"]["capabilities"]["web"] is True
            websocket.send_json({"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {}})
            assert websocket.receive_json()["result"]["session"]["model"] == "fake-model"


@pytest.mark.parametrize(
    ("headers", "auth"),
    [
        ({"origin": "http://evil.invalid"}, None),
        ({"origin": "http://testserver"}, {"type": "auth", "token": "wrong"}),
    ],
)
def test_websocket_rejects_invalid_origin_or_token(tmp_path, headers, auth) -> None:
    with TestClient(_app(tmp_path)) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws", headers=headers) as websocket:
                if auth is not None:
                    websocket.send_json(auth)
                    websocket.receive_json()


def test_websocket_allows_only_one_client(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    with TestClient(_app(tmp_path)) as client:
        with client.websocket_connect("/ws", headers={"origin": "http://testserver"}) as first:
            first.send_json({"type": "auth", "token": "test-token"})
            assert first.receive_json()["type"] == "authenticated"
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect("/ws", headers={"origin": "http://testserver"}) as second:
                    second.receive_json()


def test_websocket_disconnect_cancels_active_run(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    class TrackedRuntime(_BlockingRuntime):
        def __init__(self, root: Path) -> None:
            super().__init__(root)
            self.finished = threading.Event()
            self.final_status: str | None = None

        def run_prompt(self, *args, **kwargs):
            result = super().run_prompt(*args, **kwargs)
            self.final_status = result.status
            self.finished.set()
            return result

    runtime = TrackedRuntime(tmp_path)
    app = create_web_app(
        token="test-token",
        allowed_origin="http://testserver",
        static_dir=_static(tmp_path),
        runtime_factory=lambda **kwargs: runtime,
    )
    with TestClient(app) as client:
        with client.websocket_connect("/ws", headers={"origin": "http://testserver"}) as websocket:
            websocket.send_json({"type": "auth", "token": "test-token"})
            websocket.receive_json()
            websocket.send_json({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            websocket.receive_json()
            websocket.send_json({"jsonrpc": "2.0", "id": 2, "method": "run/start", "params": {"prompt": "wait"}})
            websocket.receive_json()
            assert runtime.entered.wait(timeout=1.0)
            # Close explicitly while TestClient's portal is still alive so the
            # ASGI disconnect event is delivered before app shutdown.
            websocket.close()
            assert runtime.finished.wait(timeout=2.0)
            assert runtime.final_status == RunStatus.CANCELLED
    for _ in range(100):
        if not any(thread.name.startswith("mycode-run-") and thread.is_alive() for thread in threading.enumerate()):
            break
        time.sleep(0.01)
    assert not any(thread.name.startswith("mycode-run-") and thread.is_alive() for thread in threading.enumerate())
