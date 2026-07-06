"""Basic guardrails for tool execution.

These checks are deliberately small and deterministic. They reduce accidental
damage, but they are not a security sandbox. Real isolation needs OS-level
controls such as containers, VMs, or a restricted user account.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

DENIED_PREFIX = "错误:权限拒绝:"
SECURITY_NOTICE = (
    "安全说明:命令黑名单和项目路径限制只是基础防护,不是安全沙箱;"
    "真正隔离需要容器/受限用户等 OS 级手段。"
)

_SENSITIVE_EXACT_NAMES = {
    ".env",
    ".ssh",
    "id_rsa",
    "id_ed25519",
    "secret",
    "secrets",
    "token",
    "tokens",
}
_SENSITIVE_SUFFIXES = (".pem", ".key")
_SENSITIVE_MARKERS = ("secret", "token")

_CONTROL_TOKENS = {"|", ";", "&", "&&", "||"}
_PIPE_TO_SHELL_RE = re.compile(
    r"\b(?:curl|wget)\b(?:(?![;&]).)*\|\s*(?:sudo\s+)?(?:sh|bash)\b",
    re.IGNORECASE,
)
_POWERSHELL_IEX_RE = re.compile(
    r"\b(?:iwr|irm|invoke-webrequest|invoke-restmethod|curl|wget)\b(?:(?![;&]).)*\|\s*(?:iex|invoke-expression)\b",
    re.IGNORECASE,
)


def project_root(root: str | Path | None = None) -> Path:
    """Return the resolved project root.

    The current working directory is the project root for this CLI.
    """
    return (Path.cwd() if root is None else Path(root)).resolve()


def resolve_project_path(path: str | Path, root: str | Path | None = None) -> Path:
    """Resolve a path relative to the project root, following symlinks."""
    root_path = project_root(root)
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root_path / candidate
    return candidate.resolve()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_sensitive_name(name: str) -> bool:
    lower = name.lower()
    if lower == ".env" or lower.startswith(".env."):
        return True
    if lower in _SENSITIVE_EXACT_NAMES:
        return True
    if lower.endswith(_SENSITIVE_SUFFIXES):
        return True
    return any(marker in lower for marker in _SENSITIVE_MARKERS)


def is_sensitive_path(path: str | Path) -> bool:
    """Return True when any path component looks like a secret-bearing path."""
    return any(_is_sensitive_name(part) for part in Path(path).parts)


def check_project_path(
    path: str | Path, root: str | Path | None = None
) -> tuple[Path | None, str | None]:
    """Resolve path and ensure it stays inside the resolved project root."""
    root_path = project_root(root)
    resolved = resolve_project_path(path, root_path)
    if not _is_within(resolved, root_path):
        return None, f"{DENIED_PREFIX}路径不在项目根目录内:{path}"
    return resolved, None


def check_read_path(
    path: str | Path, root: str | Path | None = None
) -> tuple[Path | None, str | None]:
    """Resolve path, enforce project boundary, and deny sensitive reads."""
    resolved, error = check_project_path(path, root)
    if error is not None or resolved is None:
        return None, error
    if is_sensitive_path(resolved):
        return None, f"{DENIED_PREFIX}拒绝读取敏感路径:{path}"
    return resolved, None


def check_write_path(
    path: str | Path, root: str | Path | None = None
) -> tuple[Path | None, str | None]:
    """Resolve path for a write/edit: enforce project boundary, deny sensitive.

    Like check_read_path but with write-flavoured messages. The file need not
    exist yet (write_file may create it).
    """
    resolved, error = check_project_path(path, root)
    if error is not None or resolved is None:
        return None, error
    if is_sensitive_path(resolved):
        return None, f"{DENIED_PREFIX}拒绝写入敏感路径:{path}"
    return resolved, None


def sensitive_ripgrep_globs() -> tuple[str, ...]:
    """Glob patterns that keep ripgrep away from known sensitive paths."""
    return (
        ".env",
        ".env.*",
        "**/.env",
        "**/.env.*",
        ".ssh/**",
        "**/.ssh/**",
        "id_rsa",
        "**/id_rsa",
        "id_ed25519",
        "**/id_ed25519",
        "*.pem",
        "**/*.pem",
        "*.key",
        "**/*.key",
        "*secret*",
        "**/*secret*",
        "*token*",
        "**/*token*",
    )


def _command_name(token: str) -> str:
    name = token.strip("'\"").replace("\\", "/").rsplit("/", 1)[-1].lower()
    for suffix in (".exe", ".cmd", ".bat", ".com"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _tokens(command: str) -> list[str]:
    spaced = re.sub(r"([|;&])", r" \1 ", command)
    try:
        return shlex.split(spaced, posix=True)
    except ValueError:
        return spaced.split()


def _has_rm_rf_root(tokens: list[str]) -> bool:
    for idx, token in enumerate(tokens):
        if _command_name(token) != "rm":
            continue
        seen_recursive = False
        seen_force = False
        for arg in tokens[idx + 1 :]:
            if arg in _CONTROL_TOKENS:
                break
            lower = arg.lower()
            if lower.startswith("-"):
                seen_recursive = seen_recursive or "r" in lower
                seen_force = seen_force or "f" in lower
                continue
            if lower == "/" and seen_recursive and seen_force:
                return True
    return False


def _has_del_recursive(tokens: list[str]) -> bool:
    for idx, token in enumerate(tokens):
        if _command_name(token) != "del":
            continue
        for arg in tokens[idx + 1 :]:
            if arg in _CONTROL_TOKENS:
                break
            if arg.lower() == "/s":
                return True
    return False


def _has_rmdir_recursive(tokens: list[str]) -> bool:
    for idx, token in enumerate(tokens):
        if _command_name(token) not in {"rd", "rmdir"}:
            continue
        for arg in tokens[idx + 1 :]:
            if arg in _CONTROL_TOKENS:
                break
            if arg.lower() in {"/s", "-r", "-recurse"}:
                return True
    return False


def _has_remove_item_recursive_root(tokens: list[str]) -> bool:
    for idx, token in enumerate(tokens):
        if _command_name(token) not in {"remove-item", "rm", "ri", "del"}:
            continue
        seen_recursive = False
        for arg in tokens[idx + 1 :]:
            if arg in _CONTROL_TOKENS:
                break
            lower = arg.lower()
            if lower in {"-recurse", "-r"}:
                seen_recursive = True
                continue
            if seen_recursive and lower.rstrip("\\/") in {"c:", "d:", "/", "\\"}:
                return True
    return False


_PATH_OPERATORS = {
    "rm",
    "del",
    "remove-item",
    "ri",
    "rmdir",
    "rd",
    "mv",
    "move",
    "cp",
    "copy",
    "mkdir",
    "new-item",
}
_READ_COMMANDS = {
    "cat",
    "type",
    "ls",
    "dir",
    "rg",
    "grep",
    "findstr",
    "pytest",
}
_WRITE_COMMANDS = {
    "rm",
    "del",
    "remove-item",
    "ri",
    "rmdir",
    "rd",
    "mv",
    "move",
    "cp",
    "copy",
    "mkdir",
    "new-item",
    "git",
    "npm",
    "pnpm",
    "yarn",
    "pip",
    "python",
    "py",
}


def _looks_like_path_arg(arg: str) -> bool:
    if not arg or arg.startswith("-"):
        return False
    if arg.startswith("/") and len(arg) <= 3 and arg[1:].isalpha():
        return False
    if arg.startswith("/"):
        return True
    if any(ch in arg for ch in "*?$%"):
        return False
    if re.match(r"^[A-Za-z]:[\\/]", arg):
        return True
    return any(sep in arg for sep in ("/", "\\")) or arg.startswith(".")


def command_path_denial_reason(
    command: str, root: str | Path | None = None
) -> str | None:
    """Deny common file-operation commands that target paths outside the project.

    This is intentionally conservative and only inspects arguments to commands
    that normally operate on paths. It does not try to parse every shell syntax.
    """
    tokens = _tokens(command)
    root_path = project_root(root)
    for idx, token in enumerate(tokens):
        name = _command_name(token)
        if name not in _PATH_OPERATORS:
            continue
        for arg in tokens[idx + 1 :]:
            if arg in _CONTROL_TOKENS:
                break
            if not _looks_like_path_arg(arg):
                continue
            resolved = resolve_project_path(arg, root_path)
            if not _is_within(resolved, root_path):
                return f"{DENIED_PREFIX}命令路径不在项目根目录内:{arg}"
    return None


def classify_command_risk(command: str) -> str:
    """Return a coarse command risk tier for approval prompts."""
    if command_denial_reason(command) is not None:
        return "dangerous"
    tokens = _tokens(command)
    names = [_command_name(t) for t in tokens if t not in _CONTROL_TOKENS]
    if not names:
        return "unknown"
    first = names[0]
    if first in _READ_COMMANDS:
        return "read"
    if first in _WRITE_COMMANDS:
        if first == "git" and len(names) > 1 and names[1] in {"status", "diff", "log", "show"}:
            return "read"
        if first in {"python", "py"} and "-m" in tokens and "pytest" in tokens:
            return "read"
        return "write"
    return "unknown"


def command_denial_reason(command: str) -> str | None:
    """Return a denial reason when a command hits the destructive blacklist."""
    tokens = _tokens(command)
    if _has_rm_rf_root(tokens):
        return f"{DENIED_PREFIX}命令命中黑名单:rm -rf /"
    if _has_del_recursive(tokens):
        return f"{DENIED_PREFIX}命令命中黑名单:del /s"
    if _has_rmdir_recursive(tokens):
        return f"{DENIED_PREFIX}命令命中黑名单:rmdir/rd recursive"
    if _has_remove_item_recursive_root(tokens):
        return f"{DENIED_PREFIX}命令命中黑名单:Remove-Item recursive root"
    if _PIPE_TO_SHELL_RE.search(command):
        return f"{DENIED_PREFIX}命令命中黑名单:curl/wget | sh"
    if _POWERSHELL_IEX_RE.search(command):
        return f"{DENIED_PREFIX}命令命中黑名单:download | Invoke-Expression"

    for token in tokens:
        name = _command_name(token)
        if name.startswith("mkfs") or name in {
            "shutdown",
            "reboot",
            "format",
            "stop-computer",
            "restart-computer",
        }:
            return f"{DENIED_PREFIX}命令命中黑名单:{name}"
    return None


def is_command_allowed(command: str) -> bool:
    return command_denial_reason(command) is None and command_path_denial_reason(command) is None
