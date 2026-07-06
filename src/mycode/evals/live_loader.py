"""TOML manifest loader for live eval suites."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from mycode.evals.live_types import LiveEvalCase

DEFAULT_LIVE_ROOT = Path("evals/live")


def load_live_suite(suite: str, root: Path = DEFAULT_LIVE_ROOT) -> list[LiveEvalCase]:
    suite_dir = root / suite
    if not suite_dir.is_dir():
        raise FileNotFoundError(f"live eval suite not found: {suite_dir}")
    cases = [_load_case(path, suite) for path in sorted(suite_dir.glob("*/case.toml"))]
    if not cases:
        raise ValueError(f"live eval suite has no cases: {suite}")
    names = [case.name for case in cases]
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate case name in suite {suite}")
    return cases


def _load_case(path: Path, suite: str) -> LiveEvalCase:
    with path.open("rb") as handle:
        data: dict[str, Any] = tomllib.load(handle)
    expected = data.get("expected") or {}
    safety = str(data.get("safety", "safe"))
    if safety not in {"safe", "host"}:
        raise ValueError(f"invalid safety in {path}: {safety}")
    if not (path.parent / "workspace").is_dir() or not (path.parent / "expected").is_dir():
        raise ValueError(f"case requires workspace/ and expected/: {path.parent}")
    return LiveEvalCase(
        name=str(data.get("name") or path.parent.name),
        suite=suite,
        prompt=str(data["prompt"]),
        language=str(data.get("language", "python")),
        case_dir=path.parent,
        safety=safety,  # type: ignore[arg-type]
        max_steps=int(data.get("max_steps", 12)),
        timeout_s=float(data.get("timeout_s", 180.0)),
        expected_contains=tuple(str(value) for value in expected.get("contains", [])),
        ast_parse=tuple(str(value) for value in expected.get("ast_parse", [])),
        expected_tool_sequence=tuple(str(value) for value in expected.get("tool_sequence", [])),
        protected_files=tuple(str(value) for value in expected.get("protected_files", [])),
        expected_status=str(expected.get("status", "completed")),
        expected_permission_denied=bool(expected.get("permission_denied", False)),
        allow_extra_writes=bool(expected.get("allow_extra_writes", False)),
    )


def list_live_suites(root: Path = DEFAULT_LIVE_ROOT) -> list[str]:
    if not root.is_dir():
        return []
    return sorted(path.name for path in root.iterdir() if path.is_dir() and any(path.glob("*/case.toml")))


__all__ = ["DEFAULT_LIVE_ROOT", "list_live_suites", "load_live_suite"]
