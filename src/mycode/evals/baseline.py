"""Baseline persistence and regression comparison.

Baselines are stored as ``evals/baselines/<case_name>.json``. A baseline
captures the last-known-good status and the set of scorers that passed. When a
case's current result is compared against its baseline, a **regression** is:

* the baseline status was ``pass`` but the current status is ``fail``/``error``,
  OR
* a scorer that was in the baseline's ``passing_scorers`` now fails.

Per the roadmap, baselines must **not** be auto-overwritten. Updating them
requires an explicit ``--update-baselines`` flag (see :mod:`mycode.evals.cli`).
"""

from __future__ import annotations

import json
from pathlib import Path

from mycode.evals.types import EvalBaseline, EvalResult

DEFAULT_BASELINE_DIR = Path("evals") / "baselines"


def baseline_path(case_name: str, baseline_dir: Path | None = None) -> Path:
    directory = baseline_dir or DEFAULT_BASELINE_DIR
    return directory / f"{case_name}.json"


def load_baseline(case_name: str, baseline_dir: Path | None = None) -> EvalBaseline | None:
    path = baseline_path(case_name, baseline_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return EvalBaseline.from_dict(data)


def save_baseline(result: EvalResult, baseline_dir: Path | None = None) -> Path:
    path = baseline_path(result.case_name, baseline_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    baseline = EvalBaseline.from_result(result)
    path.write_text(
        json.dumps(baseline.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_all_baselines(baseline_dir: Path | None = None) -> dict[str, EvalBaseline]:
    directory = baseline_dir or DEFAULT_BASELINE_DIR
    baselines: dict[str, EvalBaseline] = {}
    if not directory.is_dir():
        return baselines
    for path in directory.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        b = EvalBaseline.from_dict(data)
        baselines[b.case_name] = b
    return baselines


def is_regression(result: EvalResult, baseline: EvalBaseline | None) -> tuple[bool, str]:
    """Return (is_regression, reason). A missing baseline is never a regression."""
    if baseline is None:
        return False, "(无基线,跳过回归判断)"
    if baseline.status == "pass" and result.status != "pass":
        return True, f"基线为 pass,当前为 {result.status}"
    # Check for scorer-level regressions (even if overall status is still pass).
    current_passing = set(result.passing_scorer_names)
    baseline_passing = set(baseline.passing_scorers)
    regressed = baseline_passing - current_passing
    if regressed:
        return True, f"评分器退化: {sorted(regressed)}"
    return False, ""


__all__ = [
    "DEFAULT_BASELINE_DIR",
    "baseline_path",
    "is_regression",
    "load_all_baselines",
    "load_baseline",
    "save_baseline",
]
