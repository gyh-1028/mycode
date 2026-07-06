"""Small git helpers for diff and safe auto-commit flows."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from mycode.checkpoint import Checkpoint
from mycode.llm.base import BaseProvider
from mycode.ui import print_diff


@dataclass(frozen=True)
class GitState:
    enabled: bool
    reason: str | None = None


def _git_available() -> bool:
    return shutil.which("git") is not None


def _run_git(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd or Path.cwd()),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def prepare_auto_commit() -> GitState:
    if not _git_available():
        return GitState(False, "未找到 git,跳过自动提交。")
    repo = _run_git(["rev-parse", "--is-inside-work-tree"])
    if repo.returncode != 0 or repo.stdout.strip() != "true":
        return GitState(False, "当前目录不是 git 仓库,跳过自动提交。")
    status = _run_git(["status", "--porcelain", "--untracked-files=all"])
    if status.returncode != 0:
        return GitState(False, "无法读取 git 状态,跳过自动提交。")
    if status.stdout.strip():
        return GitState(False, "启动时工作区已有未提交改动,跳过自动提交以避免混入其它文件。")
    return GitState(True)


def _ensure_git_repo() -> tuple[bool, str]:
    if not _git_available():
        return False, "错误:未找到 git。"
    repo = _run_git(["rev-parse", "--is-inside-work-tree"])
    if repo.returncode != 0 or repo.stdout.strip() != "true":
        return False, "错误:当前目录不是 git 仓库。"
    return True, ""


def git_status(*, short: bool = True) -> str:
    """返回当前 git 工作区状态文本。"""
    ok, err = _ensure_git_repo()
    if not ok:
        return err
    args = ["status", "--porcelain", "--untracked-files=all"]
    if not short:
        args = ["status"]
    proc = _run_git(args)
    if proc.returncode != 0:
        return f"错误:git status 失败:{proc.stderr.strip()}"
    out = proc.stdout.strip()
    return out or "(工作区干净)"


def git_log(n: int = 10) -> str:
    """返回最近 n 条提交的简要日志。"""
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 100:
        return "错误:git log 的 n 必须是 1 到 100 之间的整数。"
    ok, err = _ensure_git_repo()
    if not ok:
        return err
    proc = _run_git(["log", "--oneline", "--decorate", f"-{n}"])
    if proc.returncode != 0:
        return f"错误:git log 失败:{proc.stderr.strip()}"
    return proc.stdout.strip() or "(无提交记录)"


def git_branch() -> str:
    """返回当前分支及本地分支列表。"""
    ok, err = _ensure_git_repo()
    if not ok:
        return err
    current = _run_git(["branch", "--show-current"])
    if current.returncode != 0:
        return f"错误:git branch 失败:{current.stderr.strip()}"
    branches = _run_git(["branch", "--list", "--sort=-committerdate"])
    if branches.returncode != 0:
        return f"错误:git branch 失败:{branches.stderr.strip()}"
    out = [f"当前分支:{current.stdout.strip()}", "本地分支:"]
    out.extend(branches.stdout.splitlines())
    return "\n".join(out).strip()


def git_diff(*, staged: bool = False) -> str:
    """Return the current tracked-file diff without modifying the repository."""

    ok, err = _ensure_git_repo()
    if not ok:
        return err
    args = ["diff", "--no-ext-diff"]
    if staged:
        args.append("--cached")
    proc = _run_git(args)
    if proc.returncode not in (0, 1):
        return f"错误:git diff 失败:{proc.stderr.strip()}"
    return proc.stdout.strip() or "(没有 tracked file diff；未跟踪文件请结合 git_status/read_file 审查)"


def _diff_for_paths(paths: list[str], *, cached: bool = False) -> str:
    args = ["diff"]
    if cached:
        args.append("--cached")
    args.append("--")
    args.extend(paths)
    proc = _run_git(args)
    return proc.stdout if proc.returncode in (0, 1) else ""


def render_diff_for_checkpoint(
    checkpoint: Checkpoint | None,
    *,
    console: Console | None = None,
) -> str:
    paths = checkpoint.changed_paths() if checkpoint else []
    proc = _run_git(["diff", "--", *paths] if paths else ["diff"])
    if proc.returncode not in (0, 1):
        return f"错误:git diff 失败:{proc.stderr.strip()}"
    if not proc.stdout.strip():
        return "(没有可显示的 git diff)"
    print_diff(proc.stdout, console=console)
    return proc.stdout


def _commit_message(provider: BaseProvider, diff_text: str) -> str:
    fallback = "mycode: update files"
    prompt = [
        {
            "role": "system",
            "content": "你是提交信息生成器。只输出一行简洁中文 git commit message,不要解释。",
        },
        {"role": "user", "content": diff_text[:12000]},
    ]
    try:
        response = provider.chat(prompt, tools=None)
    except Exception:  # noqa: BLE001 - auto commit should degrade gracefully.
        return fallback
    message = (response.text or "").strip().splitlines()[0:1]
    if not message:
        return fallback
    return message[0].strip()[:120] or fallback


def auto_commit_checkpoint(
    provider: BaseProvider,
    checkpoint: Checkpoint | None,
    *,
    state: GitState,
) -> str:
    if not state.enabled:
        return state.reason or "自动提交未启用。"
    if checkpoint is None or not checkpoint.files:
        return "本轮没有 mycode 写入的文件,无需提交。"

    paths = checkpoint.changed_paths()
    add = _run_git(["add", "--", *paths])
    if add.returncode != 0:
        return f"git add 失败:{add.stderr.strip()}"
    diff = _diff_for_paths(paths, cached=True)
    if not diff.strip():
        return "暂存区没有变化,无需提交。"
    message = _commit_message(provider, diff)
    commit = _run_git(["commit", "-m", message])
    if commit.returncode != 0:
        return f"git commit 失败:{commit.stderr.strip()}"
    return f"已创建 git commit:{message}"
