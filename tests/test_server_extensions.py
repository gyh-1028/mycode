from __future__ import annotations

from pathlib import Path

from mycode.checkpoint import Checkpoint
from mycode.model_store import ModelProfile, ModelStore
from mycode.runtime import RuntimeInfo
from mycode.server.session import RpcSession
from mycode.session import Session
from tests.test_server import _FakeRuntime


class _Writer:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    def send(self, payload: dict) -> None:
        self.messages.append(payload)


class _ProfileRuntime(_FakeRuntime):
    def __init__(self, root: Path, profile: ModelProfile) -> None:
        super().__init__(root)
        self.info = RuntimeInfo(root, profile.model, profile.provider, ())

    def new_session(self, *, persist: bool = True) -> Session:
        session = Session.new(
            model=self.info.model,
            provider=self.info.provider,
            messages=[{"role": "system", "content": "test"}],
            base_dir=self.root / ".mycode" / "sessions",
        )
        if persist:
            session.save(session.messages)
        self.sessions.insert(0, session)
        return session


def _session(tmp_path: Path, monkeypatch, **kwargs) -> RpcSession:
    monkeypatch.chdir(tmp_path)
    return RpcSession(writer=_Writer(), runtime=_FakeRuntime(tmp_path), **kwargs)  # type: ignore[arg-type]


def test_workspace_structured_read_list_search_and_guards(tmp_path, monkeypatch) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=value", encoding="utf-8")
    (tmp_path / "binary.bin").write_bytes(b"a\0b")
    rpc = _session(tmp_path, monkeypatch)

    listed = rpc.dispatch("workspace/list", {"path": "."})
    assert {item["name"] for item in listed["entries"]} == {"src"}
    opened = rpc.dispatch("workspace/read", {"path": "src/app.py"})
    assert opened["language"] == "python"
    assert "hello" in opened["content"]
    searched = rpc.dispatch("workspace/search", {"query": "world", "limit": 10})
    assert searched["matches"][0]["path"] == "src/app.py"

    for path in (".env", "binary.bin", "../outside.txt"):
        try:
            rpc.dispatch("workspace/read", {"path": path})
        except Exception as exc:  # ProtocolError is the public boundary
            assert "读取" in str(exc) or "二进制" in str(exc) or "项目根目录" in str(exc)
        else:  # pragma: no cover
            raise AssertionError(f"expected access denial for {path}")


def test_workspace_denies_symlink_escape(tmp_path, monkeypatch) -> None:
    outside = tmp_path.parent / "outside-web-test.txt"
    outside.write_text("outside", encoding="utf-8")
    link = tmp_path / "escape.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        return
    rpc = _session(tmp_path, monkeypatch)
    try:
        rpc.dispatch("workspace/read", {"path": "escape.txt"})
    except Exception as exc:
        assert "项目根目录" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("symlink escape should be denied")


def test_session_diff_uses_latest_checkpoint(tmp_path, monkeypatch) -> None:
    target = tmp_path / "app.py"
    target.write_text("before\n", encoding="utf-8")
    rpc = _session(tmp_path, monkeypatch)
    session = rpc.dispatch("session/new", {})["session"]
    checkpoint = Checkpoint.begin(session_id=session["id"], task="edit", root=tmp_path)
    checkpoint.record_write(target, "before\n", True)
    target.write_text("after\n", encoding="utf-8")

    result = rpc.dispatch("session/diff", {"sessionId": session["id"]})
    assert result["files"][0]["path"] == "app.py"
    assert "-before" in result["diff"]
    assert "+after" in result["diff"]


def test_session_compact_rpc(tmp_path, monkeypatch) -> None:
    rpc = _session(tmp_path, monkeypatch)
    session = rpc.dispatch("session/new", {})["session"]
    result = rpc.dispatch("session/compact", {"sessionId": session["id"]})
    assert result["compacted"] is True
    assert result["sessionId"] == session["id"]
    assert rpc.runtime.compacted_session is not None
    assert rpc.runtime.compacted_session.id == session["id"]


def test_model_save_never_returns_secret(tmp_path, monkeypatch) -> None:
    models_path = tmp_path / "models.toml"
    monkeypatch.setenv("MYCODE_MODELS_FILE", str(models_path))
    captured: dict[str, str] = {}
    monkeypatch.setattr(
        "mycode.server.session.CredentialStore.set",
        lambda self, credential_id, value: captured.update({credential_id: value}),
    )
    monkeypatch.setattr("mycode.server.session.CredentialStore.get", lambda self, credential_id: "stored")
    rpc = _session(tmp_path, monkeypatch)
    result = rpc.dispatch(
        "model/save",
        {
            "profile": {
                "name": "local",
                "provider": "openai",
                "model": "test-model",
                "thinking": "enabled",
                "thinkingFormat": "qwen",
                "thinkingBudget": 8192,
                "reasoningEffort": "high",
            },
            "apiKey": "top-secret",
        },
    )
    assert captured["profile:local"] == "top-secret"
    assert "top-secret" not in repr(result)
    listed = rpc.dispatch("model/list", {})
    assert "top-secret" not in repr(listed)
    assert listed["profiles"][0]["keyConfigured"] is True
    assert listed["profiles"][0]["thinking"] == "enabled"
    assert listed["profiles"][0]["thinkingFormat"] == "qwen"
    assert listed["profiles"][0]["thinkingBudget"] == 8192
    assert listed["profiles"][0]["reasoningEffort"] == "high"
    assert {catalog["id"] for catalog in listed["catalogs"]} == {"anthropic", "deepseek", "gemini", "glm", "kimi", "kimi-coding", "minimax", "openai", "qwen"}
    deepseek = next(item for item in listed["catalogs"] if item["id"] == "deepseek")
    assert {item["id"] for item in deepseek["models"]} >= {"deepseek-v4-flash", "deepseek-v4-pro"}


def test_model_use_rebuilds_runtime_and_starts_new_session(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MYCODE_MODELS_FILE", str(tmp_path / "models.toml"))
    store = ModelStore.load()
    store.add(ModelProfile("first", "openai", "model-one", credential_id="profile:first"))
    store.add(ModelProfile("second", "anthropic", "model-two", credential_id="profile:second"))

    def factory(**kwargs):
        active = ModelStore.load().active_profile()
        assert active is not None
        return _ProfileRuntime(tmp_path, active)

    rpc = _session(tmp_path, monkeypatch, runtime_factory=factory)
    result = rpc.dispatch("model/use", {"name": "second"})

    assert result["runtime"]["model"] == "model-two"
    assert result["runtime"]["provider"] == "anthropic"
    assert result["session"]["model"] == "model-two"
    assert ModelStore.load().active == "second"
