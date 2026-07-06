"""Incremental local SQLite symbol index."""

from __future__ import annotations

import ast
import hashlib
import os
import shutil
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from mycode.codeintel.models import CodeLocation, Symbol, language_for_path
from mycode.permissions import is_sensitive_path

INDEX_RELATIVE_PATH = Path(".mycode/index/codeintel-v1.sqlite3")
_IGNORED_DIRS = {
    ".git",
    ".mycode",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "build",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".pytmp",
}
_INDEXED_SUFFIXES = {
    ".py",
    ".pyi",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".toml",
    ".json",
    ".yaml",
    ".yml",
    ".md",
    ".rst",
    ".txt",
}
_MAX_FILE_BYTES = 2_000_000


def _is_ignored_dir(name: str) -> bool:
    return name in _IGNORED_DIRS or name.startswith((".pytest_tmp", "pytest-"))


@dataclass(frozen=True)
class IndexBuildResult:
    discovered: int
    indexed: int
    unchanged: int
    removed: int
    errors: tuple[str, ...]


@dataclass(frozen=True)
class _PreparedFile:
    path: str
    language: str | None = None
    mtime_ns: int = 0
    content_hash: str | None = None
    size: int = 0
    symbols: tuple[Symbol, ...] = ()
    dependencies: tuple[str, ...] = ()
    unchanged: bool = False
    error: str | None = None


class SymbolIndex:
    """A project-scoped, content-hash incremental symbol index."""

    def __init__(self, root: Path, db_path: Path | None = None) -> None:
        self.root = root.resolve()
        self.db_path = (db_path or self.root / INDEX_RELATIVE_PATH).resolve()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                language TEXT NOT NULL,
                mtime_ns INTEGER NOT NULL,
                content_hash TEXT NOT NULL,
                size INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS symbols (
                id INTEGER PRIMARY KEY,
                path TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                container TEXT,
                signature TEXT,
                start_line INTEGER NOT NULL,
                start_column INTEGER NOT NULL,
                end_line INTEGER,
                end_column INTEGER,
                source TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS symbols_name_idx ON symbols(name);
            CREATE INDEX IF NOT EXISTS symbols_path_idx ON symbols(path);
            CREATE TABLE IF NOT EXISTS dependencies (
                source_path TEXT NOT NULL,
                target TEXT NOT NULL,
                kind TEXT NOT NULL,
                UNIQUE(source_path, target, kind)
            );
            """
        )
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def discover_files(self) -> list[Path]:
        git = shutil.which("git")
        if git and (self.root / ".git").exists():
            try:
                proc = subprocess.run(
                    [git, "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                    cwd=self.root,
                    capture_output=True,
                    timeout=20,
                    check=True,
                )
                paths = [self.root / item.decode("utf-8", "replace") for item in proc.stdout.split(b"\0") if item]
                return sorted(path for path in paths if self._is_indexable(path))
            except (OSError, subprocess.SubprocessError):
                pass

        result: list[Path] = []
        for current, dirs, files in os.walk(self.root):
            dirs[:] = [name for name in dirs if not _is_ignored_dir(name)]
            base = Path(current)
            for name in files:
                path = base / name
                if self._is_indexable(path):
                    result.append(path)
        return sorted(result)

    def _is_indexable(self, path: Path) -> bool:
        try:
            rel = path.relative_to(self.root)
        except ValueError:
            return False
        return (
            path.suffix.lower() in _INDEXED_SUFFIXES
            and not is_sensitive_path(rel)
            and not any(_is_ignored_dir(part) for part in rel.parts[:-1])
        )

    def build(self) -> IndexBuildResult:
        paths = self.discover_files()
        seen: set[str] = set()
        indexed = unchanged = 0
        errors: list[str] = []
        with self._connect() as conn:
            existing_metadata = {
                str(row["path"]): (
                    str(row["content_hash"]),
                    int(row["mtime_ns"]),
                    int(row["size"]),
                )
                for row in conn.execute("SELECT path, content_hash, mtime_ns, size FROM files")
            }
            workers = min(16, max(4, (os.cpu_count() or 4) * 2))
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mycode-index") as pool:
                prepared_files = pool.map(
                    lambda path: self._prepare_file(path, existing_metadata),
                    paths,
                    chunksize=64,
                )
                prepared_files = list(prepared_files)

            for prepared in prepared_files:
                rel = prepared.path
                seen.add(rel)
                if prepared.error:
                    errors.append(prepared.error)
                if prepared.unchanged:
                    unchanged += 1
                    continue
                if prepared.content_hash is None or prepared.language is None:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO files(path, language, mtime_ns, content_hash, size) VALUES(?,?,?,?,?)",
                    (rel, prepared.language, prepared.mtime_ns, prepared.content_hash, prepared.size),
                )
                conn.execute("DELETE FROM symbols WHERE path=?", (rel,))
                conn.execute("DELETE FROM dependencies WHERE source_path=?", (rel,))
                self._insert_symbols(conn, list(prepared.symbols))
                conn.executemany(
                    "INSERT OR IGNORE INTO dependencies(source_path, target, kind) VALUES(?,?,?)",
                    [(rel, target, "import") for target in prepared.dependencies],
                )
                indexed += 1

            existing = {row[0] for row in conn.execute("SELECT path FROM files")}
            removed_paths = existing - seen
            for rel in removed_paths:
                conn.execute("DELETE FROM files WHERE path=?", (rel,))
                conn.execute("DELETE FROM symbols WHERE path=?", (rel,))
                conn.execute("DELETE FROM dependencies WHERE source_path=?", (rel,))
        return IndexBuildResult(len(paths), indexed, unchanged, len(removed_paths), tuple(errors))

    def _prepare_file(
        self,
        path: Path,
        existing_metadata: dict[str, tuple[str, int, int]],
    ) -> _PreparedFile:
        rel = path.relative_to(self.root).as_posix()
        try:
            if path.is_symlink():
                path.resolve().relative_to(self.root)
            stat = path.stat()
            if stat.st_size > _MAX_FILE_BYTES:
                return _PreparedFile(path=rel)
            previous = existing_metadata.get(rel)
            if previous is not None and previous[1:] == (stat.st_mtime_ns, stat.st_size):
                return _PreparedFile(path=rel, unchanged=True)
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if previous is not None and previous[0] == digest:
                return _PreparedFile(path=rel, unchanged=True)
            language = language_for_path(path)
            symbols: list[Symbol] = []
            dependencies: set[str] = set()
            error = None
            if language == "python":
                try:
                    symbols, dependencies = _parse_python(rel, raw.decode("utf-8", "replace"))
                except SyntaxError as exc:
                    error = f"{rel}: SyntaxError: {exc}"
            return _PreparedFile(
                path=rel,
                language=language,
                mtime_ns=stat.st_mtime_ns,
                content_hash=digest,
                size=stat.st_size,
                symbols=tuple(symbols),
                dependencies=tuple(sorted(dependencies)),
                error=error,
            )
        except (OSError, ValueError) as exc:
            return _PreparedFile(path=rel, error=f"{rel}: {type(exc).__name__}: {exc}")

    @staticmethod
    def _insert_symbols(conn: sqlite3.Connection, symbols: list[Symbol]) -> None:
        conn.executemany(
            """INSERT INTO symbols(
                path,name,kind,container,signature,start_line,start_column,end_line,end_column,source
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    symbol.location.path,
                    symbol.name,
                    symbol.kind,
                    symbol.container,
                    symbol.signature,
                    symbol.location.start_line,
                    symbol.location.start_column,
                    symbol.location.end_line,
                    symbol.location.end_column,
                    symbol.source,
                )
                for symbol in symbols
            ],
        )

    def replace_lsp_symbols(self, path: str, symbols: list[Symbol]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM symbols WHERE path=? AND source='lsp'", (path,))
            self._insert_symbols(conn, symbols)

    def search_symbols(self, query: str, *, kind: str | None = None, path: str | None = None, limit: int = 20) -> list[Symbol]:
        where = ["lower(name) LIKE ?"]
        params: list[object] = [f"%{query.lower()}%"]
        if kind:
            where.append("kind=?")
            params.append(kind)
        if path:
            where.append("path LIKE ?")
            params.append(f"{path.rstrip('/')}%")
        params.extend([query.lower(), f"{query.lower()}%", max(1, min(limit, 200))])
        sql = f"""SELECT * FROM symbols WHERE {' AND '.join(where)}
            ORDER BY CASE WHEN lower(name)=? THEN 0 WHEN lower(name) LIKE ? THEN 1 ELSE 2 END, path, start_line
            LIMIT ?"""
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_symbol(row) for row in rows]

    def symbols_for_file(self, path: str, limit: int = 100) -> list[Symbol]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM symbols WHERE path=? ORDER BY start_line LIMIT ?", (path, limit)
            ).fetchall()
        return [_row_to_symbol(row) for row in rows]

    def dependencies_for(self, path: str) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT target FROM dependencies WHERE source_path=? ORDER BY target", (path,)
            ).fetchall()
        return [str(row[0]) for row in rows]

    def files_matching(self, terms: list[str], limit: int = 50) -> list[str]:
        if not terms:
            return []
        with self._connect() as conn:
            rows = conn.execute("SELECT path FROM files ORDER BY path").fetchall()
        scored: list[tuple[int, str]] = []
        for row in rows:
            path = str(row[0])
            lower = path.lower()
            score = sum(3 if term == Path(path).stem.lower() else 1 for term in terms if term in lower)
            if score:
                scored.append((score, path))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [path for _, path in scored[:limit]]

    def status(self) -> dict[str, int | str]:
        if not self.db_path.exists():
            return {"path": str(self.db_path), "files": 0, "symbols": 0, "dependencies": 0}
        with self._connect() as conn:
            return {
                "path": str(self.db_path),
                "files": int(conn.execute("SELECT count(*) FROM files").fetchone()[0]),
                "symbols": int(conn.execute("SELECT count(*) FROM symbols").fetchone()[0]),
                "dependencies": int(conn.execute("SELECT count(*) FROM dependencies").fetchone()[0]),
            }

    def clear(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            (Path(str(self.db_path) + suffix)).unlink(missing_ok=True)


class _PythonVisitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.symbols: list[Symbol] = []
        self.dependencies: set[str] = set()
        self._containers: list[str] = []

    def _add(self, node: ast.AST, name: str, kind: str, signature: str | None = None) -> None:
        self.symbols.append(
            Symbol(
                name=name,
                kind=kind,
                container=".".join(self._containers) or None,
                signature=signature,
                location=CodeLocation(
                    path=self.path,
                    start_line=int(getattr(node, "lineno", 1)),
                    start_column=int(getattr(node, "col_offset", 0)),
                    end_line=getattr(node, "end_lineno", None),
                    end_column=getattr(node, "end_col_offset", None),
                ),
            )
        )

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        bases = ", ".join(ast.unparse(base) for base in node.bases)
        self._add(node, node.name, "class", f"class {node.name}({bases})" if bases else f"class {node.name}")
        self._containers.append(node.name)
        self.generic_visit(node)
        self._containers.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        kind = "method" if self._containers else "function"
        self._add(node, node.name, kind, f"def {node.name}{ast.unparse(node.args)}")
        self._containers.append(node.name)
        self.generic_visit(node)
        self._containers.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # type: ignore[name-defined]
        self.visit_FunctionDef(node)  # type: ignore[arg-type]

    def visit_Import(self, node: ast.Import) -> None:
        self.dependencies.update(alias.name for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.dependencies.add(node.module)


def _parse_python(path: str, text: str) -> tuple[list[Symbol], set[str]]:
    visitor = _PythonVisitor(path)
    visitor.visit(ast.parse(text, filename=path))
    return visitor.symbols, visitor.dependencies


def _row_to_symbol(row: sqlite3.Row) -> Symbol:
    return Symbol(
        name=str(row["name"]),
        kind=str(row["kind"]),
        container=row["container"],
        signature=row["signature"],
        source=str(row["source"]),
        location=CodeLocation(
            path=str(row["path"]),
            start_line=int(row["start_line"]),
            start_column=int(row["start_column"]),
            end_line=row["end_line"],
            end_column=row["end_column"],
        ),
    )


__all__ = ["INDEX_RELATIVE_PATH", "IndexBuildResult", "SymbolIndex"]
