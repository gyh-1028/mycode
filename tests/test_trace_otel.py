"""Tests for the OpenTelemetry metadata-only bridge."""

from __future__ import annotations

import pytest

from mycode.agent.events import make_event
from mycode.trace import TraceConfig
from mycode.trace_otel import (
    OtelEventSink,
    init_otel_provider,
    shutdown_otel_provider,
)

otel = pytest.importorskip("opentelemetry.sdk.trace.export", reason="OpenTelemetry SDK not installed")
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)


def _spans_for_run(exporter: InMemorySpanExporter, run_id: str):
    return [s for s in exporter.get_finished_spans() if dict(s.attributes or {}).get("run_id") == run_id]


def test_otel_sink_creates_run_model_tool_spans(tmp_path) -> None:
    exporter = InMemorySpanExporter()
    init_otel_provider(TraceConfig(otlp_enabled=True), exporter=exporter)
    sink = OtelEventSink()
    try:
        sink(make_event("r1", 1, "run.started", 1.0, payload={"max_steps": 5, "model": "m"}), {})
        sink(make_event("r1", 2, "model.call.started", 2.0, payload={"step": 1}), {})
        sink(make_event("r1", 3, "model.usage", 2.1, payload={"prompt_tokens": 10, "completion_tokens": 5}), {})
        sink(make_event("r1", 4, "model.stream.end", 2.2, payload={}), {})
        sink(make_event("r1", 5, "tool.call.started", 3.0, payload={"name": "read_file"}), {})
        sink(make_event("r1", 6, "tool.call.finished", 3.1, payload={"result_len": 7, "is_error": False}), {})
        sink(make_event("r1", 7, "run.finished", 4.0, payload={"status": "completed"}), {})
    finally:
        sink.close()
        shutdown_otel_provider()

    spans = _spans_for_run(exporter, "r1")
    names = {s.name for s in spans}
    assert "agent.run" in names
    assert "agent.model_call" in names
    assert "agent.tool_call" in names

    run_span = next(s for s in spans if s.name == "agent.run")
    assert run_span.attributes["max_steps"] == 5
    assert run_span.attributes["final_status"] == "completed"
    model_span = next(s for s in spans if s.name == "agent.model_call")
    assert model_span.attributes["prompt_tokens"] == 10
    assert model_span.attributes["completion_tokens"] == 5
    tool_span = next(s for s in spans if s.name == "agent.tool_call")
    assert tool_span.attributes["tool"] == "read_file"


def test_otel_sink_noop_when_packages_missing(tmp_path, monkeypatch) -> None:
    """When opentelemetry is not importable the sink should not crash."""
    monkeypatch.setattr("mycode.trace_otel._has_otel", lambda: False)
    exporter = InMemorySpanExporter()
    init_otel_provider(TraceConfig(otlp_enabled=True), exporter=exporter)
    sink = OtelEventSink()
    sink(make_event("r2", 1, "run.started", 1.0, payload={}), {})
    sink.close()
    shutdown_otel_provider()
    assert _spans_for_run(exporter, "r2") == []
