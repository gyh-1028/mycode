"""Tests for @file/@dir prompt expansion."""

from mycode.mentions import expand_mentions


def test_expand_file_and_dir_mentions(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("x\n", encoding="utf-8")

    out = expand_mentions("read @app.py and list @pkg")

    assert "## @app.py 文件内容" in out
    assert "print('hi')" in out
    assert "## @pkg 目录列表" in out
    assert "mod.py" in out


def test_expand_mentions_leaves_email_missing_and_sensitive_literal(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")

    text = "mail a@b.com missing @nope secret @.env"
    out = expand_mentions(text)

    assert out == text
