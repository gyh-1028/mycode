"""Persistent per-task write checkpoints for undo and git integration."""

from __future__ import annotations

import json
import os
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

CHECKPOINTS_DIR = Path(".mycode") / "checkpoints"

_CURRENT: ContextVar[Checkpoint | None] = ContextVar("mycode_checkpoint", default=None)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _checkpoint_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]


@dataclass
class FileChange:
    path: str
    kind: str
    before: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {"path": self.path, "kind": self.kind}
        if self.before is not None:
            data["before"] = self.before
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FileChange:
        return cls(
            path=str(data.get("path", "")),
            kind=str(data.get("kind", "")),
            before=data.get("before"),
        )


@dataclass
class Checkpoint:
    id: str
    session_id: str
    task: str
    root: Path
    created_at: str = field(default_factory=_now_iso)
    files: list[FileChange] = field(default_factory=list)
    undone_at: str | None = None

    @property
    def path(self) -> Path:
        return CHECKPOINTS_DIR / self.session_id / f"{self.id}.json"

    @classmethod
    def begin(cls, *, session_id: str, task: str, root: str | Path | None = None) -> Checkpoint:
        return cls(
            id=_checkpoint_id(),
            session_id=session_id,
            task=task,
            root=(Path.cwd() if root is None else Path(root)).resolve(),
        )

    @classmethod
    def load(cls, path: Path) -> Checkpoint | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return cls(
            id=str(data.get("id", path.stem)),
            session_id=str(data.get("session_id", path.parent.name)),
            task=str(data.get("task", "")),
            root=Path(data.get("root", ".")).resolve(),
            created_at=str(data.get("created_at", "")),
            files=[FileChange.from_dict(item) for item in data.get("files", [])],
            undone_at=data.get("undone_at"),
        )

    @classmethod
    def latest(cls, session_id: str | None = None) -> Checkpoint | None:
        base = CHECKPOINTS_DIR / session_id if session_id else CHECKPOINTS_DIR
        if not base.exists():
            return None
        pattern = "*.json" if session_id else "*/*.json"
        checkpoints = [
            cp
            for p in base.glob(pattern)
            if (cp := cls.load(p)) is not None and cp.files and not cp.undone_at
        ]
        checkpoints.sort(key=lambda cp: cp.created_at, reverse=True)
        return checkpoints[0] if checkpoints else None

    def save(self) -> None:
        if not self.files:
            return
        payload = {
            "id": self.id,
            "session_id": self.session_id,
            "task": self.task,
            "root": str(self.root),
            "created_at": self.created_at,
            "undone_at": self.undone_at,
            "files": [file.to_dict() for file in self.files],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def record_write(self, target: Path, original: str, existed: bool) -> None:
        resolved = target.resolve()
        rel = resolved.relative_to(self.root).as_posix()
        existing = next((item for item in self.files if item.path == rel), None)
        if existing is not None:
            return
        self.files.append(
            FileChange(
                path=rel,
                kind="modified" if existed else "created",
                before=original if existed else None,
            )
        )
        self.save()

    def changed_paths(self) -> list[str]:
        return [file.path for file in self.files]

    def undo(self) -> str:
        if Path.cwd().resolve() != self.root:
            return f"错误:检查点属于其他项目目录:{self.root}"
        restored = 0
        deleted = 0
        errors: list[str] = []
        for change in reversed(self.files):
            target = (self.root / change.path).resolve()
            try:
                if change.kind == "created":
                    if target.exists():
                        if not target.is_file():
                            errors.append(f"{change.path}:不是文件,未删除")
                            continue
                        target.unlink()
                    deleted += 1
                elif change.kind == "modified":
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(change.before or "", encoding="utf-8")
                    restored += 1
                else:
                    errors.append(f"{change.path}:未知类型 {change.kind}")
            except OSError as exc:
                errors.append(f"{change.path}:{exc}")
        if not errors:
            self.undone_at = _now_iso()
            self.save()
        summary = f"已撤销检查点 {self.id}:还原 {restored} 个,删除新建 {deleted} 个。"
        if errors:
            summary += "\n错误:\n" + "\n".join(errors)
        return summary


def set_current_checkpoint(checkpoint: Checkpoint | None):
    return _CURRENT.set(checkpoint)


def reset_current_checkpoint(token) -> None:
    _CURRENT.reset(token)


def current_checkpoint() -> Checkpoint | None:
    return _CURRENT.get()


def record_file_write(target: Path, original: str, existed: bool) -> None:
    checkpoint = current_checkpoint()
    if checkpoint is not None:
        checkpoint.record_write(target, original, existed)
