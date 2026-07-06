"""Eval runner: execute a case against the AgentRunner and score it.

The runner:

1. Creates a temp workspace and writes the case's fixture files.
2. Snapshots all files (before).
3. Sets ``MYCODE_PERMISSION_WRITE=allow`` and ``MYCODE_PERMISSION_COMMAND=allow``
   so writes/commands proceed deterministically without interactive prompts.
4. chdir's into the workspace (tools use ``Path.cwd()`` as the project root).
5. Builds a :class:`FakeProvider` with the scripted responses, collects
   ``TOOL_CALL_STARTED`` events to assemble the tool sequence, and runs the
   :class:`AgentRunner`.
6. Snapshots files (after), runs all applicable scorers, and returns an
   :class:`EvalResult`.

Everything runs offline — no network, no API key. The provider is fully
scripted; the tools execute for real against the temp workspace.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mycode.agent.events import EventType
from mycode.agent.runner import AgentRunner, RunRequest
from mycode.evals.fakes import FakeProvider
from mycode.evals.scorers import run_all_scorers
from mycode.evals.types import EvalCase, EvalResult
from mycode.prompts import build_system_prompt
from mycode.tools import get_schemas


def _snapshot_files(workspace: Path) -> dict[str, str]:
    """Read every file under the workspace (recursive) into {relpath: content}."""
    snapshot: dict[str, str] = {}
    for path in workspace.rglob("*"):
        if path.is_file():
            rel = path.relative_to(workspace).as_posix()
            try:
                snapshot[rel] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                snapshot[rel] = ""
    return snapshot


def _build_messages(case: EvalCase) -> list[dict[str, Any]]:
    if case.initial_messages:
        messages = [dict(m) for m in case.initial_messages]
        if not any(m.get("role") == "system" for m in messages):
            messages.insert(0, {"role": "system", "content": build_system_prompt()})
    else:
        messages = [{"role": "system", "content": build_system_prompt()}]
    messages.append({"role": "user", "content": case.prompt})
    return messages


def run_case(case: EvalCase, workspace: Path) -> EvalResult:
    """Run one eval case and return its scored result."""
    # 1. Write fixture files.
    workspace.mkdir(parents=True, exist_ok=True)
    for relpath, content in case.files.items():
        target = workspace / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    # 2. Snapshot before.
    before = _snapshot_files(workspace)

    # 3 & 4. Set permission env vars + chdir into workspace.
    old_cwd = Path.cwd()
    saved_env: dict[str, str] = {}
    for key in ("MYCODE_PERMISSION_WRITE", "MYCODE_PERMISSION_COMMAND"):
        saved_env[key] = os.environ.get(key, "")
        os.environ[key] = "allow"
    old_mypy = os.environ.get("MYPY_CONFIG_FILE", "")
    # Avoid config loading surprises from the real project .mycode/config.toml.
    os.environ.pop("MYPY_CONFIG_FILE", None)

    tool_sequence: list[str] = []
    permission_denied_seen = False
    events_collector: list = []

    def _collect_sink(event, attachments):  # noqa: ARG001
        events_collector.append(event)
        if event.type == EventType.TOOL_CALL_STARTED:
            tool_sequence.append(event.payload.get("name", ""))

    os.chdir(workspace)
    try:
        provider = FakeProvider(list(case.responses))
        messages = _build_messages(case)
        request = RunRequest(
            provider=provider,
            messages=messages,
            max_steps=case.max_steps,
            tools=get_schemas(),
            budget_usd=case.budget_usd,
            context_limit=case.context_limit,
            planning="off",
        )
        runner = AgentRunner(sinks=[_collect_sink])
        run_result = runner.run(request)

        # Detect permission denial by scanning tool results in messages.
        for msg in messages:
            if msg.get("role") == "tool":
                content = str(msg.get("content", ""))
                if "权限拒绝" in content:
                    permission_denied_seen = True
    except Exception as exc:  # noqa: BLE001 - eval must not crash the suite
        return EvalResult(
            case_name=case.name,
            status="error",
            run_status="error",
            final_text=None,
            tool_sequence=tool_sequence,
            file_diffs={},
            scores=[],
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        os.chdir(old_cwd)
        for key, val in saved_env.items():
            if val:
                os.environ[key] = val
            else:
                os.environ.pop(key, None)
        if old_mypy:
            os.environ["MYPY_CONFIG_FILE"] = old_mypy

    # 5. Snapshot after.
    after = _snapshot_files(workspace)

    # 6. Compute file diffs (only files that changed).
    all_paths = set(before) | set(after)
    file_diffs: dict[str, tuple[str | None, str | None]] = {}
    for rel in sorted(all_paths):
        b = before.get(rel)
        a = after.get(rel)
        if b != a:
            file_diffs[rel] = (b, a)

    # 7. Build result and run scorers.
    result = EvalResult(
        case_name=case.name,
        status="pass",  # will be overridden if any scorer fails
        run_status=run_result.status,
        final_text=run_result.final_text,
        tool_sequence=tool_sequence,
        file_diffs=file_diffs,
        scores=[],
        run_result=run_result,
    )
    # Stash the permission-denied flag for the scorer (avoids scanning events).
    result._permission_denied = permission_denied_seen  # type: ignore[attr-defined]

    result.scores = run_all_scorers(result, case, workspace, before, after)
    if any(not s.passed for s in result.scores):
        result.status = "fail"

    return result


def run_all(cases: list[EvalCase], workspace_root: Path) -> list[EvalResult]:
    """Run every case in its own subdirectory under workspace_root."""
    results: list[EvalResult] = []
    for case in cases:
        ws = workspace_root / case.name
        results.append(run_case(case, ws))
    return results


__all__ = ["FakeProvider", "run_all", "run_case"]
