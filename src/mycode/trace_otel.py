"""OpenTelemetry bridge for agent run metadata.

This module is only active when ``config.trace.otlp_enabled`` is true. It never
records prompts, tool arguments, or tool outputs — only span metadata:
run/model/tool lifecycle, durations, statuses, and token counts.

The implementation tolerates missing ``opentelemetry`` packages: if they are
not installed, ``init_otel_provider`` is a no-op and ``OtelEventSink`` ignores
all events.
"""

from __future__ import annotations

import logging
from typing import Any

from mycode.agent.events import AgentEvent, EventType
from mycode.trace import TraceConfig

_LOGGER = logging.getLogger("mycode.trace_otel")

_otel_state: dict[str, Any] = {"provider": None, "tracer": None, "exporter": None}


def _has_otel() -> bool:
    try:
        import opentelemetry.trace  # noqa: F401
        from opentelemetry.sdk.trace import TracerProvider  # noqa: F401

        return True
    except ImportError:
        return False


def init_otel_provider(
    config: TraceConfig,
    *,
    exporter: Any | None = None,
) -> Any | None:
    """Configure a TracerProvider for this process.

    If *exporter* is provided it is attached directly (used by tests). Otherwise
    an OTLP HTTP exporter is constructed from ``config.otlp_endpoint`` and
    ``config.otlp_headers`` when ``config.otlp_enabled`` is true.
    """
    if not config.otlp_enabled and exporter is None:
        return None
    if not _has_otel():
        _LOGGER.warning("OpenTelemetry requested but packages are not installed.")
        return None

    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

    provider = TracerProvider(resource=Resource({SERVICE_NAME: "mycode"}))

    if exporter is None:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            kwargs: dict[str, Any] = {}
            if config.otlp_endpoint:
                kwargs["endpoint"] = config.otlp_endpoint
            if config.otlp_headers:
                kwargs["headers"] = config.otlp_headers
            exporter = OTLPSpanExporter(**kwargs)
        except ImportError:
            _LOGGER.warning("OTLP exporter package not installed; OpenTelemetry disabled.")
            return None
        provider.add_span_processor(BatchSpanProcessor(exporter))
    else:
        provider.add_span_processor(SimpleSpanProcessor(exporter))

    from opentelemetry import trace

    trace.set_tracer_provider(provider)
    _otel_state["provider"] = provider
    _otel_state["tracer"] = trace.get_tracer("mycode")
    _otel_state["exporter"] = exporter
    return provider


def shutdown_otel_provider() -> None:
    provider = _otel_state.get("provider")
    if provider is not None:
        try:
            provider.shutdown()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("OpenTelemetry shutdown failed: %s", exc)
    _otel_state.clear()


def _get_tracer() -> Any | None:
    return _otel_state.get("tracer")


class OtelEventSink:
    """Converts AgentEvent stream into OpenTelemetry spans.

    Keeps a lightweight stack of open spans. Because events are ordered and the
    runner is single-threaded for a run, this is sufficient to pair starts and
    ends without span IDs on the wire.
    """

    def __init__(self) -> None:
        self._run_span: Any | None = None
        self._model_span: Any | None = None
        self._tool_span: Any | None = None
        self._active = _get_tracer() is not None

    def __call__(self, event: AgentEvent, attachments: dict[str, Any]) -> None:
        if not self._active:
            return
        tracer = _get_tracer()
        if tracer is None:
            return
        t = event.type
        p = event.payload

        if t == EventType.RUN_STARTED:
            run_span = tracer.start_span("agent.run")
            run_span.set_attribute("run_id", event.run_id)
            run_span.set_attribute("max_steps", p.get("max_steps", 0))
            run_span.set_attribute("model", p.get("model", ""))
            self._run_span = run_span
            return

        if t == EventType.MODEL_CALL_STARTED:
            run_span = self._run_span
            if run_span is not None:
                from opentelemetry import trace

                model_span = tracer.start_span(
                    "agent.model_call",
                    context=trace.set_span_in_context(run_span),
                )
                model_span.set_attribute("run_id", event.run_id)
                model_span.set_attribute("step", p.get("step", 0))
                self._model_span = model_span
            return

        if t == EventType.MODEL_USAGE:
            model_span = self._model_span
            if model_span is not None:
                model_span.set_attribute("prompt_tokens", p.get("prompt_tokens", 0))
                model_span.set_attribute("completion_tokens", p.get("completion_tokens", 0))
                model_span.set_attribute("cached_tokens", p.get("cached_tokens", 0))
                model_span.set_attribute("cache_write_tokens", p.get("cache_write_tokens", 0))
            return

        if t in {EventType.MODEL_STREAM_END, EventType.MODEL_CALL_ERROR}:
            model_span = self._model_span
            if model_span is not None:
                if t == EventType.MODEL_CALL_ERROR:
                    from opentelemetry.trace import Status, StatusCode

                    model_span.set_status(
                        Status(StatusCode.ERROR, str(p.get("error_type", "model_error")))
                    )
                model_span.end()
                self._model_span = None
            return

        if t == EventType.TOOL_CALL_STARTED:
            run_span = self._run_span
            if run_span is not None:
                from opentelemetry import trace

                tool_span = tracer.start_span(
                    "agent.tool_call",
                    context=trace.set_span_in_context(run_span),
                )
                tool_span.set_attribute("run_id", event.run_id)
                tool_span.set_attribute("tool", p.get("name", "unknown"))
                self._tool_span = tool_span
            return

        if t == EventType.TOOL_CALL_FINISHED:
            tool_span = self._tool_span
            if tool_span is not None:
                tool_span.set_attribute("result_len", p.get("result_len", 0))
                tool_span.set_attribute("is_error", bool(p.get("is_error", False)))
                tool_span.end()
                self._tool_span = None
            return

        if t in {
            EventType.RUN_FINISHED,
            EventType.RUN_FAILED,
            EventType.RUN_CANCELLED,
            EventType.RUN_MAX_STEPS,
            EventType.RUN_BUDGET_EXCEEDED,
            EventType.RUN_STUCK,
        }:
            run_span = self._run_span
            if run_span is not None:
                status = p.get("status") or p.get("reason") or t.rsplit(".", 1)[-1]
                run_span.set_attribute("final_status", status)
                if t != EventType.RUN_FINISHED:
                    from opentelemetry.trace import Status, StatusCode

                    run_span.set_status(Status(StatusCode.ERROR, str(status)))
                run_span.end()
                self._run_span = None
            return

    def close(self) -> None:
        for span in (self._model_span, self._tool_span, self._run_span):
            if span is not None:
                try:
                    span.end()
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.debug("failed to close span: %s", exc)
        self._model_span = None
        self._tool_span = None
        self._run_span = None


__all__ = [
    "OtelEventSink",
    "init_otel_provider",
    "shutdown_otel_provider",
]
