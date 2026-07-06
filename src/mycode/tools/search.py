"""Code search tool: search_code (prefers ripgrep, falls back to Python).

Literal (fixed-string) keyword search so the ripgrep path and the Python
fallback behave the same regardless of whether ``rg`` is installed. Both honor
the shared IGNORE_DIRS.
"""

import fnmatch
import re
import shutil
import subprocess
from pathlib import Path

from mycode.permissions import (
    check_read_path,
    is_sensitive_path,
    sensitive_ripgrep_globs,
)
from mycode.tools._common import IGNORE_DIRS, walk_files
from mycode.tools.registry import register

# 限制返回的匹配行数,避免一次塞爆模型上下文。
_MAX_RESULTS = 200


def _match_file_count(lines: list[str]) -> int:
    files: set[str] = set()
    for line in lines:
        match = re.match(r"^(.*):(\d+):", line)
        if match:
            files.add(match.group(1))
    return len(files)


def _cap(lines: list[str], query: str) -> str:
    if not lines:
        return f"(未找到匹配:{query})"
    file_count = _match_file_count(lines)
    header = f"匹配文件数:{file_count}, 匹配行数:{len(lines)}"
    if len(lines) > _MAX_RESULTS:
        head = "\n".join(lines[:_MAX_RESULTS])
        return (
            f"{header}\n{head}\n\n"
            f"... [匹配过多,共 {len(lines)} 行/{file_count} 文件,仅显示前 {_MAX_RESULTS} 行;"
            "请缩小 query 或 path 后重试]"
        )
    return f"{header}\n" + "\n".join(lines)


def _search_with_ripgrep(rg: str, query: str, path: str) -> str:
    cmd = [rg, "--line-number", "--no-heading", "--color=never", "--fixed-strings"]
    for d in IGNORE_DIRS:
        cmd += ["--glob", f"!**/{d}/**"]
    for glob in sensitive_ripgrep_globs():
        cmd += ["--glob", f"!{glob}"]
    cmd += ["--", query, path]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"错误:执行 ripgrep 失败:{exc}"

    # rg: 0=有匹配, 1=无匹配, >=2=出错
    if proc.returncode not in (0, 1):
        return f"错误:ripgrep 执行出错(返回码 {proc.returncode}):{(proc.stderr or '').strip()}"
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    return _cap(lines, query)


def _search_with_python(query: str, root: Path) -> str:
    matches: list[str] = []
    for file_path in walk_files(root):
        if is_sensitive_path(file_path):
            continue  # 敏感文件不纳入搜索结果
        try:
            with file_path.open("r", encoding="utf-8", errors="ignore") as fh:
                for lineno, line in enumerate(fh, start=1):
                    if query in line:
                        matches.append(f"{file_path}:{lineno}:{line.rstrip()}")
                        if len(matches) > _MAX_RESULTS:
                            return _cap(matches, query)
        except OSError:
            continue  # 读不了的文件直接跳过
    return _cap(matches, query)


@register(
    name="search_code",
    description="在目录中搜索包含指定关键字/文本的代码行(字面匹配;优先用 ripgrep,未安装则用 Python 遍历)。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "要搜索的关键字/文本(字面匹配)"},
            "path": {"type": "string", "description": "搜索目录,默认当前目录 '.'"},
        },
        "required": ["query"],
    },
)
def search_code(query: str, path: str = ".") -> str:
    if not query:
        return "错误:query 不能为空"
    root, denied = check_read_path(path)
    if denied is not None or root is None:
        return denied or "错误:权限拒绝"
    if not root.exists():
        return f"错误:路径不存在:{path}"

    rg = None if (Path.cwd() / ".mycodeignore").is_file() else shutil.which("rg")
    if rg:
        return _search_with_ripgrep(rg, query, str(root))
    return _search_with_python(query, root)


@register(
    name="find_files",
    description="按 glob pattern 查找文件路径(只读;遵守 .mycodeignore 和内置忽略目录)。",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "文件 glob,如 '*.py' 或 'src/**/*.py'"},
            "path": {"type": "string", "description": "搜索目录,默认当前目录 '.'"},
        },
        "required": ["pattern"],
    },
)
def find_files(pattern: str, path: str = ".") -> str:
    if not pattern:
        return "错误:pattern 不能为空"
    root, denied = check_read_path(path)
    if denied is not None or root is None:
        return denied or "错误:权限拒绝"
    if not root.exists():
        return f"错误:路径不存在:{path}"
    if root.is_file():
        rel = root.name
        return rel if fnmatch.fnmatch(rel, pattern) else f"(未找到文件:{pattern})"

    matches: list[str] = []
    for file_path in walk_files(root):
        if is_sensitive_path(file_path):
            continue
        rel = file_path.relative_to(root).as_posix()
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(file_path.name, pattern):
            matches.append(rel)
        if len(matches) > _MAX_RESULTS:
            head = "\n".join(matches[:_MAX_RESULTS])
            return f"{head}\n\n... [文件过多,共超过 {_MAX_RESULTS} 个,请缩小 pattern 或 path]"
    if not matches:
        return f"(未找到文件:{pattern})"
    return "\n".join(matches)
