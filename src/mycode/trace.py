"""Structured JSONL trace recording for agent runs.

A trace is a sequence of :class:`TraceRecord` lines, one JSON object per line,
written to ``<trace_dir>/<run_id>.jsonl``. It lets a run be replayed offline:
which model calls happened, which tools ran in what order, why a run stopped,
and how many tokens each step cost.

Privacy strategy (per the P5 roadmap):

* Tracing is **off by default**. Enable via :class:`TraceConfig` (the CLI wires
  ``MYCODE_TRACE=1`` / ``--trace`` in later milestones; the runner accepts a
  config directly now).
* By default only **metadata** is recorded: event type, seq, timestamp, and the
  non-sensitive payload fields (tool names, char counts, statuses, usage).
* Prompts sent to the provider and tool inputs/outputs are recorded only when
  the corresponding flag (``record_prompts`` / ``record_tool_io``) is set.
* Everything that *is* recorded passes through :func:`redact_log_text` so API
  keys / bearer tokens never land on disk.

The writer is deliberately minimal: no background threads, no buffering beyond
line-level flush, so a crash leaves a valid (possibly truncated) JSONL file.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mycode.agent.events import SCHEMA_VERSION, AgentEvent
from mycode.logging_config import redact_log_text

DEFAULT_TRACE_DIR = Path(".mycode") / "trace"

# Payload keys that are considered "metadata" and always safe to persist as-is.
# Anything else is dropped unless the corresponding record_* flag is on.
_META_KEYS = frozenset(
    {
        "step", "name", "args_preview", "result_len", "is_error",
        "error_signature", "prompt_tokens", "completion_tokens",
        "cached_tokens", "cache_write_tokens", "total_tokens",
        "reason", "attempt", "max_steps", "model", "run_id",
        "final_text_len", "recent_calls", "last_error_sig",
        "estimated_cost", "budget", "estimated", "status", "detail",
        "is_first", "needs_separator", "streamed_any", "reasoning_started",
        "plan_text", "line", "remaining", "content_len",
        "estimated_tokens", "paths", "items", "degraded",
    }
)


@dataclass
class TraceConfig:
    """Controls what gets written to a trace file."""

    enabled: bool = False
    directory: Path = field(default_factory=lambda: DEFAULT_TRACE_DIR)
    record_prompts: bool = False      # full message list sent to the provider
    record_tool_io: bool = False      # tool args + raw result strings
    record_outputs: bool = False      # final answer text
    otlp_enabled: bool = False        # export metadata spans via OpenTelemetry
    otlp_endpoint: str | None = None
    otlp_headers: dict[str, str] = field(default_factory=dict)
    redactor: Callable[[str], str] = field(default=redact_log_text)


@dataclass
class TraceRecord:
    """One JSONL line. ``data`` holds the (already redacted) persisted fields."""

    schema_version: int
    run_id: str
    seq: int
    type: str
    timestamp: float
    data: dict[str, Any]


class TraceWriter:
    """Append-only JSONL writer for a single run.

    Created per run. Use :meth:`open`, then feed it events via :meth:`record`,
    then :meth:`close` (or use it as a context manager). Safe to construct with
    a config whose ``enabled`` is False — :meth:`record` becomes a no-op, so the
    runner can always hold one without conditional code.
    """

    def __init__(self, config: TraceConfig, run_id: str) -> None:
        self._config = config
        self._run_id = run_id
        self._fh = None
        self._path: Path | None = None
        self._records: list[TraceRecord] = []
        if config.enabled:
            self._open()

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def records(self) -> list[TraceRecord]:
        """In-memory copy of what was written (for tests / replay)."""
        return list(self._records)

    def _open(self) -> None:
        trace_dir = self._config.directory
        trace_dir.mkdir(parents=True, exist_ok=True)
        self._path = trace_dir / f"{self._run_id}.jsonl"
        # Append so a re-run with the same id keeps prior lines; in practice
        # run_ids are unique, but append is the safer choice on crashes.
        self._fh = self._path.open("a", encoding="utf-8")

    def __enter__(self) -> TraceWriter:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ARG002
        self.close()

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.flush()
            finally:
                self._fh.close()
                self._fh = None

    # ------------------------------------------------------------------ #
    # Recording
    # ------------------------------------------------------------------ #
    def record(self, event: AgentEvent, attachments: dict[str, Any] | None = None) -> None:
        if not self._config.enabled or self._fh is None:
            return
        data = self._build_data(event, attachments or {})
        line = json.dumps(
            {
                "schema_version": event.schema_version,
                "run_id": event.run_id,
                "seq": event.seq,
                "type": event.type,
                "timestamp": event.timestamp,
                "data": data,
            },
            ensure_ascii=False,
        )
        self._fh.write(line + "\n")
        self._fh.flush()
        self._records.append(
            TraceRecord(
                schema_version=event.schema_version,
                run_id=event.run_id,
                seq=event.seq,
                type=event.type,
                timestamp=event.timestamp,
                data=data,
            )
        )

    def _build_data(self, event: AgentEvent, attachments: dict[str, Any]) -> dict[str, Any]:
        cfg = self._config
        data: dict[str, Any] = {}

        # 1) Always-persist metadata payload keys.
        for key, value in event.payload.items():
            if key in _META_KEYS:
                data[key] = value

        # 2) Optional rich attachments, redacted.
        if cfg.record_prompts and "prompt" in attachments:
            data["prompt"] = self._redact_obj(attachments["prompt"])
        if cfg.record_prompts and "selected_context" in attachments:
            data["selected_context"] = self._redact_text(str(attachments["selected_context"]))
        if cfg.record_tool_io:
            if "tool_args" in attachments:
                data["tool_args"] = self._redact_obj(attachments["tool_args"])
            if "tool_result" in attachments:
                data["tool_result"] = self._redact_text(str(attachments["tool_result"]))
        if cfg.record_outputs and "final_text" in attachments:
            data["final_text"] = self._redact_text(str(attachments["final_text"]))

        return data

    def _redact_text(self, text: str) -> str:
        return self._config.redactor(text)

    def _redact_obj(self, obj: Any) -> Any:
        """Recursively redact every string in a JSON-serialisable structure."""
        if isinstance(obj, str):
            return self._redact_text(obj)
        if isinstance(obj, list):
            return [self._redact_obj(item) for item in obj]
        if isinstance(obj, dict):
            return {key: self._redact_obj(value) for key, value in obj.items()}
        return obj


def replay(records: list[TraceRecord]) -> list[str]:
    """Return a human-readable, ordered summary of a trace (for debugging/tests).

    Each entry is one line like ``[seq] TYPE key=value ...``. This is what the
    "trace can replay the execution path" acceptance criterion exercises.
    """
    lines: list[str] = []
    for rec in records:
        parts = [f"[{rec.seq}] {rec.type}"]
        for key, value in rec.data.items():
            parts.append(f"{key}={value}")
        lines.append(" ".join(parts))
    return lines


__all__ = [
    "SCHEMA_VERSION",
    "DEFAULT_TRACE_DIR",
    "TraceConfig",
    "TraceRecord",
    "TraceWriter",
    "replay",
]
