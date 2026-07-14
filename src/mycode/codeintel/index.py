"""Incremental local SQLite symbol index."""

from __future__ import annotations

import ast
import hashlib
import os
import shutil
import sqlite3
import stat as stat_module
import subprocess
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
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
_GIT_BLOB_MIN_FILES = 2_000


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
            PRAGMA synchronous=NORMAL;
            PRAGMA temp_store=MEMORY;
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
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

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
        changed_paths = self._git_incremental_paths()
        if changed_paths is not None:
            return self.update_paths(changed_paths)
        paths = self.discover_files()
        seen: set[str] = set()
        indexed = unchanged = 0
        errors: list[str] = []
        with self._connection() as conn:
            existing_metadata = {
                str(row["path"]): (
                    str(row["content_hash"]),
                    int(row["mtime_ns"]),
                    int(row["size"]),
                )
                for row in conn.execute("SELECT path, content_hash, mtime_ns, size FROM files")
            }
            prepared_files = self._prepare_paths(paths, existing_metadata)

            changed: list[_PreparedFile] = []
            invalid_paths: set[str] = set()
            for prepared in prepared_files:
                rel = prepared.path
                seen.add(rel)
                if prepared.error:
                    errors.append(prepared.error)
                if prepared.unchanged:
                    unchanged += 1
                    continue
                if prepared.content_hash is None or prepared.language is None:
                    invalid_paths.add(rel)
                    continue
                changed.append(prepared)

            removed_paths = (set(existing_metadata) - seen) | invalid_paths
            removed_count = len(removed_paths & set(existing_metadata))
            self._delete_file_rows_batch(conn, removed_paths)
            self._replace_prepared_batch(conn, changed)
            indexed = len(changed)
            head = self._git_head()
            if head is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO metadata(key, value) VALUES('git_head', ?)",
                    (head,),
                )
        return IndexBuildResult(len(paths), indexed, unchanged, removed_count, tuple(errors))

    def update_paths(self, paths: Sequence[str | Path]) -> IndexBuildResult:
        """Update known changed paths without rediscovering the whole repository."""

        candidates: dict[str, Path | None] = {}
        errors: list[str] = []
        for value in paths:
            raw = Path(value)
            logical = raw if raw.is_absolute() else self.root / raw
            try:
                relative = logical.relative_to(self.root).as_posix()
                logical.resolve().relative_to(self.root)
            except ValueError:
                errors.append(f"{value}: path is outside the project root")
                continue
            candidates[relative] = (
                logical
                if logical.is_file() and self._is_indexable(logical)
                else None
            )

        if not candidates:
            with self._connection() as conn:
                total = int(conn.execute("SELECT count(*) FROM files").fetchone()[0])
            return IndexBuildResult(total, 0, total, 0, tuple(errors))

        with self._connection() as conn:
            placeholders = ",".join("?" for _ in candidates)
            rows = conn.execute(
                f"SELECT path, content_hash, mtime_ns, size FROM files WHERE path IN ({placeholders})",
                tuple(candidates),
            ).fetchall()
            existing_metadata = {
                str(row["path"]): (
                    str(row["content_hash"]),
                    int(row["mtime_ns"]),
                    int(row["size"]),
                )
                for row in rows
            }
            removed_candidates: set[str] = set()
            for relative, candidate in candidates.items():
                if candidate is not None:
                    continue
                removed_candidates.add(relative)

            files = [candidate for candidate in candidates.values() if candidate is not None]
            resolved_parents = self._resolved_parents(files)
            workers = min(32, max(1, len(files)))
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mycode-index") as pool:
                prepared_files = list(
                    pool.map(
                        lambda path: self._prepare_file(
                            path,
                            existing_metadata,
                            resolved_parents.get(path.parent),
                        ),
                        files,
                    )
                )

            changed: list[_PreparedFile] = []
            invalid_paths: set[str] = set()
            unchanged = 0
            for prepared in prepared_files:
                if prepared.error:
                    errors.append(prepared.error)
                if prepared.unchanged:
                    unchanged += 1
                    continue
                if prepared.content_hash is None or prepared.language is None:
                    invalid_paths.add(prepared.path)
                    continue
                changed.append(prepared)
            deleted_paths = removed_candidates | invalid_paths
            self._delete_file_rows_batch(conn, deleted_paths)
            self._replace_prepared_batch(conn, changed)
            removed = len(deleted_paths & set(existing_metadata))
            indexed = len(changed)
        return IndexBuildResult(len(candidates), indexed, unchanged, removed, tuple(errors))

    def _git_incremental_paths(self) -> list[str] | None:
        if not self.db_path.exists():
            return None
        head = self._git_head()
        if head is None:
            return None
        with self._connection() as conn:
            row = conn.execute("SELECT value FROM metadata WHERE key='git_head'").fetchone()
            indexed_paths = {str(item[0]) for item in conn.execute("SELECT path FROM files")}
        if row is None or str(row[0]) != head:
            return None
        git = shutil.which("git")
        if git is None:
            return None
        try:
            changed = subprocess.run(
                [git, "diff", "--no-renames", "--name-only", "-z", "HEAD"],
                cwd=self.root,
                capture_output=True,
                timeout=20,
                check=True,
            ).stdout
            cached = subprocess.run(
                [git, "ls-files", "--cached", "-z"],
                cwd=self.root,
                capture_output=True,
                timeout=20,
                check=True,
            ).stdout
            untracked = subprocess.run(
                [git, "ls-files", "--others", "--exclude-standard", "-z"],
                cwd=self.root,
                capture_output=True,
                timeout=20,
                check=True,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        untracked_paths = [
            item.decode("utf-8", "replace")
            for item in untracked.split(b"\0")
            if item
        ]
        current_paths = {
            item.decode("utf-8", "replace")
            for item in cached.split(b"\0")
            if item
        } | set(untracked_paths)
        changed_paths = [
            item.decode("utf-8", "replace")
            for item in changed.split(b"\0")
            if item
        ]
        return list(
            dict.fromkeys(
                [
                    *changed_paths,
                    *untracked_paths,
                    *(current_paths - indexed_paths),
                    *(indexed_paths - current_paths),
                ]
            )
        )

    def _git_head(self) -> str | None:
        git = shutil.which("git")
        if git is None or not (self.root / ".git").exists():
            return None
        try:
            result = subprocess.run(
                [git, "rev-parse", "HEAD"],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout.strip() or None

    @staticmethod
    def _delete_file_rows_batch(conn: sqlite3.Connection, paths: set[str] | list[str]) -> None:
        values = list(paths)
        for start in range(0, len(values), 500):
            chunk = values[start : start + 500]
            placeholders = ",".join("?" for _ in chunk)
            conn.execute(
                f"DELETE FROM dependencies WHERE source_path IN ({placeholders})",
                chunk,
            )
            conn.execute(f"DELETE FROM symbols WHERE path IN ({placeholders})", chunk)
            conn.execute(f"DELETE FROM files WHERE path IN ({placeholders})", chunk)

    def _replace_prepared_batch(
        self,
        conn: sqlite3.Connection,
        prepared_files: list[_PreparedFile],
    ) -> None:
        if not prepared_files:
            return
        self._delete_file_rows_batch(conn, [prepared.path for prepared in prepared_files])
        conn.executemany(
            "INSERT OR REPLACE INTO files(path, language, mtime_ns, content_hash, size) VALUES(?,?,?,?,?)",
            [
                (
                    prepared.path,
                    prepared.language,
                    prepared.mtime_ns,
                    prepared.content_hash,
                    prepared.size,
                )
                for prepared in prepared_files
            ],
        )
        self._insert_symbols(
            conn,
            [symbol for prepared in prepared_files for symbol in prepared.symbols],
        )
        conn.executemany(
            "INSERT OR IGNORE INTO dependencies(source_path, target, kind) VALUES(?,?,?)",
            [
                (prepared.path, target, "import")
                for prepared in prepared_files
                for target in prepared.dependencies
            ],
        )

    def _prepare_file(
        self,
        path: Path,
        existing_metadata: dict[str, tuple[str, int, int]],
        resolved_parent: Path | None = None,
    ) -> _PreparedFile:
        rel = path.relative_to(self.root).as_posix()
        try:
            resolved = resolved_parent / path.name if resolved_parent is not None else path.resolve()
            if resolved_parent is None:
                resolved.relative_to(self.root)
            file_stat = resolved.lstat()
            if stat_module.S_ISLNK(file_stat.st_mode):
                resolved = resolved.resolve()
                resolved.relative_to(self.root)
                file_stat = resolved.stat()
            if file_stat.st_size > _MAX_FILE_BYTES:
                return _PreparedFile(path=rel)
            previous = existing_metadata.get(rel)
            if previous is not None and previous[1:] == (file_stat.st_mtime_ns, file_stat.st_size):
                return _PreparedFile(path=rel, unchanged=True)
            with resolved.open("rb") as handle:
                raw = handle.read()
            return self._prepare_bytes(
                path,
                raw,
                mtime_ns=file_stat.st_mtime_ns,
                existing_metadata=existing_metadata,
            )
        except (OSError, ValueError) as exc:
            return _PreparedFile(path=rel, error=f"{rel}: {type(exc).__name__}: {exc}")

    def _prepare_paths(
        self,
        paths: list[Path],
        existing_metadata: dict[str, tuple[str, int, int]],
    ) -> list[_PreparedFile]:
        git_prepared, filesystem_paths = self._prepare_git_blobs(paths, existing_metadata)
        if not filesystem_paths:
            return git_prepared
        resolved_parents = self._resolved_parents(filesystem_paths)
        workers = min(32, max(8, (os.cpu_count() or 4) * 4))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mycode-index") as pool:
            filesystem_prepared = list(
                pool.map(
                    lambda path: self._prepare_file(
                        path,
                        existing_metadata,
                        resolved_parents.get(path.parent),
                    ),
                    filesystem_paths,
                    chunksize=64,
                )
            )
        return [*git_prepared, *filesystem_prepared]

    def _prepare_git_blobs(
        self,
        paths: list[Path],
        existing_metadata: dict[str, tuple[str, int, int]],
    ) -> tuple[list[_PreparedFile], list[Path]]:
        git = shutil.which("git")
        if git is None or len(paths) < _GIT_BLOB_MIN_FILES or not (self.root / ".git").exists():
            return [], paths
        try:
            staged_raw = subprocess.run(
                [git, "ls-files", "--stage", "-z"],
                cwd=self.root,
                capture_output=True,
                timeout=30,
                check=True,
            ).stdout
            changed_raw = subprocess.run(
                [git, "diff", "--no-renames", "--name-only", "-z", "HEAD"],
                cwd=self.root,
                capture_output=True,
                timeout=30,
                check=True,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return [], paths

        staged: dict[str, tuple[str, str]] = {}
        for entry in staged_raw.split(b"\0"):
            if not entry or b"\t" not in entry:
                continue
            metadata, path_raw = entry.split(b"\t", 1)
            parts = metadata.split()
            if len(parts) != 3 or parts[2] != b"0":
                continue
            staged[path_raw.decode("utf-8", "replace")] = (
                parts[0].decode("ascii", "replace"),
                parts[1].decode("ascii", "replace"),
            )
        changed = {
            item.decode("utf-8", "replace")
            for item in changed_raw.split(b"\0")
            if item
        }
        requested = {path.relative_to(self.root).as_posix(): path for path in paths}
        clean = {
            path: value
            for path, value in staged.items()
            if path in requested and path not in changed and value[0] != "120000"
        }
        if not clean:
            return [], paths
        blobs = self._read_git_blobs(git, {object_id for _, object_id in clean.values()})
        prepared: list[_PreparedFile] = []
        consumed: set[str] = set()
        for path, (_, object_id) in clean.items():
            raw = blobs.get(object_id)
            if raw is None:
                continue
            prepared.append(
                self._prepare_bytes(
                    requested[path],
                    raw,
                    mtime_ns=0,
                    existing_metadata=existing_metadata,
                )
            )
            consumed.add(path)
        return prepared, [path for path in paths if path.relative_to(self.root).as_posix() not in consumed]

    def _read_git_blobs(self, git: str, object_ids: set[str]) -> dict[str, bytes]:
        if not object_ids:
            return {}
        ordered = sorted(object_ids)
        try:
            output = subprocess.run(
                [git, "cat-file", "--batch"],
                cwd=self.root,
                input=("\n".join(ordered) + "\n").encode("ascii"),
                capture_output=True,
                timeout=60,
                check=True,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return {}
        result: dict[str, bytes] = {}
        position = 0
        while position < len(output):
            header_end = output.find(b"\n", position)
            if header_end < 0:
                break
            header = output[position:header_end].split()
            position = header_end + 1
            if len(header) != 3 or header[1] != b"blob":
                break
            size = int(header[2])
            content = output[position : position + size]
            position += size + 1
            result[header[0].decode("ascii", "replace")] = content
        return result

    def _prepare_bytes(
        self,
        path: Path,
        raw: bytes,
        *,
        mtime_ns: int,
        existing_metadata: dict[str, tuple[str, int, int]],
    ) -> _PreparedFile:
        rel = path.relative_to(self.root).as_posix()
        if len(raw) > _MAX_FILE_BYTES:
            return _PreparedFile(path=rel)
        digest = hashlib.sha256(raw).hexdigest()
        previous = existing_metadata.get(rel)
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
            mtime_ns=mtime_ns,
            content_hash=digest,
            size=len(raw),
            symbols=tuple(symbols),
            dependencies=tuple(sorted(dependencies)),
            error=error,
        )

    def _resolved_parents(self, paths: Sequence[Path]) -> dict[Path, Path]:
        result: dict[Path, Path] = {}
        for parent in {path.parent for path in paths}:
            try:
                resolved = parent.resolve()
                resolved.relative_to(self.root)
            except (OSError, ValueError):
                continue
            result[parent] = resolved
        return result

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
        with self._connection() as conn:
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
        with self._connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_symbol(row) for row in rows]

    def symbols_for_file(self, path: str, limit: int = 100) -> list[Symbol]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM symbols WHERE path=? ORDER BY start_line LIMIT ?", (path, limit)
            ).fetchall()
        return [_row_to_symbol(row) for row in rows]

    def dependencies_for(self, path: str) -> list[str]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT target FROM dependencies WHERE source_path=? ORDER BY target", (path,)
            ).fetchall()
        return [str(row[0]) for row in rows]

    def files_matching(self, terms: list[str], limit: int = 50) -> list[str]:
        if not terms:
            return []
        with self._connection() as conn:
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
        with self._connection() as conn:
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
