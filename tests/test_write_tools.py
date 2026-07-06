"""Tests for edit_file / write_file: diff + confirmation + path checks (Task 7)."""

from pathlib import Path

from mycode.config import Config, PermissionsConfig
from mycode.tools.files import edit_file, write_file


def _set_mode(monkeypatch, mode: str) -> None:
    """Force the permissions.write mode that the tools read via load_config()."""
    cfg = Config(permissions=PermissionsConfig(write=mode))
    monkeypatch.setattr("mycode.config.load_config", lambda: cfg)


def _auto_yes(monkeypatch) -> None:
    monkeypatch.setattr("mycode.ui.print_diff", lambda *a, **k: None)
    monkeypatch.setattr("mycode.ui.confirm_write", lambda prompt: True)


def _auto_no(monkeypatch) -> None:
    monkeypatch.setattr("mycode.ui.print_diff", lambda *a, **k: None)
    monkeypatch.setattr("mycode.ui.confirm_write", lambda prompt: False)


# --------------------------------------------------------------------------- #
# write_file
# --------------------------------------------------------------------------- #
def test_write_file_allow_creates_with_parents(tmp_path, monkeypatch) -> None:
    _set_mode(monkeypatch, "allow")
    target = tmp_path / "sub" / "dir" / "new.py"
    out = write_file(str(target), "print('hi')\n")
    assert out.startswith("已写入")
    assert target.read_text(encoding="utf-8") == "print('hi')\n"


def test_write_file_deny_does_not_write(tmp_path, monkeypatch) -> None:
    _set_mode(monkeypatch, "deny")
    target = tmp_path / "x.py"
    out = write_file(str(target), "data")
    assert out.startswith("用户拒绝了修改")
    assert not target.exists()


def test_write_file_ask_yes_writes(tmp_path, monkeypatch) -> None:
    _set_mode(monkeypatch, "ask")
    _auto_yes(monkeypatch)
    target = tmp_path / "x.py"
    out = write_file(str(target), "data\n")
    assert out.startswith("已写入")
    assert target.exists()


def test_write_file_ask_no_skips(tmp_path, monkeypatch) -> None:
    _set_mode(monkeypatch, "ask")
    _auto_no(monkeypatch)
    target = tmp_path / "x.py"
    out = write_file(str(target), "data\n")
    assert out.startswith("用户拒绝了修改")
    assert not target.exists()


def test_write_file_no_change(tmp_path, monkeypatch) -> None:
    _set_mode(monkeypatch, "allow")
    target = tmp_path / "x.py"
    target.write_text("same\n", encoding="utf-8")
    out = write_file(str(target), "same\n")
    assert "无变化" in out


# --------------------------------------------------------------------------- #
# edit_file
# --------------------------------------------------------------------------- #
def test_edit_file_unique_replace(tmp_path, monkeypatch) -> None:
    _set_mode(monkeypatch, "allow")
    target = tmp_path / "app.py"
    target.write_text("def a():\n    return 1\n", encoding="utf-8")
    out = edit_file(str(target), "return 1", "return 2")
    assert out.startswith("已编辑")
    assert "return 2" in target.read_text(encoding="utf-8")


def test_edit_file_old_str_not_found(tmp_path, monkeypatch) -> None:
    _set_mode(monkeypatch, "allow")
    target = tmp_path / "app.py"
    target.write_text("x = 1\n", encoding="utf-8")
    out = edit_file(str(target), "NOT THERE", "y")
    assert out.startswith("错误:") and "未找到" in out
    assert target.read_text(encoding="utf-8") == "x = 1\n"  # untouched


def test_edit_file_old_str_not_unique(tmp_path, monkeypatch) -> None:
    _set_mode(monkeypatch, "allow")
    target = tmp_path / "app.py"
    target.write_text("x\nx\n", encoding="utf-8")
    out = edit_file(str(target), "x", "y")
    assert out.startswith("错误:") and "不唯一" in out


def test_edit_file_missing_file(tmp_path, monkeypatch) -> None:
    _set_mode(monkeypatch, "allow")
    out = edit_file(str(tmp_path / "nope.py"), "a", "b")
    assert out.startswith("错误:") and "文件不存在" in out


def test_edit_file_no_change_when_same(tmp_path, monkeypatch) -> None:
    _set_mode(monkeypatch, "allow")
    target = tmp_path / "app.py"
    target.write_text("keep\n", encoding="utf-8")
    out = edit_file(str(target), "keep", "keep")
    assert "无变化" in out


# --------------------------------------------------------------------------- #
# path containment (Task 6 check applied to writes)
# --------------------------------------------------------------------------- #
def test_write_out_of_bounds_rejected(monkeypatch) -> None:
    _set_mode(monkeypatch, "allow")
    outside = Path.cwd().parent / "mycode_evil_write.py"
    out = write_file(str(outside), "evil")
    assert out.startswith("错误:") and "项目根目录" in out
    assert not outside.exists()


def test_edit_out_of_bounds_rejected(monkeypatch) -> None:
    _set_mode(monkeypatch, "allow")
    outside = Path.cwd().parent / "mycode_evil_edit.py"
    out = edit_file(str(outside), "a", "b")
    assert out.startswith("错误:") and "项目根目录" in out
