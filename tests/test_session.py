"""Tests for session persistence + resume (Task 12)."""

import json

import pytest

from mycode.session import (
    CURRENT_SCHEMA_VERSION,
    Session,
    SessionConflictError,
    SessionError,
    UnsupportedSchemaError,
)


def _convo() -> list[dict]:
    """A conversation with a tool_call + its paired tool result + final text."""
    return [
        {"role": "system", "content": "你是 mycode"},
        {"role": "user", "content": "读一下 app.py"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "app.py"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "print('hi')"},
        {"role": "assistant", "content": "这是个 hello 脚本"},
    ]


def test_save_and_load_roundtrip_preserves_messages_verbatim(tmp_path) -> None:
    messages = _convo()
    s = Session.new(model="deepseek-chat", provider="https://api.deepseek.com", base_dir=tmp_path)
    s.save(messages)

    loaded = Session.load(s.id, base_dir=tmp_path)
    assert loaded is not None
    assert loaded.model == "deepseek-chat"
    assert loaded.provider == "https://api.deepseek.com"
    # messages stored verbatim — tool_call and its tool result paired, in order
    assert loaded.messages == messages
    assistant_tc = loaded.messages[2]
    assert assistant_tc["tool_calls"][0]["id"] == "call_1"
    assert loaded.messages[3]["tool_call_id"] == "call_1"


def test_save_is_atomic_no_tmp_left(tmp_path) -> None:
    s = Session.new(model="m", provider="p", base_dir=tmp_path)
    s.save(_convo())
    assert s.path.is_file()
    assert not list(tmp_path.glob("*.tmp"))


def test_stale_session_save_is_rejected(tmp_path) -> None:
    session = Session.new(model="m", provider="p", base_dir=tmp_path)
    session.save([{"role": "user", "content": "initial"}])
    first = Session.load(session.id, base_dir=tmp_path)
    stale = Session.load(session.id, base_dir=tmp_path)
    assert first is not None and stale is not None

    first.save([{"role": "user", "content": "first writer"}])
    with pytest.raises(SessionConflictError):
        stale.save([{"role": "user", "content": "stale writer"}])

    loaded = Session.load(session.id, base_dir=tmp_path)
    assert loaded is not None
    assert loaded.messages[-1]["content"] == "first writer"


def test_load_missing_returns_none(tmp_path) -> None:
    assert Session.load("does-not-exist", base_dir=tmp_path) is None


def test_list_all_empty(tmp_path) -> None:
    assert Session.list_all(base_dir=tmp_path / "nope") == []


def _write_raw(directory, sid, updated_at, messages, *, schema_version=None):
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": sid,
        "model": "m",
        "provider": "p",
        "created_at": updated_at,
        "updated_at": updated_at,
        "messages": messages,
    }
    if schema_version is not None:
        payload["schema_version"] = schema_version
    (directory / f"{sid}.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def test_list_all_sorted_and_latest(tmp_path) -> None:
    _write_raw(tmp_path, "old", "2026-06-01T10:00:00", [{"role": "user", "content": "a"}])
    _write_raw(tmp_path, "new", "2026-06-08T09:00:00", [{"role": "user", "content": "b"}])
    sessions = Session.list_all(base_dir=tmp_path)
    assert [s.id for s in sessions] == ["new", "old"]  # newest first
    assert Session.latest(base_dir=tmp_path).id == "new"


def test_summaries(tmp_path) -> None:
    s = Session.new(model="m", provider="p", base_dir=tmp_path)
    s.save(_convo())
    loaded = Session.load(s.id, base_dir=tmp_path)
    assert loaded.first_user_text() == "读一下 app.py"
    assert loaded.turn_count() == 1  # one user message


def test_saved_payload_includes_schema_version(tmp_path) -> None:
    s = Session.new(model="m", provider="p", base_dir=tmp_path)
    s.save(_convo())
    payload = json.loads(s.path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == CURRENT_SCHEMA_VERSION


def test_legacy_v0_migration_to_v1(tmp_path) -> None:
    """Files without schema_version are treated as v0 and migrated in memory."""
    _write_raw(tmp_path, "legacy", "2026-06-01T10:00:00", [{"role": "user", "content": "hi"}])
    loaded = Session.load("legacy", base_dir=tmp_path)
    assert loaded.schema_version == CURRENT_SCHEMA_VERSION
    # Next save persists the new schema version
    loaded.save(loaded.messages)
    payload = json.loads((tmp_path / "legacy.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == CURRENT_SCHEMA_VERSION


def test_unsupported_schema_version_rejected(tmp_path) -> None:
    _write_raw(
        tmp_path,
        "future",
        "2026-06-01T10:00:00",
        [{"role": "user", "content": "hi"}],
        schema_version=99,
    )
    with pytest.raises(UnsupportedSchemaError):
        Session.load("future", base_dir=tmp_path)


def test_corrupt_file_ignored_in_list_but_raises_on_explicit_load(tmp_path) -> None:
    (tmp_path / "bad.json").write_text("{ not json", encoding="utf-8")
    assert Session.list_all(base_dir=tmp_path) == []
    assert Session.corrupt_count(base_dir=tmp_path) == 1
    with pytest.raises(SessionError):
        Session.load("bad", base_dir=tmp_path)


def test_validation_rejects_orphan_tool_result(tmp_path) -> None:
    _write_raw(
        tmp_path,
        "orphan",
        "2026-06-01T10:00:00",
        [
            {"role": "user", "content": "x"},
            {"role": "tool", "tool_call_id": "missing", "content": "oops"},
        ],
    )
    with pytest.raises(SessionError):
        Session.load("orphan", base_dir=tmp_path)


def test_validation_rejects_unmatched_tool_call(tmp_path) -> None:
    _write_raw(
        tmp_path,
        "unmatched",
        "2026-06-01T10:00:00",
        [
            {"role": "user", "content": "x"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "tc1",
                        "type": "function",
                        "function": {"name": "n", "arguments": "{}"},
                    }
                ],
            },
        ],
    )
    with pytest.raises(SessionError):
        Session.load("unmatched", base_dir=tmp_path)


def test_validation_accepts_paired_tool_call(tmp_path) -> None:
    _write_raw(
        tmp_path,
        "paired",
        "2026-06-01T10:00:00",
        [
            {"role": "user", "content": "x"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "tc1",
                        "type": "function",
                        "function": {"name": "n", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "tc1", "content": "ok"},
        ],
    )
    loaded = Session.load("paired", base_dir=tmp_path)
    assert loaded is not None
    assert loaded.turn_count() == 1
