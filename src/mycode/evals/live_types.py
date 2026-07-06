"""Data structures for real-provider task evaluations."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


@dataclass(frozen=True)
class LiveEvalCase:
    name: str
    suite: str
    prompt: str
    language: str
    case_dir: Path
    safety: Literal["safe", "host"] = "safe"
    max_steps: int = 12
    timeout_s: float = 180.0
    expected_contains: tuple[str, ...] = ()
    ast_parse: tuple[str, ...] = ()
    expected_tool_sequence: tuple[str, ...] = ()
    protected_files: tuple[str, ...] = ()
    expected_status: str = "completed"
    expected_permission_denied: bool = False
    allow_extra_writes: bool = False

    @property
    def workspace_dir(self) -> Path:
        return self.case_dir / "workspace"

    @property
    def expected_dir(self) -> Path:
        return self.case_dir / "expected"


@dataclass(frozen=True)
class ModelTarget:
    provider: str
    model: str
    temperature: float | None
    config_sources: tuple[str, ...] = ()


@dataclass
class LiveEvalTrial:
    case_name: str
    repetition: int
    passed: bool
    run_status: str
    scores: dict[str, bool]
    score_details: dict[str, str]
    tool_sequence: list[str]
    changed_files: list[str]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None
    latency_s: float = 0.0
    trace_path: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LiveEvalRun:
    run_id: str
    suite: str
    target: ModelTarget
    repeat: int
    budget_usd: float
    auto_context: bool
    started_at: str
    trials: list[LiveEvalTrial] = field(default_factory=list)
    stopped_reason: str | None = None
    output_dir: Path | None = None

    @property
    def success_rate(self) -> float:
        return sum(trial.passed for trial in self.trials) / len(self.trials) if self.trials else 0.0

    @property
    def total_cost_usd(self) -> float | None:
        costs = [trial.cost_usd for trial in self.trials]
        return None if any(cost is None for cost in costs) else sum(cost or 0.0 for cost in costs)

    @property
    def pass_at_1(self) -> float:
        first: dict[str, bool] = {}
        for trial in self.trials:
            first.setdefault(trial.case_name, trial.passed)
        return sum(first.values()) / len(first) if first else 0.0

    @property
    def pass_at_3(self) -> float:
        grouped: dict[str, list[bool]] = {}
        for trial in self.trials:
            grouped.setdefault(trial.case_name, []).append(trial.passed)
        return sum(any(values[:3]) for values in grouped.values()) / len(grouped) if grouped else 0.0

    def summary(self) -> dict[str, Any]:
        latencies = sorted(trial.latency_s for trial in self.trials)
        p95_index = min(len(latencies) - 1, int(len(latencies) * 0.95)) if latencies else 0
        return {
            "run_id": self.run_id,
            "suite": self.suite,
            "target": asdict(self.target),
            "repeat": self.repeat,
            "budget_usd": self.budget_usd,
            "auto_context": self.auto_context,
            "cases": len({trial.case_name for trial in self.trials}),
            "trials": len(self.trials),
            "passed": sum(trial.passed for trial in self.trials),
            "success_rate": self.success_rate,
            "pass_at_1": self.pass_at_1,
            "pass_at_3": self.pass_at_3,
            "prompt_tokens": sum(trial.prompt_tokens for trial in self.trials),
            "completion_tokens": sum(trial.completion_tokens for trial in self.trials),
            "total_cost_usd": self.total_cost_usd,
            "latency_p95_s": latencies[p95_index] if latencies else 0.0,
            "stopped_reason": self.stopped_reason,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.summary(), "started_at": self.started_at, "trials_detail": [trial.to_dict() for trial in self.trials]}


__all__ = ["LiveEvalCase", "LiveEvalRun", "LiveEvalTrial", "ModelTarget"]
