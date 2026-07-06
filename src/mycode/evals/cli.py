"""``mycode eval`` CLI: run cases, compare baselines, output JSON.

Subcommands:

* ``mycode eval run`` — run all (or one) case(s), compare against baselines,
  exit non-zero on regression. ``--json`` emits machine-readable output.
* ``mycode eval list`` — list discovered case names.
* ``mycode eval update-baselines`` — write current results as baselines
  (requires confirmation; never auto-overwrites silently).
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

from mycode.evals.baseline import (
    is_regression,
    load_all_baselines,
    load_baseline,
    save_baseline,
)
from mycode.evals.loader import load_cases
from mycode.evals.runner import run_all, run_case
from mycode.evals.types import EvalResult


def handle_eval(args: list[str]) -> None:
    sub = args[0] if args else "run"
    if sub == "run":
        _handle_eval_run(args[1:])
        return
    if sub == "list":
        _handle_eval_list()
        return
    if sub in {"update-baselines", "update"}:
        _handle_eval_update(args[1:])
        return
    if sub == "live":
        _handle_live(args[1:])
        return
    if sub == "compare":
        _handle_compare(args[1:])
        return
    if sub == "report":
        _handle_report(args[1:])
        return
    if sub == "baseline":
        _handle_live_baseline(args[1:])
        return
    print("未知 eval 命令。可用: run / list / update-baselines", file=sys.stderr)
    raise SystemExit(1)


def _handle_eval_list() -> None:
    cases = load_cases()
    if not cases:
        print("(未发现 eval case。请在 evals/cases/ 下添加 .py 文件,定义 CASES 列表。)")
        return
    print(f"发现 {len(cases)} 个 eval case:")
    for case in cases:
        desc = f" - {case.description}" if case.description else ""
        print(f"  {case.name}{desc}")


def _handle_eval_run(args: list[str]) -> None:
    case_filter = _parse_option(args, "--case")
    json_output = _has_flag(args, "--json")
    cases = load_cases()
    if not cases:
        print("未发现 eval case。", file=sys.stderr)
        raise SystemExit(1)
    if case_filter:
        cases = [c for c in cases if c.name == case_filter]
        if not cases:
            print(f"找不到名为 {case_filter!r} 的 case。", file=sys.stderr)
            raise SystemExit(1)

    baselines = load_all_baselines()
    results: list[EvalResult] = []
    with tempfile.TemporaryDirectory(prefix="mycode-eval-") as tmp:
        tmp_path = Path(tmp)
        if len(cases) == 1:
            results.append(run_case(cases[0], tmp_path / cases[0].name))
        else:
            results = run_all(cases, tmp_path)

    regressions: list[tuple[str, str]] = []
    for res in results:
        baseline = baselines.get(res.case_name) or load_baseline(res.case_name)
        reg, reason = is_regression(res, baseline)
        if reg:
            regressions.append((res.case_name, reason))

    if json_output:
        payload = {
            "results": [r.to_dict() for r in results],
            "regressions": [
                {"case": name, "reason": reason} for name, reason in regressions
            ],
            "summary": {
                "total": len(results),
                "passed": sum(1 for r in results if r.passed),
                "failed": sum(1 for r in results if not r.passed),
                "regressions": len(regressions),
            },
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for res in results:
            mark = "✓" if res.passed else "✗"
            print(f"{mark} {res.case_name} [{res.run_status}]")
            for score in res.scores:
                sm = "✓" if score.passed else "✗"
                print(f"    {sm} {score.name}: {score.detail}")
            if res.error:
                print(f"    ERROR: {res.error}")
        print()
        print(f"总计 {len(results)} 个 case,{sum(1 for r in results if r.passed)} 通过,"
              f"{sum(1 for r in results if not r.passed)} 失败。")
        if regressions:
            print(f"\n回归告警 ({len(regressions)} 个):")
            for name, reason in regressions:
                print(f"  · {name}: {reason}")

    if regressions:
        raise SystemExit(1)


def _handle_eval_update(args: list[str]) -> None:
    case_filter = _parse_option(args, "--case")
    force = _has_flag(args, "--force")
    if not force:
        print("即将用当前结果覆盖 evals/baselines/ 下的基线文件。")
        print("确认请加 --force。基线不会被自动覆盖。")
        raise SystemExit(1)

    cases = load_cases()
    if case_filter:
        cases = [c for c in cases if c.name == case_filter]
    if not cases:
        print("未发现 eval case。", file=sys.stderr)
        raise SystemExit(1)

    with tempfile.TemporaryDirectory(prefix="mycode-eval-") as tmp:
        tmp_path = Path(tmp)
        for case in cases:
            result = run_case(case, tmp_path / case.name)
            path = save_baseline(result)
            print(f"已写入基线: {path} [{result.status}]")


def _handle_live(args: list[str]) -> None:
    from mycode.config import ConfigError, load_config_result
    from mycode.evals.live_loader import load_live_suite
    from mycode.evals.live_runner import run_live_suite

    suite = _parse_option(args, "--suite") or "safe-core-v1"
    repeat = int(_parse_option(args, "--repeat") or "1")
    budget = float(_parse_option(args, "--budget") or "1.0")
    context_raw = (_parse_option(args, "--auto-context") or "config").lower()
    auto_context = None if context_raw == "config" else context_raw in {"1", "true", "yes", "on"}
    try:
        config_result = load_config_result()
        run = run_live_suite(
            load_live_suite(suite),
            config_result,
            repeat=repeat,
            budget_usd=budget,
            allow_unknown_pricing=_has_flag(args, "--allow-unknown-pricing"),
            allow_host_exec=_has_flag(args, "--unsafe-allow-host-exec"),
            auto_context=auto_context,
        )
    except (ConfigError, FileNotFoundError, PermissionError, ValueError) as exc:
        print(f"live eval 启动失败: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    _print_live_summary(run.to_dict(), json_output=_has_flag(args, "--json"))


def _handle_compare(args: list[str]) -> None:
    from mycode.evals.live_runner import compare_live_runs, load_live_run

    if len(args) < 2:
        print("用法: mycode eval compare <baseline-run> <candidate-run>", file=sys.stderr)
        raise SystemExit(1)
    try:
        result = compare_live_runs(load_live_run(args[0]), load_live_run(args[1]))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"读取 Eval 结果失败: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _handle_report(args: list[str]) -> None:
    from mycode.evals.live_runner import load_live_run

    if not args:
        print("用法: mycode eval report <run-id>", file=sys.stderr)
        raise SystemExit(1)
    try:
        payload = load_live_run(args[0])
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"读取 Eval 结果失败: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    _print_live_summary(payload, json_output=_has_flag(args, "--json"))


def _handle_live_baseline(args: list[str]) -> None:
    from mycode.evals.live_runner import load_live_run

    if len(args) < 2 or args[0] != "update":
        print("用法: mycode eval baseline update <run-id> --force", file=sys.stderr)
        raise SystemExit(1)
    if "--force" not in args:
        print("确认更新真实模型基线请加 --force。", file=sys.stderr)
        raise SystemExit(1)
    payload = load_live_run(args[1])
    suite = str(payload.get("suite", "live"))
    target = Path("evals/baselines") / f"live-{suite}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    source = Path(".mycode/evals/runs") / args[1] / "summary.json"
    if source.is_file():
        shutil.copyfile(source, target)
    else:
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已更新真实模型基线: {target}")


def _print_live_summary(payload: dict[str, object], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(f"run: {payload.get('run_id')}")
    print(f"suite: {payload.get('suite')}")
    print(f"trials: {payload.get('passed', 0)}/{payload.get('trials', 0)}")
    print(f"success_rate: {float(cast(Any, payload.get('success_rate', 0.0))):.1%}")
    print(f"pass@1: {float(cast(Any, payload.get('pass_at_1', 0.0))):.1%}")
    print(f"pass@3: {float(cast(Any, payload.get('pass_at_3', 0.0))):.1%}")
    cost = payload.get("total_cost_usd")
    print(f"cost_usd: {'unknown' if cost is None else f'{float(cast(Any, cost)):.6f}'}")


def _parse_option(args: list[str], name: str) -> str | None:
    prefix = name + "="
    for idx, arg in enumerate(args):
        if arg == name:
            return args[idx + 1] if idx + 1 < len(args) else None
        if arg.startswith(prefix):
            return arg.split("=", 1)[1]
    return None


def _has_flag(args: list[str], name: str) -> bool:
    return name in args


__all__ = ["handle_eval"]
