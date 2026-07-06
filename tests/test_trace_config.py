"""Tests for trace configuration loading and env overrides."""

from __future__ import annotations

from pathlib import Path

import pytest

from mycode.config import Config, ConfigError, config_with_trace_overrides, load_config, load_config_result


def test_trace_defaults() -> None:
    cfg = load_config()
    assert cfg.trace.enabled is False
    assert cfg.trace.record_prompts is False
    assert cfg.trace.record_tool_io is False
    assert cfg.trace.record_outputs is False
    assert cfg.trace.otlp_enabled is False


def test_trace_from_toml(tmp_path) -> None:
    p = tmp_path / "config.toml"
    p.write_text(
        """
        [trace]
        enabled = true
        directory = "/tmp/traces"
        record_prompts = true
        record_tool_io = true
        otlp_enabled = true
        otlp_endpoint = "http://localhost:4318"
        otlp_headers = { Authorization = "Bearer x" }
        """,
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.trace.enabled is True
    assert cfg.trace.directory == Path("/tmp/traces")
    assert cfg.trace.record_prompts is True
    assert cfg.trace.record_tool_io is True
    assert cfg.trace.otlp_enabled is True
    assert cfg.trace.otlp_endpoint == "http://localhost:4318"
    assert cfg.trace.otlp_headers == {"Authorization": "Bearer x"}


def test_trace_env_overrides(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MYCODE_TRACE", "1")
    monkeypatch.setenv("MYCODE_TRACE_DIRECTORY", str(tmp_path / "env-trace"))
    monkeypatch.setenv("MYCODE_TRACE_RECORD_PROMPTS", "true")
    monkeypatch.setenv("MYCODE_TRACE_RECORD_TOOL_IO", "yes")
    monkeypatch.setenv("MYCODE_TRACE_OTLP_ENABLED", "on")
    monkeypatch.setenv("MYCODE_TRACE_OTLP_ENDPOINT", "http://otel:4318")
    monkeypatch.setenv("MYCODE_TRACE_OTLP_HEADERS", "x-api-key=secret,env=test")
    result = load_config_result()
    trace = result.config.trace
    assert trace.enabled is True
    assert trace.directory == tmp_path / "env-trace"
    assert trace.record_prompts is True
    assert trace.record_tool_io is True
    assert trace.otlp_enabled is True
    assert trace.otlp_endpoint == "http://otel:4318"
    assert trace.otlp_headers == {"x-api-key": "secret", "env": "test"}
    assert "MYCODE_TRACE" in result.env_overrides
    assert "MYCODE_TRACE_OTLP_HEADERS" in result.env_overrides


def test_config_with_trace_overrides() -> None:
    cfg = Config()
    updated = config_with_trace_overrides(
        cfg,
        enabled=True,
        record_tool_io=True,
    )
    assert updated.trace.enabled is True
    assert updated.trace.record_tool_io is True
    # original unchanged
    assert cfg.trace.enabled is False
    assert cfg.trace.record_tool_io is False


def test_trace_config_invalid_value_raises(tmp_path) -> None:
    p = tmp_path / "config.toml"
    p.write_text("[trace]\nenabled = 123\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)
