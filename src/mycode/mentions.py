"""Expand @file and @dir mentions in user prompts."""

from __future__ import annotations

import re

from mycode.permissions import check_read_path
from mycode.tools.files import list_files, read_file

_MENTION_RE = re.compile(r"(?<![\w.])@(?P<path>[^\s]+)")
_TRAILING = ".,;:!?)，。；：！？）"


def _clean_token(raw: str) -> tuple[str, str]:
    stripped = raw.rstrip(_TRAILING)
    return stripped, raw[len(stripped):]


def expand_mentions(text: str) -> str:
    sections: list[str] = []
    for match in _MENTION_RE.finditer(text):
        token, _suffix = _clean_token(match.group("path"))
        if not token:
            continue
        resolved, denied = check_read_path(token)
        if denied is not None or resolved is None or not resolved.exists():
            continue
        if resolved.is_dir():
            content = list_files(token)
            label = "目录列表"
        elif resolved.is_file():
            content = read_file(token)
            label = "文件内容"
        else:
            continue
        sections.append(f"## @{token} {label}\n{content}")
    if not sections:
        return text
    return text + "\n\n# @引用上下文\n" + "\n\n".join(sections)
