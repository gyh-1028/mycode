"""Eval data types: cases, scores, results, baselines.

An :class:`EvalCase` bundles everything needed to run one deterministic agent
task offline: the workspace fixture (files), the scripted provider responses,
the user prompt, and the expectations the scorers check against.

An :class:`EvalResult` captures the outcome: run status, final text, the
ordered tool-call sequence, per-file before/after snapshots, the individual
:class:`EvalScore` entries, and the underlying :class:`AgentRunResult`.

An :class:`EvalBaseline` is the snapshot of a case's last-known-good scores,
persisted as JSON in ``evals/baselines/``. Comparing a result against its
baseline is how regressions are detected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mycode.agent.runner import AgentRunResult
from mycode.llm.base import LLMResponse


@dataclass
class EvalCase:
    """One deterministic agent task for offline evaluation."""

    name: str
    prompt: str
    responses: list[LLMResponse] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)
    max_steps: int = 20
    budget_usd: float | None = None
    context_limit: int | None = None
    description: str = ""

    # --- expectations (each drives a scorer; None/empty => scorer skipped) ---
    expected_final_text: str | None = None
    expected_contains: list[str] = field(default_factory=list)
    expected_tool_sequence: list[str] = field(default_factory=list)
    expected_files: dict[str, str] = field(default_factory=dict)
    expected_permission_denied: bool = False
    expected_status: str | None = None  # e.g. "completed", "stuck", "max_steps"

    # Files that must NOT change during the run (unexpected-write scorer).
    protected_files: list[str] = field(default_factory=list)

    # Pre-existing messages for session-resume cases (system prompt is added
    # automatically if not already present).
    initial_messages: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class EvalScore:
    """One scorer's verdict on a result."""

    name: str
    passed: bool
    detail: str = ""


@dataclass
class EvalResult:
    """The full outcome of running one eval case."""

    case_name: str
    status: str  # "pass" / "fail" / "error"
    run_status: str  # AgentRunResult.status
    final_text: str | None
    tool_sequence: list[str]
    file_diffs: dict[str, tuple[str | None, str | None]]  # relpath -> (before, after)
    scores: list[EvalScore]
    run_result: AgentRunResult | None = None
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.status == "pass"

    @property
    def passing_scorer_names(self) -> list[str]:
        return [s.name for s in self.scores if s.passed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_name": self.case_name,
            "status": self.status,
            "run_status": self.run_status,
            "final_text": self.final_text,
            "tool_sequence": self.tool_sequence,
            "file_diffs": {
                k: [v[0], v[1]] for k, v in self.file_diffs.items()
            },
            "scores": [
                {"name": s.name, "passed": s.passed, "detail": s.detail}
                for s in self.scores
            ],
            "error": self.error,
        }


@dataclass
class EvalBaseline:
    """Last-known-good scores for a case, persisted as JSON."""

    case_name: str
    status: str
    passing_scorers: list[str]
    final_text_len: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_name": self.case_name,
            "status": self.status,
            "passing_scorers": self.passing_scorers,
            "final_text_len": self.final_text_len,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvalBaseline:
        return cls(
            case_name=str(data.get("case_name", "")),
            status=str(data.get("status", "fail")),
            passing_scorers=list(data.get("passing_scorers", [])),
            final_text_len=data.get("final_text_len"),
        )

    @classmethod
    def from_result(cls, result: EvalResult) -> EvalBaseline:
        return cls(
            case_name=result.case_name,
            status=result.status,
            passing_scorers=result.passing_scorer_names,
            final_text_len=len(result.final_text) if result.final_text else 0,
        )


__all__ = ["EvalBaseline", "EvalCase", "EvalResult", "EvalScore"]
