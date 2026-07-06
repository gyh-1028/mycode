from __future__ import annotations

from pathlib import Path

from mycode.config import CodeIntelConfig, Config, ConfigLoadResult, ProviderConfig
from mycode.evals.fakes import FakeProvider
from mycode.evals.live_loader import load_live_suite
from mycode.evals.live_runner import compare_live_runs, run_live_suite
from mycode.llm.base import LLMResponse, StopReason, ToolCall, Usage


def test_live_suite_inventory() -> None:
    root = Path(__file__).parent.parent / "evals" / "live"
    safe = load_live_suite("safe-core-v1", root)
    host = load_live_suite("host-repair-v1", root)
    intel = load_live_suite("codeintel-v1", root)
    assert len(safe) == 20
    assert sum(case.language == "python" for case in safe) == 12
    assert sum(case.language == "typescript" for case in safe) == 4
    assert len(host) == 6 and all(case.safety == "host" for case in host)
    assert len(intel) == 12


def test_live_eval_runs_with_fake_provider_and_no_network(tmp_path: Path) -> None:
    root = Path(__file__).parent.parent / "evals" / "live"
    case = next(case for case in load_live_suite("safe-core-v1", root) if case.name == "py-add")
    config = Config(
        default_model="gpt-4o-mini",
        provider=ProviderConfig(api_key_env="UNUSED"),
        codeintel=CodeIntelConfig(enabled=False),
    )

    def factory(case, repetition):  # noqa: ANN001, ARG001
        return FakeProvider(
            [
                LLMResponse(
                    tool_calls=[ToolCall(id="r", name="read_file", args={"path": "calc.py"})],
                    stop_reason=StopReason.TOOL_CALLS,
                    usage=Usage(prompt_tokens=10),
                ),
                LLMResponse(
                    tool_calls=[
                        ToolCall(
                            id="e",
                            name="edit_file",
                            args={"path": "calc.py", "old_str": "return a - b", "new_str": "return a + b"},
                        )
                    ],
                    stop_reason=StopReason.TOOL_CALLS,
                    usage=Usage(prompt_tokens=10),
                ),
                LLMResponse(text="done", stop_reason=StopReason.END_TURN, usage=Usage(prompt_tokens=10, completion_tokens=2)),
            ]
        )

    run = run_live_suite(
        [case],
        ConfigLoadResult(config=config),
        output_root=tmp_path / "runs",
        provider_factory=factory,
    )
    assert run.success_rate == 1.0
    assert run.pass_at_1 == 1.0
    assert run.trials[0].tool_sequence == ["read_file", "edit_file"]
    assert (run.output_dir / "run.json").is_file()  # type: ignore[operator]


def test_live_host_suite_requires_explicit_flag() -> None:
    root = Path(__file__).parent.parent / "evals" / "live"
    case = load_live_suite("host-repair-v1", root)[0]
    config = Config(default_model="gpt-4o-mini", provider=ProviderConfig(api_key_env="UNUSED"))
    try:
        run_live_suite([case], ConfigLoadResult(config=config), provider_factory=lambda case, repetition: FakeProvider([]))
    except PermissionError as exc:
        assert "unsafe-allow-host-exec" in str(exc)
    else:
        raise AssertionError("host suite was not rejected")


def test_compare_live_runs_enforces_codeintel_gate() -> None:
    result = compare_live_runs(
        {"run_id": "base", "success_rate": 0.5, "prompt_tokens": 1000},
        {"run_id": "candidate", "success_rate": 0.7, "prompt_tokens": 1150},
    )
    assert result["meets_codeintel_gate"] is True
