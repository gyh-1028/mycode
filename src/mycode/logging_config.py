"""Optional file logging and audit support.

Controlled by the ``MYCODE_LOG_LEVEL`` environment variable (default ``WARNING``).
Logs are written to ``.mycode/logs/mycode.log`` in the current working directory
and rotated daily (keeps the last 7 days).

No API keys or file contents are ever logged.
"""

from __future__ import annotations

import logging
import os
import re
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

DEFAULT_LOG_LEVEL = "WARNING"
_REDACTED = "[REDACTED]"
_BEARER_RE = re.compile(r"(?i)(\bauthorization\s*[:=]?\s*bearer\s+)[^\s,;]+")
_ASSIGNMENT_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|secret)\s*[:=]\s*)[^\s,;]+"
)
_OPENAI_STYLE_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{4,}\b")


def redact_log_text(text: str) -> str:
    """Remove common credential shapes and configured secret env values."""
    redacted = _BEARER_RE.sub(rf"\1{_REDACTED}", text)
    redacted = _ASSIGNMENT_RE.sub(rf"\1{_REDACTED}", redacted)
    redacted = _OPENAI_STYLE_KEY_RE.sub(_REDACTED, redacted)
    for name, value in os.environ.items():
        upper = name.upper()
        if len(value) >= 4 and any(marker in upper for marker in ("KEY", "TOKEN", "SECRET")):
            redacted = redacted.replace(value, _REDACTED)
    return redacted


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_log_text(super().format(record))


def setup_logging(
    log_level: str | None = None,
    log_dir: Path | None = None,
) -> logging.Logger:
    """Configure the ``mycode`` logger with a rotating file handler.

    ``MYCODE_LOG_LEVEL`` overrides the default ``WARNING``.
    """
    level_name = (log_level or os.environ.get("MYCODE_LOG_LEVEL", DEFAULT_LOG_LEVEL)).upper()
    level = getattr(logging, level_name, logging.WARNING)

    logger = logging.getLogger("mycode")
    logger.setLevel(level)
    logger.propagate = False

    if log_dir is None:
        log_dir = Path.cwd() / ".mycode" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = (log_dir / "mycode.log").resolve()

    # Avoid duplicate handlers for the same destination while still allowing
    # isolated tests or embedded callers to choose a separate log directory.
    for existing in logger.handlers:
        if (
            isinstance(existing, TimedRotatingFileHandler)
            and Path(existing.baseFilename).resolve() == log_path
        ):
            existing.setLevel(level)
            return logger

    handler = TimedRotatingFileHandler(
        log_path,
        when="midnight",
        backupCount=7,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(
        RedactingFormatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
    )
    logger.addHandler(handler)
    return logger
