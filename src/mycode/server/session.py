"""Transport-neutral JSON-RPC session for local MyCode frontends."""

from __future__ import annotations

import difflib
import os
import threading
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from mycode import __version__
from mycode.agent.events import AgentEvent
from mycode.agent.runner import CancellationToken
from mycode.approvals import PERMISSION_MODES, ApprovalRequest
from mycode.checkpoint import Checkpoint
from mycode.model_store import (
    MODEL_CATALOG_METADATA,
    MODEL_CATALOGS,
    PRESETS,
    CredentialStore,
    ModelProfile,
    ModelStore,
    ModelStoreError,
    validate_profile,
)
from mycode.permissions import check_read_path
from mycode.runtime import COLLABORATION_MODES, MyCodeRuntime
from mycode.server.protocol import PROTOCOL_VERSION, ProtocolError, event_payload, notification
from mycode.server.workspace import WorkspaceError, list_workspace, read_workspace_file, search_workspace


class MessageWriter(Protocol):
    def send(self, payload: dict[str, Any]) -> None: ...


@dataclass
class PendingApproval:
    event: threading.Event
    approved: bool = False
    scope: str = "once"
    cache_key: str | None = None


class RpcSession:
    """One-workspace RPC state machine shared by stdio and WebSocket transports."""

    def __init__(
        self,
        *,
        writer: MessageWriter,
        runtime: MyCodeRuntime | None = None,
        runtime_factory=MyCodeRuntime.from_environment,
        approval_timeout: float = 300.0,
    ) -> None:
        self.writer = writer
        self.runtime = runtime
        self.runtime_factory = runtime_factory
        self.approval_timeout = approval_timeout
        self.initialized = runtime is not None
        self.shutdown_requested = False
        self._run_thread: threading.Thread | None = None
        self._run_token: CancellationToken | None = None
        self._run_id: str | None = None
        self._pending: dict[str, PendingApproval] = {}
        self._pending_lock = threading.Lock()
        self._approval_cache: set[str] = set()

    @property
    def run_active(self) -> bool:
        return self._run_thread is not None and self._run_thread.is_alive()

    def close(self) -> None:
        self._cancel_active()
        self._deny_pending()
        if self._run_thread is not None:
            self._run_thread.join(timeout=5.0)
        if self.runtime is not None:
            close = getattr(self.runtime, "close", None)
            if callable(close):
                close()

    def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "initialize":
            return self._initialize(params)
        if method in {"exit", "shutdown"}:
            self.close()
            self.shutdown_requested = True
            return None
        if not self.initialized or self.runtime is None:
            raise ProtocolError(-32002, "Server not initialized")
        handlers = {
            "session/list": self._session_list,
            "session/new": self._session_new,
            "session/open": self._session_open,
            "session/delete": self._session_delete,
            "session/diff": self._session_diff,
            "session/undo": self._session_undo,
            "session/compact": self._session_compact,
            "workspace/list": self._workspace_list,
            "workspace/read": self._workspace_read,
            "workspace/search": self._workspace_search,
            "model/list": self._model_list,
            "model/save": self._model_save,
            "model/use": self._model_use,
            "model/remove": self._model_remove,
            "run/start": self._start_run,
            "run/cancel": self._cancel_run,
            "permission/respond": self._permission_response,
        }
        handler = handlers.get(method)
        if handler is None:
            raise ProtocolError(-32601, f"Method not found: {method}")
        try:
            return handler(params)
        except WorkspaceError as exc:
            raise ProtocolError(-32020, str(exc)) from exc
        except ModelStoreError as exc:
            raise ProtocolError(-32030, str(exc)) from exc

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        workspace = Path.cwd().resolve()
        workspace_raw = params.get("workspace")
        if workspace_raw is not None and Path(str(workspace_raw)).expanduser().resolve() != workspace:
            raise ProtocolError(
                -32001,
                "Workspace must match the server process cwd",
                {"cwd": str(workspace), "requested": str(workspace_raw)},
            )
        if self.runtime is None:
            try:
                self.runtime = self.runtime_factory(project_root=workspace)
            except Exception as exc:
                raise ProtocolError(
                    -32003,
                    "Runtime initialization failed",
                    {"type": type(exc).__name__, "detail": str(exc)},
                ) from exc
        self.initialized = True
        return self._runtime_payload()

    def _runtime_payload(self) -> dict[str, Any]:
        assert self.runtime is not None
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "serverVersion": __version__,
            "workspace": str(Path.cwd().resolve()),
            "model": self.runtime.info.model,
            "provider": self.runtime.info.provider,
            "capabilities": {
                "agentEvents": True,
                "cancellation": True,
                "permissions": True,
                "approvalScopes": ["once", "run"],
                "sessions": True,
                "undo": True,
                "tools": True,
                "mcp": True,
                "skills": True,
                "workspace": True,
                "models": True,
                "web": True,
                "collaborationModes": sorted(COLLABORATION_MODES),
                "permissionModes": sorted(PERMISSION_MODES),
            },
        }

    def _require_idle(self) -> None:
        if self.run_active:
            raise ProtocolError(-32010, "An agent run is already active", {"runId": self._run_id})

    def _session_list(self, params: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
        assert self.runtime is not None
        return {"sessions": [self.runtime.session_payload(item) for item in self.runtime.list_sessions()]}

    def _session_new(self, params: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
        self._require_idle()
        assert self.runtime is not None
        session = self.runtime.new_session()
        return {"session": self.runtime.session_payload(session, include_messages=True)}

    def _session_open(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_idle()
        assert self.runtime is not None
        session_id = _require_string(params, "sessionId")
        try:
            session = self.runtime.get_session(session_id)
        except LookupError as exc:
            raise ProtocolError(-32004, str(exc)) from exc
        return {"session": self.runtime.session_payload(session, include_messages=True)}

    def _session_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_idle()
        assert self.runtime is not None
        session_id = _require_string(params, "sessionId")
        try:
            session = self.runtime.get_session(session_id)
        except LookupError as exc:
            raise ProtocolError(-32004, str(exc)) from exc
        session.path.unlink(missing_ok=True)
        return {"deleted": True, "sessionId": session_id}

    def _session_diff(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = _require_string(params, "sessionId")
        checkpoint = Checkpoint.latest(session_id)
        if checkpoint is None:
            return {"checkpointId": None, "files": [], "diff": ""}
        files: list[dict[str, Any]] = []
        combined: list[str] = []
        root = Path.cwd().resolve()
        for change in checkpoint.files:
            target, error = check_read_path(change.path, root)
            if error or target is None:
                continue
            try:
                current = target.read_text(encoding="utf-8") if target.is_file() else ""
            except (OSError, UnicodeDecodeError):
                current = ""
            before = change.before or ""
            diff = "".join(
                difflib.unified_diff(
                    before.splitlines(keepends=True),
                    current.splitlines(keepends=True),
                    fromfile=f"a/{change.path}",
                    tofile=f"b/{change.path}",
                )
            )
            files.append({"path": change.path, "kind": change.kind, "diff": diff})
            combined.append(diff)
        return {"checkpointId": checkpoint.id, "files": files, "diff": "\n".join(combined)}

    def _session_undo(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_idle()
        session_id = _require_string(params, "sessionId")
        checkpoint = Checkpoint.latest(session_id)
        if checkpoint is None:
            return {
                "undone": False,
                "checkpointId": None,
                "files": [],
                "summary": "当前会话没有可撤销的文件修改",
            }
        files = checkpoint.changed_paths()
        summary = checkpoint.undo()
        failed = summary.startswith("错误:") or "\n错误:\n" in summary
        return {
            "undone": not failed,
            "checkpointId": checkpoint.id,
            "files": files,
            "summary": summary,
        }

    def _session_compact(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_idle()
        assert self.runtime is not None
        session_id = params.get("sessionId")
        if session_id is not None and not isinstance(session_id, str):
            raise ProtocolError(-32602, "sessionId must be a string")
        try:
            session = self.runtime.get_session(session_id)
        except LookupError as exc:
            raise ProtocolError(-32004, str(exc)) from exc
        compacted = self.runtime.compact_session(session)
        return {"compacted": compacted, "sessionId": session.id}

    def _workspace_list(self, params: dict[str, Any]) -> dict[str, Any]:
        return list_workspace(str(params.get("path", ".")), Path.cwd().resolve())

    def _workspace_read(self, params: dict[str, Any]) -> dict[str, Any]:
        return read_workspace_file(_require_string(params, "path"), Path.cwd().resolve())

    def _workspace_search(self, params: dict[str, Any]) -> dict[str, Any]:
        query = _require_string(params, "query")
        path = str(params.get("path", "."))
        limit = params.get("limit", 100)
        if not isinstance(limit, int):
            raise ProtocolError(-32602, "limit must be an integer")
        return search_workspace(query, path, Path.cwd().resolve(), limit)

    def _model_list(self, params: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
        store = ModelStore.load()
        return {
            "active": store.active,
            "profiles": [_profile_payload(profile, store.active == name) for name, profile in sorted(store.profiles.items())],
            "presets": [_profile_payload(profile, False, include_key=False) for _, profile in sorted(PRESETS.items())],
            "catalogs": MODEL_CATALOGS,
            "catalogMetadata": MODEL_CATALOG_METADATA,
        }

    def _model_save(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_idle()
        raw = params.get("profile")
        if not isinstance(raw, dict):
            raise ProtocolError(-32602, "profile must be an object")
        name = _require_string(raw, "name")
        profile = ModelProfile(
            name=name,
            provider=_require_string(raw, "provider"),
            model=_require_string(raw, "model"),
            base_url=_optional_string(raw.get("baseUrl")),
            api_key_env=_optional_string(raw.get("apiKeyEnv")),
            credential_id=f"profile:{name}",
            max_tokens=_optional_int(raw.get("maxTokens"), "maxTokens"),
            temperature=_optional_float(raw.get("temperature"), "temperature"),
            top_p=_optional_float(raw.get("topP"), "topP"),
            thinking=_optional_choice(raw.get("thinking"), "thinking", {"enabled", "disabled"}),
            thinking_format=_optional_choice(
                raw.get("thinkingFormat"),
                "thinkingFormat",
                {"standard", "qwen", "openai", "anthropic", "none"},
            ),
            thinking_budget=_optional_int(raw.get("thinkingBudget"), "thinkingBudget"),
            reasoning_effort=_optional_choice(
                raw.get("reasoningEffort"),
                "reasoningEffort",
                {"none", "minimal", "low", "medium", "high", "max", "xhigh"},
            ),
        )
        validate_profile(profile)
        store = ModelStore.load()
        replace = bool(params.get("replace", name in store.profiles))
        store.add(profile, replace=replace)
        api_key = params.get("apiKey")
        if api_key is not None:
            if not isinstance(api_key, str):
                raise ProtocolError(-32602, "apiKey must be a string")
            CredentialStore().set(profile.credential_id or f"profile:{name}", api_key)
        return {"profile": _profile_payload(profile, store.active == name)}

    def _model_use(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_idle()
        name = _require_string(params, "name")
        store = ModelStore.load()
        previous = store.active
        store.use(name)
        try:
            replacement = self.runtime_factory(project_root=Path.cwd().resolve())
        except Exception as exc:
            store.active = previous
            store.save()
            raise ProtocolError(-32031, "Model activation failed", {"type": type(exc).__name__, "detail": str(exc)}) from exc
        previous_runtime = self.runtime
        self.runtime = replacement
        self.initialized = True
        if previous_runtime is not None:
            close = getattr(previous_runtime, "close", None)
            if callable(close):
                close()
        session = replacement.new_session()
        return {"runtime": self._runtime_payload(), "session": replacement.session_payload(session, include_messages=True)}

    def _model_remove(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_idle()
        name = _require_string(params, "name")
        delete_key = params.get("deleteKey")
        if not isinstance(delete_key, bool):
            raise ProtocolError(-32602, "deleteKey must be a boolean")
        store = ModelStore.load()
        if store.active == name:
            raise ProtocolError(-32032, "Cannot remove the active model profile")
        profile = store.remove(name)
        key_deleted = False
        if delete_key and profile.credential_id:
            key_deleted = CredentialStore().delete(profile.credential_id)
        return {"removed": name, "keyDeleted": key_deleted, "active": store.active}

    def _start_run(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_idle()
        prompt = _require_string(params, "prompt")
        session_id = params.get("sessionId")
        if session_id is not None and not isinstance(session_id, str):
            raise ProtocolError(-32602, "sessionId must be a string")
        assert self.runtime is not None
        try:
            session = self.runtime.get_session(session_id)
        except LookupError as exc:
            raise ProtocolError(-32004, str(exc)) from exc
        budget = params.get("budgetUsd")
        if budget is not None and not isinstance(budget, (int, float)):
            raise ProtocolError(-32602, "budgetUsd must be a number")
        collaboration_mode = str(params.get("collaborationMode", "default"))
        if collaboration_mode not in COLLABORATION_MODES:
            raise ProtocolError(-32602, "collaborationMode must be default, plan, or review")
        permission_mode = str(params.get("permissionMode", "standard"))
        if permission_mode not in PERMISSION_MODES:
            raise ProtocolError(-32602, "permissionMode must be standard, read-only, or full-access")
        run_id = uuid.uuid4().hex[:12]
        token = CancellationToken()
        self._run_id = run_id
        self._run_token = token
        self._approval_cache.clear()

        def run() -> None:
            try:
                assert self.runtime is not None
                result = self.runtime.run_prompt(
                    session,
                    prompt,
                    sink=self._event_sink,
                    cancellation_token=token,
                    approval=self._request_approval,
                    budget_usd=float(budget) if budget is not None else None,
                    run_id=run_id,
                    collaboration_mode=collaboration_mode,
                    permission_mode=permission_mode,
                )
                payload = {key: value for key, value in asdict(result).items() if key != "events"}
                payload["sessionId"] = session.id
                if payload.get("trace_path"):
                    payload["tracePath"] = payload.pop("trace_path")
                self.writer.send(notification("run/result", payload))
            except Exception as exc:  # noqa: BLE001
                self.writer.send(
                    notification(
                        "run/result",
                        {
                            "run_id": run_id,
                            "sessionId": session.id,
                            "status": "failed",
                            "final_text": None,
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                    )
                )
            finally:
                self._deny_pending()
                self._approval_cache.clear()
                self._run_token = None

        self._run_thread = threading.Thread(target=run, name=f"mycode-run-{run_id}", daemon=True)
        self._run_thread.start()
        return {"runId": run_id, "sessionId": session.id}

    def _event_sink(self, event: AgentEvent, attachments: dict[str, Any]) -> None:  # noqa: ARG002
        self.writer.send(notification("agent/event", event_payload(event)))

    def _request_approval(self, request: ApprovalRequest) -> bool:
        cache_key = _approval_cache_key(request)
        if cache_key in self._approval_cache:
            return True
        approval_id = uuid.uuid4().hex[:12]
        pending = PendingApproval(threading.Event(), cache_key=cache_key)
        with self._pending_lock:
            self._pending[approval_id] = pending
        self.writer.send(
            notification(
                "permission/request",
                {
                    "approvalId": approval_id,
                    "runId": self._run_id,
                    "canRememberForRun": True,
                    **asdict(request),
                },
            )
        )
        completed = pending.event.wait(timeout=self.approval_timeout)
        with self._pending_lock:
            self._pending.pop(approval_id, None)
        if completed and pending.approved and pending.scope == "run" and pending.cache_key:
            self._approval_cache.add(pending.cache_key)
        return completed and pending.approved

    def _permission_response(self, params: dict[str, Any]) -> dict[str, Any]:
        approval_id = _require_string(params, "approvalId")
        approved = params.get("approved")
        if not isinstance(approved, bool):
            raise ProtocolError(-32602, "approved must be a boolean")
        scope = params.get("scope", "once")
        if scope not in {"once", "run"}:
            raise ProtocolError(-32602, "scope must be once or run")
        with self._pending_lock:
            pending = self._pending.get(approval_id)
        if pending is None:
            raise ProtocolError(-32011, "Unknown or expired approval", {"approvalId": approval_id})
        pending.approved = approved
        pending.scope = str(scope)
        pending.event.set()
        return {"accepted": True}

    def _cancel_run(self, params: dict[str, Any]) -> dict[str, Any]:
        run_id = params.get("runId")
        if run_id is not None and run_id != self._run_id:
            raise ProtocolError(-32012, "Run is not active", {"runId": run_id})
        cancelled = self._run_token is not None
        self._cancel_active()
        return {"cancelled": cancelled}

    def _cancel_active(self) -> None:
        if self._run_token is not None:
            self._run_token.cancel()

    def _deny_pending(self) -> None:
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for request in pending:
            request.approved = False
            request.event.set()


def _approval_cache_key(request: ApprovalRequest) -> str:
    if request.command:
        target = " ".join(request.command.split())
    elif request.display_path:
        target = request.display_path.replace("\\", "/")
    else:
        target = request.action or request.prompt
    return f"{request.kind}:{request.action or ''}:{target}"


def _profile_payload(profile: ModelProfile, active: bool, *, include_key: bool = True) -> dict[str, Any]:
    source = "missing"
    if include_key:
        if profile.api_key_env and os.environ.get(profile.api_key_env, "").strip():
            source = f"environment:{profile.api_key_env}"
        elif profile.credential_id:
            try:
                source = "keyring" if CredentialStore().get(profile.credential_id) else "missing"
            except ModelStoreError:
                source = "keyring-unavailable"
    return {
        "name": profile.name,
        "provider": profile.provider,
        "model": profile.model,
        "baseUrl": profile.base_url,
        "apiKeyEnv": profile.api_key_env,
        "maxTokens": profile.max_tokens,
        "temperature": profile.temperature,
        "topP": profile.top_p,
        "thinking": profile.thinking,
        "thinkingFormat": profile.thinking_format,
        "thinkingBudget": profile.thinking_budget,
        "reasoningEffort": profile.reasoning_effort,
        "active": active,
        "keySource": source,
        "keyConfigured": source not in {"missing", "keyring-unavailable"},
    }


def _require_string(params: dict[str, Any], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(-32602, f"{key} must be a non-empty string")
    return value.strip()


def _optional_string(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ProtocolError(-32602, "optional string field has invalid type")
    return value


def _optional_int(value: object, name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int):
        raise ProtocolError(-32602, f"{name} must be an integer")
    return value


def _optional_float(value: object, name: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)):
        raise ProtocolError(-32602, f"{name} must be a number")
    return float(value)


def _optional_choice(value: object, name: str, choices: set[str]) -> str | None:
    result = _optional_string(value)
    if result is not None and result not in choices:
        raise ProtocolError(-32602, f"{name} must be one of: {', '.join(sorted(choices))}")
    return result


__all__ = ["MessageWriter", "PendingApproval", "RpcSession"]
