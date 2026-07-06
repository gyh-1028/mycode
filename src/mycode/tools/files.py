"""File tools: list/read plus guarded text editing."""

from __future__ import annotations

import re
from pathlib import Path

from mycode.approvals import effective_permission
from mycode.checkpoint import record_file_write
from mycode.permissions import check_read_path, check_write_path, is_sensitive_path
from mycode.tools._common import IGNORE_DIRS, is_ignored
from mycode.tools.registry import register
from mycode.ui import diff_stats, make_unified_diff, request_write_approval

_MAX_TEXT_BYTES = 5_000_000
_HUNK_RE = re.compile(r"^@@ -(?P<old_start>\d+)(?:,\d+)? \+(?P<new_start>\d+)(?:,\d+)? @@")


def _is_probably_binary(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return b"\0" in fh.read(4096)
    except OSError:
        return False


def _text_file_guard(path: Path, display_path: str) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    if size > _MAX_TEXT_BYTES:
        return f"错误:文件过大({size} bytes),拒绝直接读写:{display_path}"
    if _is_probably_binary(path):
        return f"错误:疑似二进制文件,拒绝作为文本处理:{display_path}"
    return None


@register(
    name="list_files",
    description="列出指定目录下的文件和子目录(忽略 .git/node_modules/venv/.venv/__pycache__/dist/build)。",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "要列出的目录路径,默认当前目录 '.'",
            }
        },
    },
)
def list_files(path: str = ".") -> str:
    target, denied = check_read_path(path)
    if denied is not None or target is None:
        return denied or "错误:权限拒绝"
    if not target.exists():
        return f"错误:路径不存在:{path}"
    if not target.is_dir():
        return f"错误:不是目录:{path}(若要读取文件请用 read_file)"
    try:
        entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except OSError as exc:
        return f"错误:无法读取目录 {path}:{exc}"

    lines: list[str] = []
    for entry in entries:
        if is_sensitive_path(entry):
            continue
        if is_ignored(entry, target, is_dir=entry.is_dir()):
            continue
        if entry.is_dir():
            if entry.name in IGNORE_DIRS:
                continue
            lines.append(f"{entry.name}/")
        else:
            lines.append(entry.name)

    if not lines:
        return f"(空目录:{path})"
    return "\n".join(lines)


@register(
    name="read_file",
    description="读取文本文件内容;可用 start_line/end_line 读取行范围,超出 max_file_lines 时截断。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要读取的文件路径"},
            "start_line": {"type": "integer", "description": "可选,1-based 起始行"},
            "end_line": {"type": "integer", "description": "可选,1-based 结束行(包含)"},
        },
        "required": ["path"],
    },
)
def read_file(path: str, start_line: int | None = None, end_line: int | None = None) -> str:
    from mycode.config import load_config

    target, denied = check_read_path(path)
    if denied is not None or target is None:
        return denied or "错误:权限拒绝"
    if not target.exists():
        return f"错误:文件不存在:{path}"
    if target.is_dir():
        return f"错误:这是一个目录而不是文件:{path}(请用 list_files 查看目录)"
    guard = _text_file_guard(target, path)
    if guard is not None:
        return guard

    try:
        with target.open("r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as exc:
        return f"错误:无法读取文件 {path}:{exc}"

    if not lines:
        return f"(空文件:{path})"

    max_lines = load_config().max_file_lines
    total = len(lines)
    if start_line is not None or end_line is not None:
        start = 1 if start_line is None else start_line
        end = total if end_line is None else end_line
        if start < 1 or end < start:
            return "错误:start_line/end_line 范围无效"
        start_idx = start - 1
        end_idx = min(end, total)
        selected = lines[start_idx:end_idx]
        if not selected:
            return f"(行范围为空:{path}:{start}-{end})"
        suffix = ""
        if len(selected) > max_lines:
            selected = selected[:max_lines]
            suffix = (
                f"\n\n... [请求范围共 {end_idx - start_idx} 行,已截断,仅显示前 {max_lines} 行;"
                "建议使用 start_line/end_line 缩小读取范围]"
            )
        return "".join(selected).rstrip("\n") + suffix

    if total > max_lines:
        shown = "".join(lines[:max_lines]).rstrip("\n")
        return (
            f"{shown}\n\n"
            f"... [文件共 {total} 行,已截断,仅显示前 {max_lines} 行;"
            "建议使用 start_line/end_line 读取目标范围]"
        )
    return "".join(lines).rstrip("\n")


def _confirm_and_write(
    target: Path, display_path: str, original: str, new_content: str, action: str
) -> str:
    """生成 diff -> 按 permissions.write 模式确认 -> 落盘或返回拒绝。"""
    from mycode.config import load_config

    if original == new_content:
        return f"(无变化:{display_path} 内容未改变,未写入)"

    diff = make_unified_diff(display_path, original, new_content)
    mode = effective_permission(load_config().permissions.write)
    if not request_write_approval(display_path, diff, mode=mode, action=action):
        return f"用户拒绝了修改:{display_path}(未写入任何内容)"

    try:
        existed = target.exists() and target.is_file()
        record_file_write(target, original, existed)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(new_content, encoding="utf-8")
    except OSError as exc:
        return f"错误:写入失败 {display_path}:{exc}"

    added, removed = diff_stats(diff)
    return f"已{action}:{display_path}(+{added} -{removed} 行)"


@register(
    name="edit_file",
    description=(
        "编辑已有文件:把 old_str 替换为 new_str。old_str 必须在文件中"
        "唯一出现(否则报错)。落盘前会展示 diff 并请用户确认。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要编辑的文件路径"},
            "old_str": {
                "type": "string",
                "description": "要被替换的原文,必须在文件中唯一出现(包含足够上下文以唯一定位)",
            },
            "new_str": {"type": "string", "description": "替换后的新内容"},
        },
        "required": ["path", "old_str", "new_str"],
    },
)
def edit_file(path: str, old_str: str, new_str: str) -> str:
    target, denied = check_write_path(path)
    if denied is not None or target is None:
        return denied or "错误:权限拒绝"
    if not target.exists():
        return f"错误:文件不存在:{path}(如需新建请用 write_file)"
    if target.is_dir():
        return f"错误:这是一个目录而不是文件:{path}"
    guard = _text_file_guard(target, path)
    if guard is not None:
        return guard

    try:
        original = target.read_text(encoding="utf-8")
    except OSError as exc:
        return f"错误:无法读取文件 {path}:{exc}"

    occurrences = original.count(old_str)
    if occurrences == 0:
        return (
            f"错误:在 {path} 中未找到 old_str,无法定位要修改的位置;"
            "请确认原文(含空格/缩进)完全一致。"
        )
    if occurrences > 1:
        return (
            f"错误:old_str 在 {path} 中出现了 {occurrences} 次,不唯一;"
            "请提供更长、更具体的上下文以唯一定位。"
        )

    new_content = original.replace(old_str, new_str, 1)
    return _confirm_and_write(target, path, original, new_content, action="编辑")


@register(
    name="write_file",
    description="写入文件(覆盖原内容,父目录自动创建)。落盘前会展示 diff 并请用户确认。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要写入的文件路径(父目录会自动创建)"},
            "content": {
                "type": "string",
                "description": "要写入的完整文件内容(会覆盖原有内容)",
            },
        },
        "required": ["path", "content"],
    },
)
def write_file(path: str, content: str) -> str:
    target, denied = check_write_path(path)
    if denied is not None or target is None:
        return denied or "错误:权限拒绝"
    if target.is_dir():
        return f"错误:目标是一个目录而不是文件:{path}"
    guard = _text_file_guard(target, path)
    if guard is not None:
        return guard

    original = ""
    if target.exists():
        try:
            original = target.read_text(encoding="utf-8")
        except OSError as exc:
            return f"错误:无法读取已有文件 {path}:{exc}"

    return _confirm_and_write(target, path, original, content, action="写入")


def _parse_unified_patch(patch: str) -> list[tuple[int, list[tuple[str, str]]]]:
    hunks: list[tuple[int, list[tuple[str, str]]]] = []
    lines = patch.splitlines()
    idx = 0
    while idx < len(lines):
        match = _HUNK_RE.match(lines[idx])
        if not match:
            idx += 1
            continue
        old_start = int(match.group("old_start"))
        idx += 1
        hunk: list[tuple[str, str]] = []
        while idx < len(lines) and not lines[idx].startswith("@@ "):
            hline = lines[idx]
            if hline == r"\ No newline at end of file":
                idx += 1
                continue
            if not hline:
                return []
            op = hline[0]
            if op not in {" ", "+", "-"}:
                break
            hunk.append((op, hline[1:]))
            idx += 1
        hunks.append((old_start, hunk))
    return hunks


def _apply_unified_patch_text(original: str, patch: str) -> tuple[str | None, str | None]:
    hunks = _parse_unified_patch(patch)
    if not hunks:
        return None, "错误:未找到 unified diff hunk(@@ ... @@)"

    original_lines = original.splitlines()
    output: list[str] = []
    cursor = 0
    for old_start, hunk in hunks:
        target_idx = old_start - 1
        if target_idx < cursor or target_idx > len(original_lines):
            return None, "错误:patch hunk 行号与文件内容不匹配"
        output.extend(original_lines[cursor:target_idx])
        cursor = target_idx
        for op, text in hunk:
            if op == "+":
                output.append(text)
                continue
            if cursor >= len(original_lines) or original_lines[cursor] != text:
                return None, "错误:patch 上下文与文件内容不匹配"
            if op == " ":
                output.append(original_lines[cursor])
            cursor += 1
    output.extend(original_lines[cursor:])
    new_content = "\n".join(output)
    if original.endswith("\n"):
        new_content += "\n"
    return new_content, None


@register(
    name="apply_patch",
    description="对单个已有文本文件应用 unified diff patch。适合多处小改;落盘前展示 diff 并请用户确认。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要修改的文件路径"},
            "patch": {"type": "string", "description": "针对该文件的 unified diff 内容"},
        },
        "required": ["path", "patch"],
    },
)
def apply_patch(path: str, patch: str) -> str:
    target, denied = check_write_path(path)
    if denied is not None or target is None:
        return denied or "错误:权限拒绝"
    if not target.exists():
        return f"错误:文件不存在:{path}"
    if target.is_dir():
        return f"错误:这是一个目录而不是文件:{path}"
    guard = _text_file_guard(target, path)
    if guard is not None:
        return guard

    try:
        original = target.read_text(encoding="utf-8")
    except OSError as exc:
        return f"错误:无法读取文件 {path}:{exc}"

    new_content, error = _apply_unified_patch_text(original, patch)
    if error is not None or new_content is None:
        return error or "错误:patch 应用失败"
    return _confirm_and_write(target, path, original, new_content, action="打补丁")
