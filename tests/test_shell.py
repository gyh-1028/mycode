"""Tests for run_bash: blacklist, confirm, cwd, timeout, truncation (Task 8)."""

import sys
from pathlib import Path

from mycode.config import Config, PermissionsConfig
from mycode.tools.shell import run_bash

PY = sys.executable  # absolute path to the current Python, robust across shells


def _set_cmd_mode(monkeypatch, mode: str, max_out: int = 20000) -> None:
    cfg = Config(
        permissions=PermissionsConfig(command=mode), max_command_output=max_out
    )
    monkeypatch.setattr("mycode.config.load_config", lambda: cfg)


def _auto_yes(monkeypatch) -> None:
    monkeypatch.setattr("mycode.ui.print_command", lambda *a, **k: None)
    monkeypatch.setattr("mycode.ui.confirm_write", lambda prompt: True)


def _auto_no(monkeypatch) -> None:
    monkeypatch.setattr("mycode.ui.print_command", lambda *a, **k: None)
    monkeypatch.setattr("mycode.ui.confirm_write", lambda prompt: False)


def test_blacklisted_command_rejected_before_run() -> None:
    # No confirm mocked: must be refused by the blacklist before any execution.
    out = run_bash("rm -rf /")
    assert out.startswith("错误:") and "黑名单" in out


def test_deny_mode_does_not_execute(monkeypatch) -> None:
    _set_cmd_mode(monkeypatch, "deny")
    out = run_bash(f'{PY} -c "print(123)"')
    assert out.startswith("用户拒绝了命令执行")


def test_allow_mode_runs_and_reports_exit_code(monkeypatch) -> None:
    _set_cmd_mode(monkeypatch, "allow")
    out = run_bash(f"{PY} -c \"print('hello123')\"")
    assert "[退出码 0]" in out
    assert "hello123" in out


def test_ask_yes_runs(monkeypatch) -> None:
    _set_cmd_mode(monkeypatch, "ask")
    _auto_yes(monkeypatch)
    out = run_bash(f"{PY} -c \"print('okok')\"")
    assert "[退出码 0]" in out and "okok" in out


def test_ask_no_skips(monkeypatch) -> None:
    _set_cmd_mode(monkeypatch, "ask")
    _auto_no(monkeypatch)
    out = run_bash(f'{PY} -c "print(1)"')
    assert out.startswith("用户拒绝了命令执行")


def test_nonzero_exit_and_stderr_captured(monkeypatch) -> None:
    _set_cmd_mode(monkeypatch, "allow")
    out = run_bash(f"{PY} -c \"import sys; sys.stderr.write('boom'); sys.exit(2)\"")
    assert "[退出码 2]" in out
    assert "boom" in out


def test_cwd_is_project_root(monkeypatch) -> None:
    _set_cmd_mode(monkeypatch, "allow")
    out = run_bash(f"{PY} -c \"import os; print(os.getcwd())\"")
    assert str(Path.cwd().resolve()) in out


def test_output_truncated(monkeypatch) -> None:
    _set_cmd_mode(monkeypatch, "allow", max_out=50)
    out = run_bash(f"{PY} -c \"print('x' * 500)\"")
    assert "已截断" in out
    assert "[退出码 0]" in out


def test_timeout(monkeypatch) -> None:
    _set_cmd_mode(monkeypatch, "allow")
    monkeypatch.setattr("mycode.tools.shell._DEFAULT_TIMEOUT", 1)
    out = run_bash(f"{PY} -c \"import time; time.sleep(5)\"")
    assert out.startswith("错误:") and "超过" in out
    assert "command_timeout" in out


def test_configured_timeout(monkeypatch) -> None:
    cfg = Config(permissions=PermissionsConfig(command="allow"), command_timeout=1)
    monkeypatch.setattr("mycode.config.load_config", lambda: cfg)
    out = run_bash(f"{PY} -c \"import time; time.sleep(5)\"")
    assert out.startswith("错误:") and "1s" in out
    assert "command_timeout = 2" in out


def test_empty_command_rejected() -> None:
    assert run_bash("   ").startswith("错误:")
