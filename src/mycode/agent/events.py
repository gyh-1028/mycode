"""Agent event protocol: a stable, UI-agnostic stream of what happened in a run.

The :class:`AgentRunner` emits :class:`AgentEvent` objects to one or more
sinks. A sink is any callable ``(event, attachments) -> None``. The console
adapter renders events into the same Rich output users saw before; the trace
writer persists them (with optional redacted attachments) as JSONL so a run can
be replayed offline.

Design notes
------------
* ``schema_version`` lets consumers reject incompatible traces early.
* ``seq`` is a per-run monotonic counter assigned by the runner, so event order
  is stable regardless of how many sinks are attached.
* ``payload`` carries everything a *display* sink needs. Sensitive/large blobs
  (full prompts, tool inputs/outputs) travel in the separate ``attachments``
  dict and are only persisted when the trace config explicitly enables them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# Bumped only on breaking changes to the event shape/semantics.
SCHEMA_VERSION = 1


# --------------------------------------------------------------------------- #
# Event type vocabulary. Keep names dotted and stable; consumers switch on them.
# --------------------------------------------------------------------------- #
class EventType:
    # Run lifecycle
    RUN_STARTED = "run.started"
    RUN_FINISHED = "run.finished"  # completed with final text
    RUN_FAILED = "run.failed"  # terminal error (model/deadline/...)
    RUN_CANCELLED = "run.cancelled"
    RUN_MAX_STEPS = "run.max_steps"
    RUN_STUCK = "run.stuck"
    RUN_BUDGET_EXCEEDED = "run.budget_exceeded"

    # Planning
    PLAN_CREATED = "plan.created"
    PLAN_FAILED = "plan.failed"
    PLAN_PROGRESS = "plan.progress"
    PLAN_REMAINING_SKIPPED = "plan.remaining_skipped"

    # Context compaction
    CONTEXT_COMPACTED = "context.compacted"
    CONTEXT_CAPACITY = "context.capacity"
    CONTEXT_SELECTED = "context.selected"
    CONTEXT_DEGRADED = "context.degraded"

    # Model call lifecycle
    MODEL_CALL_STARTED = "model.call.started"
    MODEL_CALL_RETRY = "model.call.retry"
    MODEL_CALL_ERROR = "model.call.error"
    MODEL_STREAM_REASONING = "model.stream.reasoning"
    MODEL_STREAM_TEXT = "model.stream.text"
    MODEL_STREAM_END = "model.stream.end"
    MODEL_USAGE = "model.usage"  # per-step usage delta

    # Tool execution
    TOOL_CALL_STARTED = "tool.call.started"
    TOOL_CALL_FINISHED = "tool.call.finished"

    # Checkpoint (consistent state reached; safe to persist session)
    CHECKPOINT = "checkpoint"

    # Final usage report (rendered once at the end of a run)
    USAGE_REPORTED = "usage.reported"


# Terminal statuses reported in AgentRunResult / run.finished payload.
class RunStatus:
    COMPLETED = "completed"
    MAX_STEPS = "max_steps"
    STUCK = "stuck"
    BUDGET_EXCEEDED = "budget_exceeded"
    MODEL_ERROR = "model_error"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class AgentEvent:
    """A single, ordered observation from an agent run.

    ``attachments`` are NOT part of the wire payload: they carry optional rich
    data (full prompts / tool I/O) that only the trace writer consumes, and only
    when its config opts in. Display sinks should read ``payload`` alone.
    """

    schema_version: int
    run_id: str
    seq: int
    type: str
    timestamp: float
    payload: dict[str, Any] = field(default_factory=dict)


# A sink receives the event plus an (often empty) dict of trace attachments.
EventSink = Callable[[AgentEvent, dict[str, Any]], None]


def make_event(
    run_id: str,
    seq: int,
    type_: str,
    timestamp: float,
    *,
    payload: dict[str, Any] | None = None,
) -> AgentEvent:
    return AgentEvent(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        seq=seq,
        type=type_,
        timestamp=timestamp,
        payload=dict(payload or {}),
    )
