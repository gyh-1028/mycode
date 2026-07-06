"""Real-provider eval execution with isolated workspaces and built-in graders."""

from __future__ import annotations

import ast
import json
import os
import shutil
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from mycode.agent.events import AgentEvent, EventType
from mycode.agent.runner import AgentRunner, RunRequest
from mycode.codeintel.context import ContextSelector
from mycode.config import Config, ConfigLoadResult
from mycode.evals.live_types import LiveEvalCase, LiveEvalRun, LiveEvalTrial, ModelTarget
from mycode.llm import BaseProvider, build_provider
from mycode.pricing import price_for_model
from mycode.prompts import build_system_prompt
from mycode.tools import get_schemas
from mycode.trace import TraceConfig, TraceWriter

SAFE_TOOLS = {
    "list_files",
    "read_file",
    "find_files",
    "search_code",
    "edit_file",
    "write_file",
    "apply_patch",
    "search_symbols",
    "find_definition",
    "find_references",
    "get_diagnostics",
}
HOST_TOOLS = SAFE_TOOLS | {"run_bash"}
ProviderFactory = Callable[[LiveEvalCase, int], BaseProvider]


def run_live_suite(
    cases: list[LiveEvalCase],
    config_result: ConfigLoadResult,
    *,
    repeat: int = 1,
    budget_usd: float = 1.0,
    allow_unknown_pricing: bool = False,
    allow_host_exec: bool = False,
    auto_context: bool | None = None,
    output_root: Path = Path(".mycode/evals/runs"),
    provider_factory: ProviderFactory | None = None,
) -> LiveEvalRun:
    if repeat < 1:
        raise ValueError("repeat must be at least 1")
    config = config_result.config
    if price_for_model(config.default_model, config.pricing) is None and not allow_unknown_pricing:
        raise ValueError(f"unknown pricing for model {config.default_model}; configure pricing or pass --allow-unknown-pricing")
    if any(case.safety == "host" for case in cases) and not allow_host_exec:
        raise PermissionError("host eval suite requires --unsafe-allow-host-exec")

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    output_dir = (output_root / run_id).resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    target = ModelTarget(
        provider=config.provider.type,
        model=config.default_model,
        temperature=config.provider.temperature,
        config_sources=tuple(str(path) for path in config_result.files),
    )
    use_context = config.codeintel.auto_context if auto_context is None else auto_context
    run = LiveEvalRun(
        run_id=run_id,
        suite=cases[0].suite if cases else "",
        target=target,
        repeat=repeat,
        budget_usd=budget_usd,
        auto_context=use_context,
        started_at=datetime.now(UTC).isoformat(),
        output_dir=output_dir,
    )
    factory = provider_factory or _provider_factory(config)

    for case in cases:
        for repetition in range(1, repeat + 1):
            spent = run.total_cost_usd
            if spent is not None and spent >= budget_usd:
                run.stopped_reason = "budget_exceeded"
                _save_run(run)
                return run
            trial_dir = output_dir / "workspaces" / case.name / str(repetition)
            trial_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(case.workspace_dir, trial_dir)
            remaining = None if spent is None else max(0.0, budget_usd - spent)
            trial = _run_trial(
                case,
                repetition,
                trial_dir,
                output_dir,
                config,
                factory(case, repetition),
                remaining,
                use_context,
            )
            run.trials.append(trial)
            _save_run(run)
    return run


def _provider_factory(config: Config) -> ProviderFactory:
    key = config.provider.resolve_api_key()

    def factory(case: LiveEvalCase, repetition: int) -> BaseProvider:  # noqa: ARG001
        return build_provider(config, key)

    return factory


def _run_trial(
    case: LiveEvalCase,
    repetition: int,
    workspace: Path,
    output_dir: Path,
    config: Config,
    provider: BaseProvider,
    budget_usd: float | None,
    auto_context: bool,
) -> LiveEvalTrial:
    before = _snapshot(workspace)
    events: list[AgentEvent] = []

    def sink(event: AgentEvent, attachments: dict[str, object]) -> None:  # noqa: ARG001
        events.append(event)

    run_id = f"{case.name}-{repetition}-{uuid.uuid4().hex[:6]}"
    trace = TraceWriter(TraceConfig(enabled=True, directory=output_dir / "traces"), run_id)
    allowed = HOST_TOOLS if case.safety == "host" else SAFE_TOOLS
    schemas = [schema for schema in get_schemas() if schema["name"] in allowed]
    started = time.monotonic()
    try:
        with _workspace_environment(workspace, host=case.safety == "host"):
            selector = (
                ContextSelector(workspace, config.codeintel.model_copy(update={"auto_context": True}), context_limit=config.context_limit)
                if auto_context and config.codeintel.enabled
                else None
            )
            result = AgentRunner(sinks=[sink], trace=trace).run(
                RunRequest(
                    provider=provider,
                    messages=[
                        {"role": "system", "content": build_system_prompt(workspace, active_skills=[])},
                        {"role": "user", "content": case.prompt},
                    ],
                    max_steps=case.max_steps,
                    tools=schemas,
                    allowed_tool_names=set(allowed),
                    context_limit=config.context_limit,
                    planning="off",
                    budget_usd=budget_usd,
                    model=config.default_model,
                    pricing_overrides=config.pricing,
                    deadline_s=case.timeout_s,
                    retry_transient=True,
                    max_retries=config.provider.max_retries,
                    retry_backoff=config.provider.retry_backoff,
                    context_selector=selector,
                    run_id=run_id,
                )
            )
    except Exception as exc:  # noqa: BLE001
        trace.close()
        return LiveEvalTrial(
            case.name,
            repetition,
            False,
            "error",
            {"runner": False},
            {"runner": str(exc)},
            [],
            [],
            latency_s=time.monotonic() - started,
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        trace.close()

    after = _snapshot(workspace)
    tool_sequence = [str(event.payload.get("name")) for event in events if event.type == EventType.TOOL_CALL_STARTED]
    permission_denied = any(
        event.type == EventType.TOOL_CALL_FINISHED
        and "权限拒绝" in str(event.payload.get("error_signature", ""))
        for event in events
    )
    changed = sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
    scores, details = _grade(
        case,
        workspace,
        result.status,
        result.final_text or "",
        tool_sequence,
        before,
        after,
        permission_denied,
    )
    return LiveEvalTrial(
        case_name=case.name,
        repetition=repetition,
        passed=all(scores.values()),
        run_status=result.status,
        scores=scores,
        score_details=details,
        tool_sequence=tool_sequence,
        changed_files=changed,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        cost_usd=result.estimated_cost,
        latency_s=time.monotonic() - started,
        trace_path=str(trace.path) if trace.path else None,
        error=result.error,
    )


def _grade(
    case: LiveEvalCase,
    workspace: Path,
    status: str,
    final_text: str,
    tools: list[str],
    before: dict[str, bytes],
    after: dict[str, bytes],
    permission_denied: bool,
) -> tuple[dict[str, bool], dict[str, str]]:
    scores: dict[str, bool] = {}
    details: dict[str, str] = {}

    def record(name: str, passed: bool, detail: str) -> None:
        scores[name] = passed
        details[name] = detail

    record("status", status == case.expected_status, f"expected {case.expected_status}, got {status}")
    if case.expected_contains:
        missing = [value for value in case.expected_contains if value not in final_text]
        record("contains", not missing, f"missing: {missing}" if missing else "all required text present")
    expected_files = _snapshot(case.expected_dir)
    mismatched = [path for path, content in expected_files.items() if after.get(path) != content]
    record("file_state", not mismatched, f"mismatched: {mismatched}" if mismatched else "expected files match")
    ast_errors: list[str] = []
    for rel in case.ast_parse:
        try:
            ast.parse((workspace / rel).read_text(encoding="utf-8"), filename=rel)
        except (OSError, SyntaxError) as exc:
            ast_errors.append(f"{rel}: {exc}")
    if case.ast_parse:
        record("ast", not ast_errors, "; ".join(ast_errors) or "AST valid")
    if case.expected_tool_sequence:
        record("tool_sequence", tools == list(case.expected_tool_sequence), f"expected {list(case.expected_tool_sequence)}, got {tools}")
    if case.protected_files:
        changed_protected = [path for path in case.protected_files if before.get(path) != after.get(path)]
        record("protected_files", not changed_protected, f"changed: {changed_protected}" if changed_protected else "unchanged")
    if not case.allow_extra_writes:
        expected_paths = set(expected_files)
        unexpected = [path for path in set(before) | set(after) if before.get(path) != after.get(path) and path not in expected_paths]
        record("unexpected_writes", not unexpected, f"unexpected: {unexpected}" if unexpected else "none")
    if case.expected_permission_denied:
        record(
            "permission",
            permission_denied,
            "permission denial observed" if permission_denied else "no denial observed",
        )
    return scores, details


def _snapshot(root: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    if not root.is_dir():
        return result
    for path in root.rglob("*"):
        if path.is_file() and ".mycode" not in path.relative_to(root).parts:
            result[path.relative_to(root).as_posix()] = path.read_bytes().replace(b"\r\n", b"\n")
    return result


@contextmanager
def _workspace_environment(workspace: Path, *, host: bool) -> Iterator[None]:
    previous_cwd = Path.cwd()
    previous_config = os.environ.get("MYCODE_CONFIG")
    config_file = workspace.parent / f"eval-config-{workspace.name}.toml"
    config_file.write_text(
        "[permissions]\nwrite = \"allow\"\ncommand = \"allow\"\n" if host else "[permissions]\nwrite = \"allow\"\ncommand = \"deny\"\n",
        encoding="utf-8",
    )
    os.environ["MYCODE_CONFIG"] = str(config_file.resolve())
    os.chdir(workspace)
    try:
        yield
    finally:
        os.chdir(previous_cwd)
        if previous_config is None:
            os.environ.pop("MYCODE_CONFIG", None)
        else:
            os.environ["MYCODE_CONFIG"] = previous_config
        config_file.unlink(missing_ok=True)


def _save_run(run: LiveEvalRun) -> None:
    assert run.output_dir is not None
    payload = run.to_dict()
    (run.output_dir / "run.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (run.output_dir / "summary.json").write_text(json.dumps(run.summary(), ensure_ascii=False, indent=2), encoding="utf-8")


def load_live_run(path_or_id: str, root: Path = Path(".mycode/evals/runs")) -> dict[str, object]:
    path = Path(path_or_id)
    run_file = path / "run.json" if path.is_dir() else root / path_or_id / "run.json"
    if path.is_file():
        run_file = path
    return json.loads(run_file.read_text(encoding="utf-8"))


def compare_live_runs(baseline: dict[str, object], candidate: dict[str, object]) -> dict[str, object]:
    base_rate = float(cast(Any, baseline.get("success_rate", 0.0)))
    candidate_rate = float(cast(Any, candidate.get("success_rate", 0.0)))
    base_tokens = int(cast(Any, baseline.get("prompt_tokens", 0)))
    candidate_tokens = int(cast(Any, candidate.get("prompt_tokens", 0)))
    token_change = (candidate_tokens - base_tokens) / base_tokens if base_tokens else 0.0
    return {
        "baseline_run": baseline.get("run_id"),
        "candidate_run": candidate.get("run_id"),
        "success_rate_delta": candidate_rate - base_rate,
        "prompt_token_change": token_change,
        "meets_codeintel_gate": candidate_rate - base_rate >= 0.10 and token_change <= 0.20,
    }


__all__ = ["compare_live_runs", "load_live_run", "run_live_suite"]
