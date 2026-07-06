"""Shared ignore rules and file walking for the read-only tools."""

import fnmatch
import os
from collections.abc import Iterator
from pathlib import Path

# 遍历/列目录时一律忽略的目录名。
IGNORE_DIRS = frozenset(
    {".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build"}
)


def _ignore_patterns() -> list[str]:
    path = Path.cwd().resolve() / ".mycodeignore"
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [
        line.strip()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _match_ignore_pattern(rel: str, name: str, pattern: str, is_dir: bool) -> bool:
    negated = pattern.startswith("!")
    if negated:
        pattern = pattern[1:]
    if not pattern:
        return False
    if pattern.endswith("/"):
        pattern = pattern.rstrip("/")
        return is_dir and (rel == pattern or rel.startswith(pattern + "/") or fnmatch.fnmatch(name, pattern))
    if "/" in pattern:
        return fnmatch.fnmatch(rel, pattern)
    return fnmatch.fnmatch(name, pattern) or any(fnmatch.fnmatch(part, pattern) for part in rel.split("/"))


def is_ignored(path: Path, root: Path, *, is_dir: bool | None = None) -> bool:
    root = root.resolve()
    path = path.resolve()
    if is_dir is None:
        is_dir = path.is_dir()
    try:
        rel = path.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            return False
    if not rel or rel == ".":
        return False
    name = path.name
    if is_dir and name in IGNORE_DIRS:
        return True
    ignored = False
    for pattern in _ignore_patterns():
        negated = pattern.startswith("!")
        if _match_ignore_pattern(rel, name, pattern, is_dir):
            ignored = not negated
    return ignored


def walk_files(root: Path) -> Iterator[Path]:
    """产出 root 下的所有文件,剪掉 IGNORE_DIRS;root 本身是文件时只产出它。"""
    if root.is_file():
        base = root.parent
        if not is_ignored(root, base, is_dir=False):
            yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        # 原地修改 dirnames 以阻止 os.walk 进入被忽略的目录。
        current = Path(dirpath)
        dirnames[:] = [
            d
            for d in dirnames
            if not is_ignored(current / d, root, is_dir=True)
        ]
        for name in filenames:
            path = Path(dirpath) / name
            if not is_ignored(path, root, is_dir=False):
                yield path
