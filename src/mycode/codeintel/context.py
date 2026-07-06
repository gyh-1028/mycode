"""Deterministic, local context selection from the symbol index."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mycode.codeintel.index import SymbolIndex
from mycode.codeintel.models import Symbol
from mycode.config import CodeIntelConfig
from mycode.context import estimate_tokens
from mycode.permissions import check_read_path

_TERM_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_PATH_RE = re.compile(r"(?:@|\b)([A-Za-z0-9_.-]+(?:[/\\][A-Za-z0-9_.-]+)+\.[A-Za-z0-9]+)")
_STOP_TERMS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "file",
    "code",
    "please",
    "error",
    "return",
    "class",
    "function",
}


@dataclass(frozen=True)
class ContextItem:
    path: str
    reason: str
    score: int
    symbol: str | None = None
    start_line: int = 1
    end_line: int = 1


@dataclass(frozen=True)
class ContextPacket:
    content: str = ""
    items: tuple[ContextItem, ...] = ()
    estimated_tokens: int = 0
    degraded: tuple[str, ...] = ()

    def event_payload(self) -> dict[str, Any]:
        return {
            "estimated_tokens": self.estimated_tokens,
            "paths": list(dict.fromkeys(item.path for item in self.items)),
            "items": [
                {
                    "path": item.path,
                    "symbol": item.symbol,
                    "reason": item.reason,
                    "score": item.score,
                    "start_line": item.start_line,
                    "end_line": item.end_line,
                }
                for item in self.items
            ],
            "degraded": list(self.degraded),
        }


class ContextSelector:
    """Select compact symbol bodies and file snippets without mutating history."""

    def __init__(self, root: Path, config: CodeIntelConfig, *, context_limit: int) -> None:
        self.root = root.resolve()
        self.config = config
        self.index = SymbolIndex(self.root)
        self.token_budget = min(
            config.max_context_tokens,
            max(0, int(context_limit * config.max_context_fraction)),
        )
        self._dirty = True
        self._last_errors: tuple[str, ...] = ()

    def invalidate(self) -> None:
        """Request an incremental rebuild before the next selection."""
        self._dirty = True

    def select(self, messages: list[dict[str, Any]]) -> ContextPacket:
        if not self.config.enabled or not self.config.auto_context or self.token_budget <= 0:
            return ContextPacket()
        if self._dirty:
            build = self.index.build()
            self._last_errors = build.errors
            self._dirty = False
        text = _recent_text(messages)
        terms = _terms(text)
        explicit = _explicit_paths(text)
        candidates: list[tuple[int, str, Symbol | None, str]] = []

        for path in explicit:
            candidates.append((200, path, None, "explicit path"))
        for term in terms[:20]:
            for symbol in self.index.search_symbols(term, limit=12):
                score = 120 if symbol.name.lower() == term else 90
                candidates.append((score, symbol.location.path, symbol, f"symbol match: {term}"))
        for path in self.index.files_matching(terms, limit=30):
            candidates.append((50, path, None, "path match"))

        deduped: list[tuple[int, str, Symbol | None, str]] = []
        seen: set[tuple[str, int, int]] = set()
        for candidate in sorted(candidates, key=lambda item: (-item[0], item[1])):
            score, path, symbol, reason = candidate
            start = symbol.location.start_line if symbol else 1
            end = min(symbol.location.end_line or start + 80, start + 199) if symbol else 200
            key = (path, start, end)
            if key not in seen:
                seen.add(key)
                deduped.append((score, path, symbol, reason))

        sections: list[str] = []
        items: list[ContextItem] = []
        selected_paths: set[str] = set()
        for score, path, symbol, reason in deduped:
            if len(items) >= self.config.max_chunks or len(selected_paths) >= self.config.max_files and path not in selected_paths:
                break
            chunk = self._chunk(path, symbol)
            if not chunk:
                continue
            candidate_text = "\n\n".join([*sections, chunk])
            header = _header(items, self.index)
            if estimate_tokens([{"role": "system", "content": header + candidate_text}]) > self.token_budget:
                continue
            sections.append(chunk)
            selected_paths.add(path)
            start = symbol.location.start_line if symbol else 1
            end = min(symbol.location.end_line or start + 80, start + 199) if symbol else min(200, chunk.count("\n") + 1)
            items.append(ContextItem(path, reason, score, symbol.name if symbol else None, start, end))

        content = _header(items, self.index) + "\n\n".join(sections) if sections else ""
        degraded = tuple(self._last_errors[:10])
        return ContextPacket(content, tuple(items), estimate_tokens([{"role": "system", "content": content}]), degraded)

    def _chunk(self, path: str, symbol: Symbol | None) -> str:
        resolved, error = check_read_path(path, self.root)
        if error or resolved is None or not resolved.is_file():
            return ""
        try:
            lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        start = max(1, symbol.location.start_line if symbol else 1)
        end = min(len(lines), symbol.location.end_line or start + 80) if symbol else min(len(lines), 200)
        end = min(end, start + 199)
        body = "\n".join(f"{number:>5}: {lines[number - 1]}" for number in range(start, end + 1))
        language = "python" if resolved.suffix in {".py", ".pyi"} else "typescript" if resolved.suffix.lower() in {".ts", ".tsx", ".js", ".jsx"} else "text"
        return f"### {path}:{start}-{end}\n```{language}\n{body}\n```"


def _recent_text(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages[-8:]:
        if message.get("role") in {"user", "tool"}:
            content = message.get("content")
            if isinstance(content, str):
                parts.append(content[-5000:])
    return "\n".join(parts)


def _terms(text: str) -> list[str]:
    counts: dict[str, int] = {}
    for match in _TERM_RE.findall(text):
        term = match.lower()
        if term not in _STOP_TERMS:
            counts[term] = counts.get(term, 0) + 1
    return [term for term, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def _explicit_paths(text: str) -> list[str]:
    return list(dict.fromkeys(match.replace("\\", "/") for match in _PATH_RE.findall(text)))


def _header(items: list[ContextItem], index: SymbolIndex) -> str:
    if not items:
        return ""
    map_lines = ["# Selected project context", "Repo map:"]
    for path in dict.fromkeys(item.path for item in items):
        symbols = index.symbols_for_file(path, limit=12)
        signatures = [symbol.signature or symbol.name for symbol in symbols]
        suffix = f" - {'; '.join(signatures)}" if signatures else ""
        map_lines.append(f"- {path}{suffix}")
    map_lines.append("Use this context as a hint. Read files or call code-intelligence tools before editing when details are missing.\n")
    return "\n".join(map_lines)


__all__ = ["ContextItem", "ContextPacket", "ContextSelector"]
