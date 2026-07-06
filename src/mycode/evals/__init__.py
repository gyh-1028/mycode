"""Offline evaluation framework for mycode agent behavior.

Provides deterministic, network-free eval cases that exercise the
:class:`~mycode.agent.runner.AgentRunner` end-to-end: real tools run against
temp workspaces, a scripted :class:`~mycode.evals.fakes.FakeProvider` supplies the
model responses, and structured scorers check the outcome.

Cases live in ``evals/cases/*.py`` (each defining ``CASES: list[EvalCase]``);
baselines live in ``evals/baselines/<case_name>.json``. Run via
``mycode eval run``.
"""

from mycode.evals.baseline import (
    is_regression,
    load_baseline,
    save_baseline,
)
from mycode.evals.cli import handle_eval
from mycode.evals.live_loader import load_live_suite
from mycode.evals.live_runner import compare_live_runs, run_live_suite
from mycode.evals.live_types import LiveEvalCase, LiveEvalRun, LiveEvalTrial, ModelTarget
from mycode.evals.loader import load_cases
from mycode.evals.runner import run_all, run_case
from mycode.evals.types import EvalBaseline, EvalCase, EvalResult, EvalScore

__all__ = [
    "EvalBaseline",
    "EvalCase",
    "EvalResult",
    "EvalScore",
    "LiveEvalCase",
    "LiveEvalRun",
    "LiveEvalTrial",
    "ModelTarget",
    "compare_live_runs",
    "handle_eval",
    "is_regression",
    "load_baseline",
    "load_cases",
    "load_live_suite",
    "run_all",
    "run_case",
    "run_live_suite",
    "save_baseline",
]
