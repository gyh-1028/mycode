"""Tests for the tool registry and the read-only tools (Task 3)."""

from types import SimpleNamespace

from mycode.config import Config, PermissionsConfig
from mycode.tools import dispatch, get_schemas
from mycode.tools.files import apply_patch, list_files, read_file
from mycode.tools.search import find_files, search_code


# --------------------------------------------------------------------------- #
# registry / schemas
# --------------------------------------------------------------------------- #
def test_get_schemas_lists_the_three_tools() -> None:
    schemas = get_schemas()
    names = {s["name"] for s in schemas}
    assert {"list_files", "read_file", "search_code"} <= names
    for s in schemas:
        assert set(s) == {"name", "description", "parameters"}
        assert s["parameters"]["type"] == "object"
        assert s["description"]


# --------------------------------------------------------------------------- #
# list_files
# --------------------------------------------------------------------------- #
def test_list_files_basic_and_ignores_noise(tmp_path) -> None:
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / "b.txt").write_text("y", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    # these must be ignored
    (tmp_path / ".git").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "__pycache__").mkdir()

    out = list_files(str(tmp_path))
    assert "a.py" in out
    assert "b.txt" in out
    assert "pkg/" in out
    assert ".git" not in out
    assert "node_modules" not in out
    assert "__pycache__" not in out


def test_list_files_missing_path_returns_error_string() -> None:
    out = list_files("no/such/dir-xyz")
    assert out.startswith("错误:")


def test_list_files_on_a_file_returns_error_string(tmp_path) -> None:
    f = tmp_path / "f.py"
    f.write_text("x", encoding="utf-8")
    assert list_files(str(f)).startswith("错误:")


# --------------------------------------------------------------------------- #
# read_file
# --------------------------------------------------------------------------- #
def test_read_file_basic(tmp_path) -> None:
    f = tmp_path / "hello.py"
    f.write_text("print('hi')\n# 中文注释\n", encoding="utf-8")
    out = read_file(str(f))
    assert "print('hi')" in out
    assert "中文注释" in out


def test_read_file_line_range(tmp_path) -> None:
    f = tmp_path / "hello.py"
    f.write_text("one\ntwo\nthree\n", encoding="utf-8")
    assert read_file(str(f), start_line=2, end_line=3) == "two\nthree"


def test_read_file_rejects_binary(tmp_path) -> None:
    f = tmp_path / "blob.bin"
    f.write_bytes(b"a\0b")
    out = read_file(str(f))
    assert out.startswith("错误:") and "二进制" in out


def test_read_file_missing_returns_error_string() -> None:
    assert read_file("nope-xyz.txt").startswith("错误:")


def test_read_file_truncates_over_max_lines(tmp_path, monkeypatch) -> None:
    f = tmp_path / "big.txt"
    f.write_text("\n".join(f"line{i}" for i in range(50)), encoding="utf-8")
    monkeypatch.setattr(
        "mycode.config.load_config", lambda: Config(max_file_lines=5)
    )
    out = read_file(str(f))
    assert "line0" in out
    assert "已截断" in out
    assert "start_line/end_line" in out
    assert "line49" not in out


# --------------------------------------------------------------------------- #
# search_code
# --------------------------------------------------------------------------- #
def test_search_code_finds_keyword(tmp_path) -> None:
    (tmp_path / "mod.py").write_text(
        "def handler():\n    return MAGIC_TOKEN\n", encoding="utf-8"
    )
    out = search_code("MAGIC_TOKEN", str(tmp_path))
    assert "MAGIC_TOKEN" in out
    assert "mod.py" in out
    assert "匹配文件数:1" in out
    assert "匹配行数:1" in out


def test_search_code_no_match_is_not_an_error(tmp_path) -> None:
    (tmp_path / "mod.py").write_text("nothing here\n", encoding="utf-8")
    out = search_code("DEFINITELY_ABSENT", str(tmp_path))
    assert not out.startswith("错误:")
    assert "未找到匹配" in out


def test_search_code_respects_ignore_dirs(tmp_path, monkeypatch) -> None:
    # force the Python fallback so the assertion is independent of rg
    monkeypatch.setattr("mycode.tools.search.shutil.which", lambda _: None)
    nm = tmp_path / "node_modules"
    nm.mkdir()
    (nm / "vendor.js").write_text("var SECRET = 1\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("ok = True\n", encoding="utf-8")
    out = search_code("SECRET", str(tmp_path))
    assert "未找到匹配" in out


def test_mycodeignore_filters_list_search_and_find(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("mycode.tools.search.shutil.which", lambda _: None)
    (tmp_path / ".mycodeignore").write_text("ignored.py\nignored_dir/\n", encoding="utf-8")
    (tmp_path / "ignored.py").write_text("SECRET = 1\n", encoding="utf-8")
    (tmp_path / "keep.py").write_text("SECRET = 2\n", encoding="utf-8")
    (tmp_path / "ignored_dir").mkdir()
    (tmp_path / "ignored_dir" / "nested.py").write_text("SECRET = 3\n", encoding="utf-8")

    listing = list_files(str(tmp_path))
    assert "keep.py" in listing
    assert "ignored.py" not in listing
    assert "ignored_dir" not in listing

    search = search_code("SECRET", str(tmp_path))
    assert "keep.py" in search
    assert "ignored.py" not in search
    assert "nested.py" not in search

    found = find_files("*.py", str(tmp_path))
    assert found == "keep.py"


def test_search_code_python_fallback_when_no_rg(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("mycode.tools.search.shutil.which", lambda _: None)
    (tmp_path / "x.py").write_text("KEYWORD_ABC = 1\n", encoding="utf-8")
    out = search_code("KEYWORD_ABC", str(tmp_path))
    assert "KEYWORD_ABC" in out and "x.py" in out


def test_search_code_uses_ripgrep_when_available(tmp_path, monkeypatch) -> None:
    # pretend rg exists and stub its output to exercise the ripgrep branch
    monkeypatch.setattr("mycode.tools.search.shutil.which", lambda _: "rg")
    fake = SimpleNamespace(returncode=0, stdout="app.py:2:    return MAGIC\n", stderr="")
    monkeypatch.setattr(
        "mycode.tools.search.subprocess.run", lambda *a, **k: fake
    )
    out = search_code("MAGIC", str(tmp_path))
    assert "匹配文件数:1" in out
    assert "匹配行数:1" in out
    assert "app.py:2:    return MAGIC" in out


def test_search_code_reports_truncation_hint(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("mycode.tools.search.shutil.which", lambda _: None)
    monkeypatch.setattr("mycode.tools.search._MAX_RESULTS", 2)
    for idx in range(4):
        (tmp_path / f"m{idx}.py").write_text("NEEDLE = 1\n", encoding="utf-8")

    out = search_code("NEEDLE", str(tmp_path))

    assert "匹配文件数:" in out
    assert "匹配过多" in out
    assert "请缩小 query 或 path" in out


def test_search_code_ripgrep_error_returns_error_string(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("mycode.tools.search.shutil.which", lambda _: "rg")
    fake = SimpleNamespace(returncode=2, stdout="", stderr="regex parse error")
    monkeypatch.setattr("mycode.tools.search.subprocess.run", lambda *a, **k: fake)
    out = search_code("(", str(tmp_path))
    assert out.startswith("错误:")


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #
def test_dispatch_runs_tool(tmp_path) -> None:
    f = tmp_path / "a.py"
    f.write_text("hello\n", encoding="utf-8")
    assert "a.py" in dispatch("list_files", {"path": str(tmp_path)})
    assert "hello" in dispatch("read_file", {"path": str(f)})


def test_apply_patch_tool(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "mycode.config.load_config",
        lambda: Config(permissions=PermissionsConfig(write="allow")),
    )
    target = tmp_path / "app.py"
    target.write_text("a\nb\nc\n", encoding="utf-8")
    patch = """--- a/app.py
+++ b/app.py
@@ -1,3 +1,3 @@
 a
-b
+B
 c
"""

    out = apply_patch(str(target), patch)

    assert out.startswith("已打补丁")
    assert target.read_text(encoding="utf-8") == "a\nB\nc\n"


def test_dispatch_unknown_tool_returns_error_string() -> None:
    out = dispatch("no_such_tool", {})
    assert out.startswith("错误:")
    assert "未知工具" in out


def test_dispatch_bad_args_returns_error_string() -> None:
    # read_file requires `path`; omitting it must not raise
    out = dispatch("read_file", {})
    assert out.startswith("错误:")


def test_dispatch_non_dict_args_returns_error_string() -> None:
    out = dispatch("list_files", ["not", "a", "dict"])  # type: ignore[arg-type]
    assert out.startswith("错误:")
