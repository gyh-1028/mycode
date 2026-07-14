"""Tests for persistent write checkpoints and undo."""

import os

import pytest

from mycode.checkpoint import (
    Checkpoint,
    FileChange,
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


def test_checkpoint_undo_rejects_tampered_outside_path(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    checkpoint = Checkpoint.begin(session_id="s3", task="tampered")
    checkpoint.files.append(FileChange(path="../outside.txt", kind="modified", before="changed\n"))

    result = checkpoint.undo()

    assert "../outside.txt" in result
    assert outside.read_text(encoding="utf-8") == "outside\n"
    assert checkpoint.undone_at is None


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks is not reliably available on Windows CI")
def test_checkpoint_undo_rejects_symlink_outside_project(tmp_path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "app.py"
    target.write_text("outside\n", encoding="utf-8")
    monkeypatch.chdir(project)
    checkpoint = Checkpoint.begin(session_id="s4", task="symlink")
    checkpoint.files.append(FileChange(path="linked/app.py", kind="modified", before="changed\n"))
    (project / "linked").symlink_to(outside, target_is_directory=True)

    result = checkpoint.undo()

    assert "linked/app.py" in result
    assert target.read_text(encoding="utf-8") == "outside\n"
    assert checkpoint.undone_at is None
