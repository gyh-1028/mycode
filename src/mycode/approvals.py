"""UI-neutral approval requests for write, shell, and external tool actions."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

PERMISSION_MODES = {"standard", "read-only", "full-access"}


@dataclass(frozen=True)
class ApprovalRequest:
    """A potentially destructive action waiting for a user decision."""

    kind: str
    prompt: str
    display_path: str | None = None
    diff: str | None = None
    command: str | None = None
    risk: str | None = None
    action: str | None = None


ApprovalHandler = Callable[[ApprovalRequest], bool]

_CURRENT_HANDLER: ContextVar[ApprovalHandler | None] = ContextVar(
    "mycode_approval_handler", default=None
)
_CURRENT_PERMISSION_MODE: ContextVar[str] = ContextVar(
    "mycode_permission_mode", default="standard"
)


@contextmanager
def approval_handler(handler: ApprovalHandler | None) -> Iterator[None]:
    """Install an approval handler for the current execution context."""

    token = _CURRENT_HANDLER.set(handler)
    try:
        yield
    finally:
        _CURRENT_HANDLER.reset(token)


@contextmanager
def permission_scope(mode: str) -> Iterator[None]:
    """Apply a per-run permission profile without mutating persisted config."""

    normalized = (mode or "standard").strip().lower()
    if normalized not in PERMISSION_MODES:
        raise ValueError(f"未知权限模式: {mode}")
    token = _CURRENT_PERMISSION_MODE.set(normalized)
    try:
        yield
    finally:
        _CURRENT_PERMISSION_MODE.reset(token)


def effective_permission(configured: str) -> str:
    """Resolve a configured ask/allow/deny value against the active run mode."""

    mode = _CURRENT_PERMISSION_MODE.get()
    if mode == "full-access":
        return "allow"
    if mode == "read-only":
        return "deny"
    return configured


def decide(request: ApprovalRequest, fallback: Callable[[], bool]) -> bool:
    """Ask the active frontend, falling back to the legacy terminal prompt."""

    handler = _CURRENT_HANDLER.get()
    return fallback() if handler is None else bool(handler(request))


__all__ = [
    "PERMISSION_MODES",
    "ApprovalHandler",
    "ApprovalRequest",
    "approval_handler",
    "decide",
    "effective_permission",
    "permission_scope",
]
