"""Reproducible cold and incremental code-index benchmark."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
from pathlib import Path

from mycode.codeintel.index import SymbolIndex


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--files", type=int, default=10_000)
    parser.add_argument("--changes", type=int, default=100)
    parser.add_argument("--git", action="store_true")
    args = parser.parse_args()
    if args.files < 1 or args.changes < 0 or args.changes > args.files:
        parser.error("require files >= 1 and 0 <= changes <= files")

    temp_root = Path(".pytmp")
    temp_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="codeintel-bench-", dir=temp_root) as raw:
        root = Path(raw).resolve()
        for index in range(args.files):
            (root / f"module_{index:05d}.py").write_text(
                f"def function_{index}(value: int) -> int:\n    return value + {index}\n",
                encoding="utf-8",
            )
        if args.git:
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=MyCode Benchmark",
                    "-c",
                    "user.email=benchmark@example.com",
                    "commit",
                    "-qm",
                    "benchmark fixture",
                ],
                cwd=root,
                check=True,
            )

        symbol_index = SymbolIndex(root)
        started = time.perf_counter()
        cold = symbol_index.build()
        cold_seconds = time.perf_counter() - started

        changed: list[str] = []
        for index in range(args.changes):
            path = root / f"module_{index:05d}.py"
            path.write_text(
                f"def function_{index}(value: int) -> int:\n    return value - {index}\n",
                encoding="utf-8",
            )
            changed.append(path.name)
        started = time.perf_counter()
        incremental = symbol_index.update_paths(changed)
        incremental_seconds = time.perf_counter() - started

        print(
            json.dumps(
                {
                    "files": args.files,
                    "changes": args.changes,
                    "cold_seconds": round(cold_seconds, 3),
                    "incremental_seconds": round(incremental_seconds, 3),
                    "cold_indexed": cold.indexed,
                    "incremental_indexed": incremental.indexed,
                    "errors": [*cold.errors, *incremental.errors],
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
