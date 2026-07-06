"""Command execution tool: run_bash.

Pipeline: blacklist check -> confirm -> run via the system shell with cwd pinned
to the project root, a timeout, and stdout/stderr captured. Oversized output is
truncated. Returns ``[退出码 N]`` followed by the (possibly truncated) output.

This is a guardrail, not a sandbox: cwd is pinned but a command can still `cd`
elsewhere or do damage. Real isolation needs OS-level means (see permissions).
"""

import os
import subprocess

from mycode.approvals import effective_permission
from mycode.permissions import (
    classify_command_risk,
    command_denial_reason,
    command_path_denial_reason,
    project_root,
)
from mycode.tools.registry import register
from mycode.ui import request_command_approval

_DEFAULT_TIMEOUT = 60  # 秒


def _combine_output(stdout: str, stderr: str) -> str:
    parts: list[str] = []
    if stdout and stdout.strip():
        parts.append(stdout.rstrip("\n"))
    if stderr and stderr.strip():
        parts.append("--- stderr ---\n" + stderr.rstrip("\n"))
    return "\n".join(parts)


def _truncate(text: str, limit: int) -> str:
    """超过 limit 字符则保留首尾、省略中间并标注。"""
    if limit <= 0 or len(text) <= limit:
        return text
    head = limit // 2
    tail = limit - head
    omitted = len(text) - limit
    return (
        text[:head]
        + f"\n\n... [输出过长,已截断,省略中间 {omitted} 字符] ...\n\n"
        + text[-tail:]
    )


@register(
    name="run_bash",
    description=(
        "在项目根目录下执行一条 shell 命令(先过危险命令黑名单,并请用户确认)。"
        "返回退出码与(超长会截断的)输出。适合运行测试、构建、git 等。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 shell 命令"}
        },
        "required": ["command"],
    },
)
def run_bash(command: str) -> str:
    if not command or not command.strip():
        return "错误:command 不能为空"

    from mycode.config import load_config

    # 1. 危险命令黑名单(减速带)
    denied = command_denial_reason(command)
    if denied is not None:
        return denied
    denied = command_path_denial_reason(command)
    if denied is not None:
        return denied

    cfg = load_config()
    timeout = _DEFAULT_TIMEOUT if cfg.command_timeout == 60 else cfg.command_timeout

    # 2. 执行前确认(对应 permissions.command)
    risk = classify_command_risk(command)
    if not request_command_approval(
        command,
        mode=effective_permission(cfg.permissions.command),
        risk=risk,
    ):
        return f"用户拒绝了命令执行:{command}"

    # 3. 执行:cwd 限制在项目根、带超时;让子进程以 UTF-8 输出便于解码
    child_env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(project_root()),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=child_env,
        )
    except subprocess.TimeoutExpired:
        suggested = max(timeout + 1, timeout * 2)
        return (
            f"错误:命令超过 {timeout}s 未结束,已被终止:{command}\n"
            f"提示:可在 .mycode/config.toml 设置 command_timeout = {suggested} 后重试。"
        )
    except OSError as exc:
        return f"错误:命令执行失败:{exc}"

    # 4. 合并 stdout/stderr 并按 max_command_output 截断
    output = _truncate(
        _combine_output(proc.stdout or "", proc.stderr or ""),
        cfg.max_command_output,
    )
    if output.strip():
        return f"[退出码 {proc.returncode}]\n{output}"
    return f"[退出码 {proc.returncode}](无输出)"
