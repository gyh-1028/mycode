"""P5 TraceWriter tests: ordering, metadata-only default, opt-in recording,
redaction, and replay. All offline."""

from __future__ import annotations

import json
from pathlib import Path

from mycode.agent.events import SCHEMA_VERSION, make_event
from mycode.agent.runner import AgentRunner, RunRequest
from mycode.llm.base import LLMResponse, StopReason, ToolCall
from mycode.trace import TraceConfig, TraceWriter, replay
from tests.fakes import FakeProvider


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# --------------------------------------------------------------------------- #
# Off by default -> no file, no records
# --------------------------------------------------------------------------- #
def test_trace_disabled_by_default(tmp_path) -> None:
    cfg = TraceConfig(enabled=False, directory=tmp_path)
    writer = TraceWriter(cfg, run_id="r-off")
    e = make_event("r-off", 1, "run.started", 1.0, payload={"run_id": "r-off"})
    writer.record(e)
    writer.close()
    assert writer.path is None
    assert writer.records == []
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------- #
# Enabled with metadata-only default -> file written, no prompts/io
# --------------------------------------------------------------------------- #
def test_trace_enabled_writes_jsonl_metadata_only(tmp_path) -> None:
    cfg = TraceConfig(enabled=True, directory=tmp_path)
    writer = TraceWriter(cfg, run_id="r1")
    e1 = make_event("r1", 1, "tool.call.started", 1.0, payload={"name": "read_file", "step": 1})
    e2 = make_event(
        "r1", 2, "tool.call.finished", 1.5,
        payload={"name": "read_file", "result_len": 10, "is_error": False},
    )
    writer.record(e1, attachments={"tool_args": {"path": "secret.py"}, "tool_result": "sk-leaked-key"})
    writer.record(e2)
    writer.close()

    assert writer.path == tmp_path / "r1.jsonl"
    lines = _read_jsonl(writer.path)
    assert len(lines) == 2
    assert lines[0]["seq"] == 1
    assert lines[0]["type"] == "tool.call.started"
    assert lines[0]["schema_version"] == SCHEMA_VERSION
    assert lines[0]["run_id"] == "r1"
    # metadata present
    assert lines[0]["data"]["name"] == "read_file"
    assert lines[0]["data"]["step"] == 1
    # tool_args / tool_result NOT recorded by default
    assert "tool_args" not in lines[0]["data"]
    assert "tool_result" not in lines[1]["data"]
    # the redactor never had to run because we dropped the attachment
    assert "sk-leaked-key" not in json.dumps(lines)


# --------------------------------------------------------------------------- #
# Opt-in tool_io -> recorded and redacted
# --------------------------------------------------------------------------- #
def test_trace_tool_io_opt_in_redacts(tmp_path, monkeypatch) -> None:
    secret = "sk-test-secret-1234"
    monkeypatch.setenv("MYCODE_KEY", secret)
    cfg = TraceConfig(enabled=True, record_tool_io=True, directory=tmp_path)
    writer = TraceWriter(cfg, run_id="r2")
    e = make_event("r2", 1, "tool.call.finished", 1.0, payload={"name": "run_bash", "result_len": 50})
    writer.record(e, attachments={"tool_args": {"command": "echo sk-test-secret-1234"}, "tool_result": "out sk-test-secret-1234"})
    writer.close()
    lines = _read_jsonl(writer.path)
    blob = json.dumps(lines, ensure_ascii=False)
    assert "sk-test-secret-1234" not in blob
    assert "[REDACTED]" in blob
    # the attachment keys are now present
    assert "tool_args" in lines[0]["data"]
    assert "tool_result" in lines[0]["data"]


# --------------------------------------------------------------------------- #
# Opt-in prompts -> recorded and recursively redacted
# --------------------------------------------------------------------------- #
def test_trace_prompts_opt_in_redacts(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MYCODE_KEY", "sk-leaked-1234")
    cfg = TraceConfig(enabled=True, record_prompts=True, directory=tmp_path)
    writer = TraceWriter(cfg, run_id="r3")
    e = make_event("r3", 1, "model.call.started", 1.0, payload={"step": 1})
    writer.record(e, attachments={"prompt": [{"role": "user", "content": "key=sk-leaked-1234"}]})
    writer.close()
    lines = _read_jsonl(writer.path)
    blob = json.dumps(lines, ensure_ascii=False)
    assert "sk-leaked-1234" not in blob
    assert "[REDACTED]" in blob
    assert "prompt" in lines[0]["data"]
    assert lines[0]["data"]["prompt"][0]["role"] == "user"


# --------------------------------------------------------------------------- #
# End-to-end: a real run produces an ordered, replayable trace
# --------------------------------------------------------------------------- #
def test_end_to_end_run_produces_replayable_trace(tmp_path) -> None:
    f = tmp_path / "notes.txt"
    f.write_text("hello\n", encoding="utf-8")
    provider = FakeProvider(
        [
            LLMResponse(
                tool_calls=[ToolCall(id="c1", name="read_file", args={"path": str(f)})],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            LLMResponse(text="done", stop_reason=StopReason.END_TURN),
        ]
    )
    cfg = TraceConfig(enabled=True, directory=tmp_path / "trace")
    writer = TraceWriter(cfg, run_id="e2e")
    runner = AgentRunner(sinks=[], trace=writer, clock=(lambda: 0.0), sleep=(lambda s: None))
    res = runner.run(
        RunRequest(provider=provider, messages=[{"role": "user", "content": "q"}],
                   max_steps=5, tools=[], run_id="e2e")
    )
    writer.close()

    assert res.status == "completed"
    assert res.final_text == "done"

    # records are in seq order
    seqs = [r.seq for r in writer.records]
    assert seqs == sorted(seqs)
    # the execution path is replayable: read_file appears before run.finished
    types = [r.type for r in writer.records]
    assert types[0] == "run.started"
    assert types[-1] == "usage.reported"
    assert types.index("tool.call.finished") < types.index("run.finished")
    # replay produces readable lines
    summary = replay(writer.records)
    assert any("tool.call.finished" in line and "read_file" in line for line in summary)
    assert any("run.finished" in line for line in summary)


# --------------------------------------------------------------------------- #
# Context manager closes the file handle
# --------------------------------------------------------------------------- #
def test_trace_context_manager_closes(tmp_path) -> None:
    cfg = TraceConfig(enabled=True, directory=tmp_path)
    with TraceWriter(cfg, run_id="ctx") as writer:
        e = make_event("ctx", 1, "run.started", 1.0, payload={})
        writer.record(e)
    # file is closed and persisted
    assert writer.path is not None
    lines = _read_jsonl(writer.path)
    assert len(lines) == 1


# --------------------------------------------------------------------------- #
# Metadata-only: sensitive-looking non-meta payload fields are dropped
# --------------------------------------------------------------------------- #
def test_non_meta_payload_fields_dropped_by_default(tmp_path) -> None:
    cfg = TraceConfig(enabled=True, directory=tmp_path)
    writer = TraceWriter(cfg, run_id="drop")
    # 'args_preview' is a meta key (safe); 'secret_blob' is not -> dropped
    e = make_event("drop", 1, "tool.call.started", 1.0,
                   payload={"args_preview": "ok", "secret_blob": "sk-xxxxx"})
    writer.record(e)
    writer.close()
    lines = _read_jsonl(writer.path)
    assert lines[0]["data"] == {"args_preview": "ok"}
    assert "secret_blob" not in lines[0]["data"]
