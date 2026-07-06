import tomllib
from pathlib import Path

import mycode

ROOT = Path(__file__).parents[1]


def test_release_metadata_is_consistent() -> None:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    assert project["name"] == "mycode-ai-cli"
    assert project["version"] == mycode.__version__ == "0.2.2"
    assert project["requires-python"] == ">=3.11"
    assert project["license"] == "Apache-2.0"
    assert {"mcp", "tui", "trace", "all"} <= set(project["optional-dependencies"])


def test_release_artifacts_are_declared() -> None:
    required = [
        "LICENSE",
        "NOTICE",
        "CHANGELOG.md",
        ".github/workflows/ci.yml",
        ".github/workflows/release.yml",
        "editors/vscode/package.json",
        "schemas/protocol-v1.json",
    ]
    assert all((ROOT / path).is_file() for path in required)
