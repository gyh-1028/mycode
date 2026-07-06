"""Tests for git helpers."""

import shutil
import subprocess

import pytest

from mycode.checkpoint import Checkpoint, FileChange
from mycode.git_ops import (
    auto_commit_checkpoint,
    git_branch,
    git_log,
    git_status,
    prepare_auto_commit,
)
from mycode.llm.base import LLMResponse
from tests.fakes import FakeProvider

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(args: list[str], cwd) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _init_repo(path) -> None:
    _git(["init"], path)
    _git(["config", "user.email", "test@example.com"], path)
    _git(["config", "user.name", "Tester"], path)
    (path / "touched.txt").write_text("old\n", encoding="utf-8")
    (path / "other.txt").write_text("old\n", encoding="utf-8")
    _git(["add", "."], path)
    _git(["commit", "-m", "initial"], path)


def test_prepare_auto_commit_detects_clean_repo(tmp_path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)

    state = prepare_auto_commit()

    assert state.enabled is True


def test_auto_commit_stages_only_checkpoint_files(tmp_path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    state = prepare_auto_commit()
    assert state.enabled
    (tmp_path / "touched.txt").write_text("new\n", encoding="utf-8")
    (tmp_path / "other.txt").write_text("dirty\n", encoding="utf-8")
    checkpoint = Checkpoint(
        id="cp1",
        session_id="s1",
        task="edit",
        root=tmp_path,
        files=[FileChange(path="touched.txt", kind="modified", before="old\n")],
    )
    provider = FakeProvider([LLMResponse(text="更新 touched 文件")])

    out = auto_commit_checkpoint(provider, checkpoint, state=state)

    assert "已创建 git commit" in out
    changed_after_commit = _git(["diff", "--name-only", "HEAD"], tmp_path).stdout.splitlines()
    assert changed_after_commit == ["other.txt"]
    committed_files = _git(["show", "--name-only", "--format=", "HEAD"], tmp_path).stdout.splitlines()
    assert committed_files == ["touched.txt"]


def test_git_status_reports_porcelain(tmp_path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "new.txt").write_text("x", encoding="utf-8")
    status = git_status()
    assert "new.txt" in status


def test_git_log_returns_commits(tmp_path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    log = git_log(n=5)
    assert "initial" in log


def test_git_log_rejects_out_of_range_count() -> None:
    assert git_log(n=0).startswith("错误:")
    assert git_log(n=101).startswith("错误:")


def test_git_branch_returns_current_branch(tmp_path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    out = git_branch()
    assert "当前分支:" in out
    assert "master" in out or "main" in out


def test_git_tools_registered_and_dispatch(tmp_path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    from mycode.tools import dispatch, get_schemas

    names = {s["name"] for s in get_schemas()}
    assert {"git_status", "git_log", "git_branch"} <= names
    assert "initial" in dispatch("git_log", {"n": 5})
