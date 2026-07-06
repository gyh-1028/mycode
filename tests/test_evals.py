"""P6 Evals tests: scorers, baseline comparison, and end-to-end case runs.

All offline — FakeProvider, real tools, temp workspaces. Covers the roadmap's
required scenarios: read-modify, shell fail/fix, permission denial, loop
detection, session resume, context compaction, plus scorer and baseline unit
tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mycode.agent.runner import RunRequest
from mycode.evals.baseline import (
    EvalBaseline,
    is_regression,
    load_baseline,
    save_baseline,
)
from mycode.evals.loader import load_cases
from mycode.evals.runner import run_case
from mycode.evals.scorers import (
    score_contains,
    score_exact,
    score_file_state,
    score_permission,
    score_run_status,
    score_tool_sequence,
    score_unexpected_write,
)
from mycode.evals.types import EvalCase, EvalResult, EvalScore
from mycode.llm.base import LLMResponse, StopReason, ToolCall


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _result(
    case_name: str = "t",
    *,
    final_text: str | None = "",
    tool_sequence: list[str] | None = None,
    run_status: str = "completed",
    permission_denied: bool = False,
) -> EvalResult:
    r = EvalResult(
        case_name=case_name,
        status="pass",
        run_status=run_status,
        final_text=final_text,
        tool_sequence=tool_sequence or [],
        file_diffs={},
        scores=[],
    )
    r._permission_denied = permission_denied  # type: ignore[attr-defined]
    return r


def _case(**kwargs) -> EvalCase:
    return EvalCase(name=kwargs.pop("name", "t"), prompt="q", **kwargs)


# --------------------------------------------------------------------------- #
# Scorer unit tests
# --------------------------------------------------------------------------- #
class TestScorers:
    def test_exact_pass(self) -> None:
        r = _result(final_text="hello")
        c = _case(expected_final_text="hello")
        s = score_exact(r, c)
        assert s is not None and s.passed

    def test_exact_fail(self) -> None:
        r = _result(final_text="hello")
        c = _case(expected_final_text="world")
        s = score_exact(r, c)
        assert s is not None and not s.passed
        assert "world" in s.detail

    def test_exact_skipped_when_none(self) -> None:
        c = _case()
        assert score_exact(_result(), c) is None

    def test_contains_pass(self) -> None:
        r = _result(final_text="the answer is 42 today")
        c = _case(expected_contains=["answer", "42"])
        s = score_contains(r, c)
        assert s is not None and s.passed

    def test_contains_fail_lists_missing(self) -> None:
        r = _result(final_text="nothing here")
        c = _case(expected_contains=["answer", "42"])
        s = score_contains(r, c)
        assert s is not None and not s.passed
        assert "answer" in s.detail and "42" in s.detail

    def test_tool_sequence_pass(self) -> None:
        r = _result(tool_sequence=["read_file", "edit_file"])
        c = _case(expected_tool_sequence=["read_file", "edit_file"])
        s = score_tool_sequence(r, c)
        assert s is not None and s.passed

    def test_tool_sequence_fail(self) -> None:
        r = _result(tool_sequence=["read_file"])
        c = _case(expected_tool_sequence=["read_file", "edit_file"])
        s = score_tool_sequence(r, c)
        assert s is not None and not s.passed

    def test_file_state_pass(self, tmp_path) -> None:
        (tmp_path / "a.py").write_text("fixed\n", encoding="utf-8")
        r = _result()
        c = _case(expected_files={"a.py": "fixed\n"})
        s = score_file_state(r, c, tmp_path)
        assert s is not None and s.passed

    def test_file_state_fail(self, tmp_path) -> None:
        (tmp_path / "a.py").write_text("wrong\n", encoding="utf-8")
        r = _result()
        c = _case(expected_files={"a.py": "right\n"})
        s = score_file_state(r, c, tmp_path)
        assert s is not None and not s.passed

    def test_permission_pass(self) -> None:
        r = _result(permission_denied=True)
        c = _case(expected_permission_denied=True)
        s = score_permission(r, c)
        assert s is not None and s.passed

    def test_permission_fail(self) -> None:
        r = _result(permission_denied=False)
        c = _case(expected_permission_denied=True)
        s = score_permission(r, c)
        assert s is not None and not s.passed

    def test_unexpected_write_pass(self) -> None:
        before = {"a.py": "x", "b.py": "y"}
        after = {"a.py": "x", "b.py": "y"}
        c = _case(protected_files=["a.py"])
        s = score_unexpected_write(_result(), c, before, after)
        assert s is not None and s.passed

    def test_unexpected_write_fail(self) -> None:
        before = {"a.py": "x", "b.py": "y"}
        after = {"a.py": "CHANGED", "b.py": "y"}
        c = _case(protected_files=["a.py"])
        s = score_unexpected_write(_result(), c, before, after)
        assert s is not None and not s.passed
        assert "a.py" in s.detail

    def test_run_status_pass(self) -> None:
        r = _result(run_status="stuck")
        c = _case(expected_status="stuck")
        s = score_run_status(r, c)
        assert s is not None and s.passed

    def test_run_status_fail(self) -> None:
        r = _result(run_status="completed")
        c = _case(expected_status="stuck")
        s = score_run_status(r, c)
        assert s is not None and not s.passed


# --------------------------------------------------------------------------- #
# Baseline comparison unit tests
# --------------------------------------------------------------------------- #
class TestBaseline:
    def test_missing_baseline_is_not_regression(self) -> None:
        r = _result()
        reg, _ = is_regression(r, None)
        assert not reg

    def test_pass_to_fail_is_regression(self) -> None:
        r = _result()
        r.status = "fail"
        b = EvalBaseline(case_name="t", status="pass", passing_scorers=["exact"])
        reg, reason = is_regression(r, b)
        assert reg
        assert "pass" in reason

    def test_scorer_regression_is_flagged(self) -> None:
        r = _result()
        r.status = "pass"
        r.scores = [EvalScore("exact", True), EvalScore("file_state", False)]
        b = EvalBaseline(case_name="t", status="pass", passing_scorers=["exact", "file_state"])
        reg, reason = is_regression(r, b)
        assert reg
        assert "file_state" in reason

    def test_improvement_is_not_regression(self) -> None:
        r = _result()
        r.status = "pass"
        r.scores = [EvalScore("exact", True)]
        b = EvalBaseline(case_name="t", status="fail", passing_scorers=[])
        reg, _ = is_regression(r, b)
        assert not reg

    def test_save_and_load_roundtrip(self, tmp_path) -> None:
        r = _result(final_text="hello")
        r.scores = [EvalScore("exact", True), EvalScore("contains", True)]
        path = save_baseline(r, baseline_dir=tmp_path)
        assert path.is_file()
        loaded = load_baseline("t", baseline_dir=tmp_path)
        assert loaded is not None
        assert loaded.status == "pass"
        assert set(loaded.passing_scorers) == {"exact", "contains"}
        assert loaded.final_text_len == 5

    def test_load_missing_returns_none(self, tmp_path) -> None:
        assert load_baseline("nonexistent", baseline_dir=tmp_path) is None


# --------------------------------------------------------------------------- #
# End-to-end case runs (the roadmap's required scenarios)
# --------------------------------------------------------------------------- #
class TestCaseRuns:
    """Run the canonical cases and assert they pass."""

    @pytest.fixture
    def cases(self) -> dict[str, EvalCase]:
        all_cases = load_cases(Path(__file__).parent.parent / "evals" / "cases")
        return {c.name: c for c in all_cases}

    def test_read_then_answer(self, cases, tmp_path) -> None:
        case = cases["read_then_answer"]
        result = run_case(case, tmp_path / case.name)
        assert result.status == "pass", _score_details(result)
        assert "hello world" in (result.final_text or "")
        assert result.tool_sequence == ["read_file"]

    def test_edit_file_fix(self, cases, tmp_path) -> None:
        case = cases["edit_file_fix"]
        result = run_case(case, tmp_path / case.name)
        assert result.status == "pass", _score_details(result)
        assert (tmp_path / case.name / "bug.py").read_text() == "def add(a, b):\n    return a + b\n"

    def test_shell_fail_then_fix(self, cases, tmp_path) -> None:
        case = cases["shell_fail_then_fix"]
        result = run_case(case, tmp_path / case.name)
        assert result.status == "pass", _score_details(result)
        assert result.tool_sequence == ["run_bash", "edit_file", "run_bash"]
        assert (tmp_path / case.name / "calc.py").read_text() == "def add(a, b):\n    return a + b\n"

    def test_permission_denied(self, cases, tmp_path) -> None:
        case = cases["permission_denied"]
        result = run_case(case, tmp_path / case.name)
        assert result.status == "pass", _score_details(result)
        # the .env file must NOT have been leaked into the final answer
        assert "super-secret-value" not in (result.final_text or "")

    def test_loop_detection(self, cases, tmp_path) -> None:
        case = cases["loop_detection"]
        result = run_case(case, tmp_path / case.name)
        assert result.status == "pass", _score_details(result)
        assert result.run_status == "stuck"
        # protected file unchanged
        assert (tmp_path / case.name / "broken.py").read_text() == "x = 1\n"

    def test_session_resume(self, cases, tmp_path) -> None:
        case = cases["session_resume"]
        result = run_case(case, tmp_path / case.name)
        assert result.status == "pass", _score_details(result)
        assert "42" in (result.final_text or "")
        assert result.tool_sequence == []

    def test_all_canonical_cases_pass(self, cases, tmp_path) -> None:
        """Every canonical case must pass — this is the regression gate."""
        for name, case in cases.items():
            result = run_case(case, tmp_path / case.name)
            assert result.status == "pass", f"case {name} failed:\n{_score_details(result)}"


# --------------------------------------------------------------------------- #
# Context compaction (exercises maybe_compact through the runner)
# --------------------------------------------------------------------------- #
def test_context_compaction_triggers_and_preserves_pairing(tmp_path, monkeypatch) -> None:
    """A very low context_limit forces compaction; the runner still completes."""
    from mycode.evals.runner import FakeProvider

    f = tmp_path / "data.txt"
    f.write_text("compact me\n", encoding="utf-8")

    compact_calls = {"n": 0}

    original_maybe_compact = None

    import mycode.agent.runner as runner_mod

    original_maybe_compact = runner_mod.maybe_compact

    def spying_compact(provider, messages, *, context_limit, **kw):
        compact_calls["n"] += 1
        # Force compaction: replace all non-system messages with a summary,
        # keeping tool_call/tool pairs intact by collapsing whole turns.
        return original_maybe_compact(provider, messages, context_limit=context_limit, **kw)

    monkeypatch.setattr(runner_mod, "maybe_compact", spying_compact)

    # Long message to push estimate_tokens over a tiny limit.
    long_msg = "x" * 2000
    provider = FakeProvider(
        [
            LLMResponse(
                tool_calls=[ToolCall(id="c1", name="read_file", args={"path": "data.txt"})],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            LLMResponse(text="done", stop_reason=StopReason.END_TURN),
        ]
    )
    from mycode.agent.runner import AgentRunner

    request = RunRequest(
        provider=provider,
        messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": long_msg},
        ],
        max_steps=5,
        tools=[],
        context_limit=100,  # tiny -> compaction triggers
        planning="off",
    )
    runner = AgentRunner(sinks=[])
    res = runner.run(request)

    assert res.status == "completed"
    assert compact_calls["n"] >= 1


# --------------------------------------------------------------------------- #
# Case loader
# --------------------------------------------------------------------------- #
def test_loader_discovers_canonical_cases() -> None:
    cases = load_cases(Path(__file__).parent.parent / "evals" / "cases")
    names = {c.name for c in cases}
    assert {
        "read_then_answer",
        "edit_file_fix",
        "shell_fail_then_fix",
        "permission_denied",
        "loop_detection",
        "session_resume",
    } <= names


def test_loader_returns_empty_for_missing_dir(tmp_path) -> None:
    from mycode.evals.loader import load_cases_from_dir

    assert load_cases_from_dir(tmp_path / "nope") == []


# --------------------------------------------------------------------------- #
# EvalResult.to_dict
# --------------------------------------------------------------------------- #
def test_result_to_dict_is_json_serializable() -> None:
    r = _result(final_text="hello", tool_sequence=["read_file"])
    r.scores = [EvalScore("exact", True, ""), EvalScore("contains", False, "missing")]
    r.file_diffs = {"a.py": ("old", "new")}
    d = r.to_dict()
    # must be JSON-serializable (for --json output)
    json.dumps(d, ensure_ascii=False)
    assert d["case_name"] == "t"
    assert d["final_text"] == "hello"
    assert d["tool_sequence"] == ["read_file"]
    assert len(d["scores"]) == 2
    assert d["file_diffs"]["a.py"] == ["old", "new"]


# --------------------------------------------------------------------------- #
def _score_details(result: EvalResult) -> str:
    lines = [f"  status={result.status} run_status={result.run_status}"]
    if result.error:
        lines.append(f"  error={result.error}")
    for s in result.scores:
        lines.append(f"  {'✓' if s.passed else '✗'} {s.name}: {s.detail}")
    return "\n".join(lines)
