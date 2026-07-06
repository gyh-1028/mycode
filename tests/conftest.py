"""Pytest configuration: keep temporary directories inside the project root.

`PYTEST_DEBUG_TEMPROOT` tells pytest where to create its `run-<pid>`
temporary directories. By setting it to `<repo>/.pytmp` we avoid leaving
stale Windows pytest temp directories in the system temp folder, which can
suffer from broken ACLs after a few runs.
"""

import os
from pathlib import Path


def pytest_configure(config) -> None:  # noqa: ARG001 - pytest hook signature
    project_root = Path(__file__).parent.parent
    temp_root = project_root / ".pytmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    os.environ["PYTEST_DEBUG_TEMPROOT"] = str(temp_root)
    os.environ["MYCODE_MODELS_FILE"] = str(temp_root / f"models-{os.getpid()}.toml")
