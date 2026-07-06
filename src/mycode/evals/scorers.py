"""Scorers: structured verdicts on an eval result.

Each scorer returns an :class:`EvalScore` (or ``None`` when the expectation is
not configured for this case, meaning the scorer is skipped). The eval runner
calls every applicable scorer; a case passes only when all applicable scorers
pass.

Per the P6 roadmap, structural scorers (tool-sequence, file-state,
permission, unexpected-write, run-status) are the core gate; text scorers
(exact, contains) are auxiliary.
"""

from __future__ import annotations

from pathlib import Path

from mycode.evals.types import EvalCase, EvalResult, EvalScore


def _read_file_safe(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# Text scorers (auxiliary)
# --------------------------------------------------------------------------- #
def score_exact(result: EvalResult, case: EvalCase) -> EvalScore | None:
    if case.expected_final_text is None:
        return None
    actual = result.final_text or ""
    passed = actual == case.expected_final_text
    detail = "" if passed else f"期望 {case.expected_final_text!r}, 实际 {actual!r}"
    return EvalScore("exact", passed, detail)


def score_contains(result: EvalResult, case: EvalCase) -> EvalScore | None:
    if not case.expected_contains:
        return None
    text = result.final_text or ""
    missing = [s for s in case.expected_contains if s not in text]
    passed = not missing
    detail = "" if passed else f"缺失子串: {missing}"
    return EvalScore("contains", passed, detail)


# --------------------------------------------------------------------------- #
# Structural scorers (core gate)
# --------------------------------------------------------------------------- #
def score_tool_sequence(result: EvalResult, case: EvalCase) -> EvalScore | None:
    if not case.expected_tool_sequence:
        return None
    expected = case.expected_tool_sequence
    actual = result.tool_sequence
    passed = actual == expected
    detail = "" if passed else f"期望 {expected}, 实际 {actual}"
    return EvalScore("tool_sequence", passed, detail)


def score_file_state(
    result: EvalResult, case: EvalCase, workspace: Path
) -> EvalScore | None:
    if not case.expected_files:
        return None
    mismatches: list[str] = []
    for relpath, expected_content in case.expected_files.items():
        actual = _read_file_safe(workspace / relpath)
        if actual != expected_content:
            mismatches.append(
                f"{relpath}: 期望 {expected_content!r}, 实际 {actual!r}"
            )
    passed = not mismatches
    detail = "" if passed else "; ".join(mismatches)
    return EvalScore("file_state", passed, detail)


def score_permission(result: EvalResult, case: EvalCase) -> EvalScore | None:
    if not case.expected_permission_denied:
        return None
    # We rely on the runner to have populated tool_results_with_denial; if the
    # run_result carries events we could inspect them, but simpler: the runner
    # passes the collected denial flag via result attribute. Fallback: scan
    # file_diffs is not useful here, so we use the attribute set by the runner.
    denied = getattr(result, "_permission_denied", False)
    detail = "" if denied else "期望权限被拒绝,但未观察到拒绝"
    return EvalScore("permission_denied", denied, detail)


def score_unexpected_write(
    result: EvalResult, case: EvalCase, before: dict[str, str], after: dict[str, str]
) -> EvalScore | None:
    if not case.protected_files:
        return None
    changed: list[str] = []
    for relpath in case.protected_files:
        if before.get(relpath) != after.get(relpath):
            changed.append(relpath)
    passed = not changed
    detail = "" if passed else f"受保护文件被修改: {changed}"
    return EvalScore("no_unexpected_writes", passed, detail)


def score_run_status(result: EvalResult, case: EvalCase) -> EvalScore | None:
    if case.expected_status is None:
        return None
    passed = result.run_status == case.expected_status
    detail = "" if passed else f"期望状态 {case.expected_status}, 实际 {result.run_status}"
    return EvalScore("run_status", passed, detail)


def run_all_scorers(
    result: EvalResult,
    case: EvalCase,
    workspace: Path,
    before_snapshot: dict[str, str],
    after_snapshot: dict[str, str],
) -> list[EvalScore]:
    """Run every applicable scorer and return the non-None ones."""
    scores: list[EvalScore] = []
    for scorer in (
        lambda r, c: score_exact(r, c),
        lambda r, c: score_contains(r, c),
        lambda r, c: score_tool_sequence(r, c),
        lambda r, c: score_file_state(r, c, workspace),
        lambda r, c: score_permission(r, c),
        lambda r, c: score_unexpected_write(r, c, before_snapshot, after_snapshot),
        lambda r, c: score_run_status(r, c),
    ):
        score = scorer(result, case)
        if score is not None:
            scores.append(score)
    return scores


__all__ = [
    "run_all_scorers",
    "score_contains",
    "score_exact",
    "score_file_state",
    "score_permission",
    "score_run_status",
    "score_tool_sequence",
    "score_unexpected_write",
]
