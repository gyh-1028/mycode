"""Deterministic local server used by Playwright visual QA."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

import uvicorn

from mycode.web.server import create_web_app
from tests.test_server import _FakeRuntime


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token", default="qa-token")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    temp_root = repo / ".pytmp"
    temp_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="web-preview-", dir=temp_root) as temporary:
        root = Path(temporary)
        (root / "src").mkdir()
        (root / "src" / "app.py").write_text(
            'def greet(name: str) -> str:\n    return f"Hello, {name}"\n',
            encoding="utf-8",
        )
        (root / "README.md").write_text("# Preview workspace\n", encoding="utf-8")
        os.chdir(root)
        os.environ["MYCODE_MODELS_FILE"] = str(root / "models.toml")
        runtime = _FakeRuntime(root, ask=True)
        session = runtime.new_session()
        session.messages.extend(
            [
                {"role": "user", "content": "请检查项目结构，并优化 greet 函数的错误处理。"},
                {"role": "assistant", "content": "我会先读取 `src/app.py`，确认调用方式后再给出修改。"},
            ]
        )
        session.save(session.messages)
        app = create_web_app(
            token=args.token,
            allowed_origin=f"http://127.0.0.1:{args.port}",
            static_dir=repo / "src" / "mycode" / "web" / "static",
            runtime_factory=lambda **kwargs: runtime,
        )
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
