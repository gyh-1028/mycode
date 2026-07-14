"""Cross-process locking and durable atomic text writes."""

from __future__ import annotations

import hashlib
import importlib
import os
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class FileLockTimeout(TimeoutError):
    pass


def content_fingerprint(content: str, encoding: str = "utf-8") -> str:
    return hashlib.sha256(content.encode(encoding)).hexdigest()


def bytes_fingerprint(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def file_fingerprint(path: Path) -> str | None:
    try:
        return bytes_fingerprint(path.read_bytes())
    except FileNotFoundError:
        return None


@contextmanager
def path_lock(path: Path, *, timeout: float = 10.0) -> Iterator[None]:
    """Hold a process-wide advisory lock associated with ``path``."""

    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    handle = lock_path.open("a+b")
    if handle.seek(0, os.SEEK_END) == 0:
        handle.write(b"\0")
        handle.flush()
    deadline = time.monotonic() + timeout
    platform_module: Any
    if os.name == "nt":
        platform_module = importlib.import_module("msvcrt")

        def lock() -> None:
            platform_module.locking(handle.fileno(), platform_module.LK_NBLCK, 1)

        def unlock() -> None:
            platform_module.locking(handle.fileno(), platform_module.LK_UNLCK, 1)

    else:
        platform_module = importlib.import_module("fcntl")

        def lock() -> None:
            platform_module.flock(handle.fileno(), platform_module.LOCK_EX | platform_module.LOCK_NB)

        def unlock() -> None:
            platform_module.flock(handle.fileno(), platform_module.LOCK_UN)

    acquired = False
    try:
        while not acquired:
            try:
                handle.seek(0)
                lock()
                acquired = True
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise FileLockTimeout(f"timed out waiting for lock: {path}") from exc
                time.sleep(0.05)
        yield
    finally:
        if acquired:
            handle.seek(0)
            unlock()
        handle.close()


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> str:
    """Write through a unique temporary file and atomically replace ``path``."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding=encoding, newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)
    return content_fingerprint(content, encoding)


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


__all__ = [
    "FileLockTimeout",
    "atomic_write_text",
    "bytes_fingerprint",
    "content_fingerprint",
    "file_fingerprint",
    "path_lock",
]
