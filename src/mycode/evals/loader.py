"""Case discovery: load eval cases from ``evals/cases/*.py``.

Each case file defines a module-level ``CASES: list[EvalCase]``. The loader
imports files by path (so they live outside the ``mycode`` package) and collects
all case lists. Files that fail to import are skipped with a warning, so one
broken case file never blocks the whole suite.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any

from mycode.evals.types import EvalCase

_LOGGER = logging.getLogger("mycode.evals.loader")

DEFAULT_CASES_DIR = Path("evals") / "cases"


def _load_module(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_cases_from_dir(cases_dir: Path) -> list[EvalCase]:
    """Discover and load every ``CASES`` list from Python files in cases_dir."""
    if not cases_dir.is_dir():
        return []
    cases: list[EvalCase] = []
    for path in sorted(cases_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            module = _load_module(path)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("跳过无法加载的 case 文件 %s: %s", path, exc)
            continue
        file_cases = getattr(module, "CASES", None)
        if file_cases is None:
            continue
        for case in file_cases:
            if isinstance(case, EvalCase):
                cases.append(case)
    return cases


def load_cases(cases_dir: Path | None = None) -> list[EvalCase]:
    """Load cases from the given dir (default: evals/cases/)."""
    directory = cases_dir or DEFAULT_CASES_DIR
    return load_cases_from_dir(Path(directory))


__all__ = ["DEFAULT_CASES_DIR", "load_cases", "load_cases_from_dir"]
