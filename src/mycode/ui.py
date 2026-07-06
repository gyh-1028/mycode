"""Terminal UI helpers: unified diffs, colored rendering, write confirmation.

Used by the write/edit tools to preview changes (green additions / red
deletions) and ask the user to approve them before anything touches disk.
"""

import difflib

import typer
from rich.console import Console
from rich.text import Text

from mycode.approvals import ApprovalRequest, decide

_console = Console()


def make_unified_diff(path: str, old: str, new: str) -> str:
    """Return a unified diff between old and new content (empty if identical)."""
    diff = difflib.unified_diff(
        old.splitlines(),
        new.splitlines(),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="",
    )
    return "\n".join(diff)


def diff_stats(diff_text: str) -> tuple[int, int]:
    """Count added / removed lines in a unified diff (ignoring the +++/--- header)."""
    added = removed = 0
    for line in diff_text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def render_diff(diff_text: str) -> Text:
    """Colorize a unified diff: additions green, deletions red, hunks cyan."""
    text = Text()
    for line in diff_text.splitlines():
        if line.startswith(("+++", "---")):
            text.append(line + "\n", style="bold")
        elif line.startswith("@@"):
            text.append(line + "\n", style="cyan")
        elif line.startswith("+"):
            text.append(line + "\n", style="green")
        elif line.startswith("-"):
            text.append(line + "\n", style="red")
        else:
            text.append(line + "\n")
    return text


def print_diff(diff_text: str, console: Console | None = None) -> None:
    (console or _console).print(render_diff(diff_text))


def confirm_write(prompt: str) -> bool:
    """Interactive y/N prompt. Defaults to No; non-interactive (EOF) -> No."""
    try:
        return typer.confirm(prompt, default=False)
    except (EOFError, typer.Abort):
        return False


def request_write_approval(
    display_path: str,
    diff_text: str,
    mode: str = "ask",
    action: str = "写入",
    console: Console | None = None,
) -> bool:
    """Decide whether a write may proceed, per permissions.write mode.

    - "allow": proceed silently.
    - "deny": refuse.
    - "ask" (default): show the diff and prompt y/N.
    """
    normalized = (mode or "ask").strip().lower()
    if normalized in ("allow", "yes", "always", "auto"):
        return True
    if normalized in ("deny", "no", "never"):
        return False
    prompt = f"确认{action} {display_path}?"

    def fallback() -> bool:
        print_diff(diff_text, console=console)
        return confirm_write(prompt)

    return decide(
        ApprovalRequest(
            kind="write",
            prompt=prompt,
            display_path=display_path,
            diff=diff_text,
            action=action,
        ),
        fallback,
    )


def print_stream_chunk(chunk: str, console: Console | None = None) -> None:
    """即时写出一段流式文本(不加换行、不做 Rich 解析),并立刻 flush。"""
    target = console or _console
    target.file.write(chunk)
    target.file.flush()


def print_reasoning_chunk(
    chunk: str, *, is_first: bool = False, console: Console | None = None
) -> None:
    """以灰色前缀输出推理模型思考链的一个片段,不换行。"""
    target = console or _console
    if is_first:
        target.print("思考: ", style="dim", end="", markup=False)
    target.print(chunk, style="dim", end="", markup=False)
    target.file.flush()


def print_usage(
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    console: Console | None = None,
    cached_tokens: int = 0,
    estimated_cost: float | None = None,
) -> None:
    """整轮任务结束后展示累计 token 用量;命中缓存时附带省下的 token。"""
    line = (
        f"本次任务累计 token:输入 {prompt_tokens} + 输出 {completion_tokens} "
        f"= 共 {total_tokens}"
    )
    if cached_tokens > 0:
        line += f"(其中 {cached_tokens} 输入 token 命中缓存,已省)"
    if estimated_cost is not None:
        line += f", 估算成本 ${estimated_cost:.6f}"
    (console or _console).print(Text(line, style="bright_black"))


def print_command(command: str, console: Console | None = None) -> None:
    (console or _console).print(Text(f"$ {command}", style="yellow"))


def request_command_approval(
    command: str,
    mode: str = "ask",
    console: Console | None = None,
    risk: str | None = None,
) -> bool:
    """Decide whether a shell command may run, per permissions.command mode."""
    normalized = (mode or "ask").strip().lower()
    if normalized in ("allow", "yes", "always", "auto"):
        return True
    if normalized in ("deny", "no", "never"):
        return False
    suffix = f"(风险:{risk})" if risk else ""
    prompt = f"确认运行该命令{suffix}?"

    def fallback() -> bool:
        print_command(command, console=console)
        return confirm_write(prompt)

    return decide(
        ApprovalRequest(
            kind="command",
            prompt=prompt,
            command=command,
            risk=risk,
        ),
        fallback,
    )
