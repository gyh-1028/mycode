"""Session persistence: archive a conversation and resume it later.

Stores the *internal normalized messages* verbatim — the OpenAI-format dict list
exactly as the loop builds it: each assistant turn keeps its ``tool_calls`` block
and is immediately followed by the matching ``tool`` result messages, in order.
Nothing is flattened into a custom shape, so on resume the messages go straight
back to the provider (which converts to its own API format).

Layout: ``.mycode/sessions/<id>.json`` =
``{id, model, provider, schema_version, created_at, updated_at, messages}``.

Schema versioning
-----------------
* Legacy files without ``schema_version`` are treated as v0.
* New files are written as v1.
* A sequential migration registry upgrades v0 -> v1 in memory; the next save
  persists the new schema version.
* Files with ``schema_version`` greater than the current version are rejected to
  prevent old clients from corrupting newer data.
* Corrupt files are never deleted or overwritten. ``Session.load`` raises a
  readable ``SessionError``; ``Session.list_all`` skips them and logs the path.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from mycode.persistence import atomic_write_text, bytes_fingerprint, file_fingerprint, path_lock

CURRENT_SCHEMA_VERSION = 1
LEGACY_SCHEMA_VERSION = 0

SESSIONS_DIR = Path(".mycode") / "sessions"

_LOGGER = logging.getLogger("mycode.session")


class SessionError(Exception):
    """Readable error for corrupt/invalid session files."""


class UnsupportedSchemaError(SessionError):
    """Session file was written by a newer client."""


class SessionConflictError(SessionError):
    """The session changed on disk after this object was loaded."""


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _resolve_dir(base_dir: str | Path | None) -> Path:
    return SESSIONS_DIR if base_dir is None else Path(base_dir)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def _validate_string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise SessionError(f"字段 {field} 必须是字符串,得到 {type(value).__name__}")
    return value


def _validate_optional_string(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _validate_string(value, field)


def _validate_message(msg: Any, index: int) -> None:
    if not isinstance(msg, dict):
        raise SessionError(f"messages[{index}] 必须是对象,得到 {type(msg).__name__}")
    role = msg.get("role")
    if role not in {"system", "user", "assistant", "tool"}:
        raise SessionError(
            f"messages[{index}].role 必须是 system/user/assistant/tool 之一,得到 {role!r}"
        )
    content = msg.get("content")
    if content is not None and not isinstance(content, str):
        raise SessionError(
            f"messages[{index}].content 必须是字符串或 null,得到 {type(content).__name__}"
        )
    tool_calls = msg.get("tool_calls")
    if tool_calls is not None:
        if not isinstance(tool_calls, list):
            raise SessionError(
                f"messages[{index}].tool_calls 必须是数组,得到 {type(tool_calls).__name__}"
            )
        for tc_idx, tc in enumerate(tool_calls):
            if not isinstance(tc, dict):
                raise SessionError(
                    f"messages[{index}].tool_calls[{tc_idx}] 必须是对象"
                )
            if not isinstance(tc.get("id"), str):
                raise SessionError(
                    f"messages[{index}].tool_calls[{tc_idx}].id 必须是字符串"
                )
            if tc.get("type") != "function":
                raise SessionError(
                    f"messages[{index}].tool_calls[{tc_idx}].type 必须是 'function'"
                )
            fn = tc.get("function")
            if not isinstance(fn, dict):
                raise SessionError(
                    f"messages[{index}].tool_calls[{tc_idx}].function 必须是对象"
                )
            if not isinstance(fn.get("name"), str):
                raise SessionError(
                    f"messages[{index}].tool_calls[{tc_idx}].function.name 必须是字符串"
                )
            if not isinstance(fn.get("arguments"), str):
                raise SessionError(
                    f"messages[{index}].tool_calls[{tc_idx}].function.arguments 必须是字符串"
                )
    if role == "tool":
        if not isinstance(msg.get("tool_call_id"), str):
            raise SessionError(f"messages[{index}].tool_call_id 必须是字符串")


def _validate_pairing(messages: list[dict[str, Any]]) -> None:
    """Ensure every assistant tool_call is followed by a matching tool result."""
    pending: set[str] = set()
    for idx, msg in enumerate(messages):
        role = msg.get("role")
        if role == "assistant":
            for tc in msg.get("tool_calls") or []:
                pending.add(tc["id"])
        elif role == "tool":
            tcid = msg.get("tool_call_id")
            if tcid not in pending:
                raise SessionError(
                    f"messages[{idx}] 是孤立 tool result:tool_call_id={tcid!r} 没有前置的 assistant tool_call"
                )
            pending.discard(tcid)
    if pending:
        raise SessionError(
            f"存在未匹配的 assistant tool_call: {sorted(pending)}"
        )


def _validate_data(data: dict[str, Any], *, source: str) -> None:
    if not isinstance(data, dict):
        raise SessionError(f"{source}: 顶层必须是对象")
    _validate_string(data.get("id"), "id")
    _validate_string(data.get("model"), "model")
    _validate_string(data.get("provider"), "provider")
    _validate_string(data.get("created_at"), "created_at")
    _validate_string(data.get("updated_at"), "updated_at")
    messages = data.get("messages")
    if not isinstance(messages, list):
        raise SessionError(f"{source}: messages 必须是数组")
    for idx, msg in enumerate(messages):
        _validate_message(msg, idx)
    _validate_pairing(messages)


# --------------------------------------------------------------------------- #
# Migration registry
# --------------------------------------------------------------------------- #
Migration = Callable[[dict[str, Any]], dict[str, Any]]


def _migrate_v0_to_v1(data: dict[str, Any]) -> dict[str, Any]:
    """In-memory migration for legacy sessions: add schema_version, keep messages."""
    data = dict(data)
    data["schema_version"] = CURRENT_SCHEMA_VERSION
    return data


_MIGRATIONS: dict[int, Migration] = {
    LEGACY_SCHEMA_VERSION: _migrate_v0_to_v1,
}


def _migrate(data: dict[str, Any]) -> dict[str, Any]:
    version = data.get("schema_version", LEGACY_SCHEMA_VERSION)
    if version == CURRENT_SCHEMA_VERSION:
        return data
    if version > CURRENT_SCHEMA_VERSION:
        raise UnsupportedSchemaError(
            f"Session schema_version={version} 高于当前支持的版本 {CURRENT_SCHEMA_VERSION},"
            "请升级 mycode 后再打开此会话。"
        )
    seen: set[int] = set()
    while version < CURRENT_SCHEMA_VERSION:
        if version in seen:
            raise SessionError(f"迁移循环 detected at schema_version={version}")
        seen.add(version)
        migrator = _MIGRATIONS.get(version)
        if migrator is None:
            raise SessionError(f"缺少从 schema_version={version} 到 {version + 1} 的迁移器")
        data = migrator(data)
        version = data.get("schema_version", version + 1)
    return data


# --------------------------------------------------------------------------- #
# Session dataclass
# --------------------------------------------------------------------------- #
@dataclass
class Session:
    id: str
    model: str
    provider: str
    created_at: str
    updated_at: str
    messages: list[dict[str, Any]]
    base_dir: Path
    schema_version: int = CURRENT_SCHEMA_VERSION
    _fingerprint: str | None = field(default=None, repr=False, compare=False)

    @property
    def path(self) -> Path:
        return self.base_dir / f"{self.id}.json"

    # -- construction ------------------------------------------------------- #
    @classmethod
    def new(
        cls,
        *,
        model: str,
        provider: str,
        messages: list[dict[str, Any]] | None = None,
        base_dir: str | Path | None = None,
    ) -> Session:
        now = _now_iso()
        sid = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        return cls(
            id=sid,
            model=model,
            provider=provider,
            created_at=now,
            updated_at=now,
            messages=messages if messages is not None else [],
            base_dir=_resolve_dir(base_dir),
            schema_version=CURRENT_SCHEMA_VERSION,
        )

    # -- persistence -------------------------------------------------------- #
    def save(self, messages: list[dict[str, Any]]) -> None:
        """原子写入当前 messages(写临时文件后 os.replace,避免半截文件)。"""
        updated_at = _now_iso()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "id": self.id,
            "model": self.model,
            "provider": self.provider,
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "updated_at": updated_at,
            "messages": messages,
        }
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        with path_lock(self.path):
            if file_fingerprint(self.path) != self._fingerprint:
                raise SessionConflictError(
                    f"session changed on disk; reload before saving: {self.id}"
                )
            self._fingerprint = atomic_write_text(self.path, content)
        self.messages = messages
        self.updated_at = updated_at

    # -- loading ------------------------------------------------------------ #
    @classmethod
    def _load_raw(cls, path: Path) -> tuple[dict[str, Any], str]:
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise SessionError(f"无法读取会话文件 {path}: {exc}") from exc
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SessionError(f"会话文件 JSON 解析失败 {path}: {exc}") from exc
        return data, bytes_fingerprint(raw)

    @classmethod
    def _from_data(
        cls,
        data: dict[str, Any],
        base_dir: Path,
        *,
        source: str,
        fingerprint: str | None = None,
    ) -> Session:
        data = _migrate(data)
        _validate_data(data, source=source)
        return cls(
            id=data.get("id", ""),
            model=data.get("model", ""),
            provider=data.get("provider", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            messages=data.get("messages", []),
            base_dir=base_dir,
            schema_version=data.get("schema_version", CURRENT_SCHEMA_VERSION),
            _fingerprint=fingerprint,
        )

    @classmethod
    def _from_file(cls, path: Path, base_dir: Path) -> Session | None:
        try:
            data, fingerprint = cls._load_raw(path)
            return cls._from_data(
                data,
                base_dir=base_dir,
                source=str(path),
                fingerprint=fingerprint,
            )
        except (OSError, json.JSONDecodeError, SessionError):
            _LOGGER.warning("corrupt session skipped during scan: %s", path)
            return None

    @classmethod
    def load(cls, session_id: str, base_dir: str | Path | None = None) -> Session | None:
        """Load a session by id.

        Returns ``None`` if the file does not exist. Raises ``SessionError`` if
        the file exists but is corrupt or uses an unsupported schema version.
        """
        directory = _resolve_dir(base_dir)
        path = directory / f"{session_id}.json"
        if not path.is_file():
            return None
        data, fingerprint = cls._load_raw(path)
        return cls._from_data(
            data,
            base_dir=directory,
            source=str(path),
            fingerprint=fingerprint,
        )

    @classmethod
    def list_all(cls, base_dir: str | Path | None = None) -> list[Session]:
        directory = _resolve_dir(base_dir)
        if not directory.is_dir():
            return []
        sessions: list[Session] = []
        for p in directory.glob("*.json"):
            session = cls._from_file(p, directory)
            if session is not None:
                sessions.append(session)
        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        return sessions

    @classmethod
    def latest(cls, base_dir: str | Path | None = None) -> Session | None:
        sessions = cls.list_all(base_dir)
        return sessions[0] if sessions else None

    @classmethod
    def corrupt_count(cls, base_dir: str | Path | None = None) -> int:
        """Count session files that exist but fail validation (not deleted)."""
        directory = _resolve_dir(base_dir)
        if not directory.is_dir():
            return 0
        count = 0
        for p in directory.glob("*.json"):
            try:
                data, fingerprint = cls._load_raw(p)
                cls._from_data(data, directory, source=str(p), fingerprint=fingerprint)
            except (OSError, json.JSONDecodeError, SessionError):
                count += 1
        return count

    # -- summaries (for the `sessions` listing) ----------------------------- #
    def first_user_text(self) -> str:
        for message in self.messages:
            if message.get("role") == "user":
                return str(message.get("content", ""))
        return ""

    def turn_count(self) -> int:
        return sum(1 for m in self.messages if m.get("role") == "user")
