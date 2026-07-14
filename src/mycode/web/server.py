"""FastAPI/WebSocket transport for the local MyCode workbench."""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import socket
import threading
import webbrowser
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from mycode.server.protocol import ProtocolError, error, success, validate_request
from mycode.server.session import RpcSession

AUTH_TIMEOUT_SECONDS = 5.0
STATIC_DIR = Path(__file__).with_name("static")


class WebRuntimeError(RuntimeError):
    pass


class WebSocketWriter:
    def __init__(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self.loop = loop
        self.queue = queue
        self._closed = threading.Event()

    def send(self, payload: dict[str, Any]) -> None:
        if self._closed.is_set() or self.loop.is_closed():
            return
        try:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, payload)
        except RuntimeError:
            # A browser/test transport may close its loop while the worker is
            # finishing. Notifications after disconnect are intentionally lost.
            return

    def close(self) -> None:
        self._closed.set()


def create_web_app(
    *,
    token: str,
    allowed_origin: str,
    static_dir: Path | None = None,
    runtime_factory=None,
):
    assets = (static_dir or STATIC_DIR).resolve()
    index_path = assets / "index.html"
    if not index_path.is_file():
        raise WebRuntimeError(f"Web 静态资源不存在:{index_path}。开发环境请先运行 npm run build:web")

    app = FastAPI(title="MyCode Local Web", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.active_client = False
    app.state.client_lock = asyncio.Lock()

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; connect-src 'self' ws:; img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; font-src 'self'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    app.mount("/assets", StaticFiles(directory=assets / "assets"), name="assets")

    @app.get("/")
    async def index():
        return FileResponse(index_path)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        origin = websocket.headers.get("origin")
        if origin != allowed_origin:
            await websocket.close(code=1008, reason="invalid origin")
            return
        async with app.state.client_lock:
            if app.state.active_client:
                await websocket.close(code=1008, reason="another client is active")
                return
            app.state.active_client = True

        await websocket.accept()
        rpc: RpcSession | None = None
        writer: WebSocketWriter | None = None
        sender: asyncio.Task[None] | None = None
        try:
            try:
                auth = await asyncio.wait_for(websocket.receive_json(), timeout=AUTH_TIMEOUT_SECONDS)
            except (TimeoutError, ValueError):
                await websocket.close(code=1008, reason="authentication timeout")
                return
            if not isinstance(auth, dict) or auth.get("type") != "auth" or not secrets.compare_digest(str(auth.get("token", "")), token):
                await websocket.close(code=1008, reason="authentication failed")
                return

            queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
            writer = WebSocketWriter(asyncio.get_running_loop(), queue)
            rpc = (
                RpcSession(writer=writer)
                if runtime_factory is None
                else RpcSession(writer=writer, runtime_factory=runtime_factory)
            )

            async def send_messages() -> None:
                while True:
                    await websocket.send_json(await queue.get())

            sender = asyncio.create_task(send_messages())
            await websocket.send_json({"type": "authenticated"})

            while True:
                message = await websocket.receive_json()
                request_id = message.get("id") if isinstance(message, dict) else None
                try:
                    request_id, method, params = validate_request(message)
                    result = await asyncio.to_thread(rpc.dispatch, method, params)
                    if request_id is not None:
                        await websocket.send_json(success(request_id, result))
                except ProtocolError as exc:
                    await websocket.send_json(error(request_id, exc.code, exc.message, exc.data))
                except Exception as exc:  # noqa: BLE001 - transport boundary
                    await websocket.send_json(
                        error(request_id, -32603, "Internal error", {"type": type(exc).__name__, "detail": str(exc)})
                    )
        except WebSocketDisconnect:
            pass
        finally:
            if writer is not None:
                writer.close()
            if rpc is not None:
                await asyncio.to_thread(rpc.close)
            if sender is not None:
                sender.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await sender
            async with app.state.client_lock:
                app.state.active_client = False

    return app


def _listen_socket(port: int) -> socket.socket:
    if port < 0 or port > 65535:
        raise WebRuntimeError("端口必须在 0 到 65535 之间")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(128)
    return listener


def run_web_server(*, port: int = 0, open_browser: bool = True) -> None:
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover
        raise WebRuntimeError("Web 工作台需要 web extra: pip install 'mycode-ai-cli[web]'") from exc

    listener = _listen_socket(port)
    actual_port = int(listener.getsockname()[1])
    origin = f"http://127.0.0.1:{actual_port}"
    token = secrets.token_urlsafe(32)
    app = create_web_app(token=token, allowed_origin=origin)
    url = f"{origin}/#token={token}"
    print(f"MyCode Web: {origin if open_browser else url}")
    print("仅允许本机访问。按 Ctrl+C 停止服务。")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    config = uvicorn.Config(app, log_level="warning", access_log=False)
    uvicorn.Server(config).run(sockets=[listener])


__all__ = [
    "AUTH_TIMEOUT_SECONDS",
    "STATIC_DIR",
    "WebRuntimeError",
    "WebSocketWriter",
    "create_web_app",
    "run_web_server",
]
