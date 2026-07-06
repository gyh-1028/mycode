"""Tests for persistent write checkpoints and undo."""

from mycode.checkpoint import (
    Checkpoint,
    record_file_write,
    reset_current_checkpoint,
    set_current_checkpoint,
)


def test_checkpoint_records_modified_file_and_undo_restores(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "app.py"
    target.write_text("old\n", encoding="utf-8")
    checkpoint = Checkpoint.begin(session_id="s1", task="edit")
    token = set_current_checkpoint(checkpoint)
    try:
        record_file_write(target, "old\n", existed=True)
        target.write_text("new\n", encoding="utf-8")
    finally:
        reset_current_checkpoint(token)

    assert checkpoint.path.is_file()
    assert checkpoint.changed_paths() == ["app.py"]

    result = checkpoint.undo()

    assert "还原 1 个" in result
    assert target.read_text(encoding="utf-8") == "old\n"
    assert Checkpoint.latest("s1") is None


def test_checkpoint_records_created_file_and_undo_deletes(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "new.txt"
    checkpoint = Checkpoint.begin(session_id="s2", task="create")
    token = set_current_checkpoint(checkpoint)
    try:
        record_file_write(target, "", existed=False)
        target.write_text("created\n", encoding="utf-8")
    finally:
        reset_current_checkpoint(token)

    result = checkpoint.undo()

    assert "删除新建 1 个" in result
    assert not target.exists()
