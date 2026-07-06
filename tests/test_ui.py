"""Tests for ui.py: unified diff, stats, coloring, write approval (Task 7)."""

import io

from rich.console import Console

from mycode.ui import (
    diff_stats,
    make_unified_diff,
    print_stream_chunk,
    print_usage,
    render_diff,
    request_write_approval,
)


def test_print_usage_shows_cache_savings_only_when_hit() -> None:
    buf = io.StringIO()
    console = Console(file=buf, width=200)
    print_usage(100, 20, 120, console=console, cached_tokens=64)
    out = buf.getvalue()
    assert "120" in out and "64" in out and "命中缓存" in out

    buf2 = io.StringIO()
    print_usage(100, 20, 120, console=Console(file=buf2, width=200), cached_tokens=0)
    assert "命中缓存" not in buf2.getvalue()


def test_print_stream_chunk_writes_raw_and_flushes() -> None:
    buf = io.StringIO()
    console = Console(file=buf)
    print_stream_chunk("ab", console=console)
    print_stream_chunk("c", console=console)
    assert buf.getvalue() == "abc"  # no newline, no styling


def test_make_unified_diff_marks_add_and_remove() -> None:
    diff = make_unified_diff("f.py", "a\nb\nc\n", "a\nB\nc\n")
    lines = diff.splitlines()
    assert any(line.startswith("-b") for line in lines)
    assert any(line.startswith("+B") for line in lines)


def test_make_unified_diff_empty_when_identical() -> None:
    assert make_unified_diff("f.py", "x\ny\n", "x\ny\n") == ""


def test_diff_stats_counts_added_removed() -> None:
    diff = make_unified_diff("f.py", "a\nb\n", "a\nb\nc\nd\n")
    added, removed = diff_stats(diff)
    assert (added, removed) == (2, 0)


def test_render_diff_applies_colors() -> None:
    text = render_diff("--- a/f\n+++ b/f\n@@ -1 +1 @@\n-old\n+new\n ctx")
    assert "old" in text.plain and "new" in text.plain
    styles = {str(span.style) for span in text.spans}
    assert any("green" in s for s in styles)
    assert any("red" in s for s in styles)


def test_request_write_approval_allow_and_deny() -> None:
    assert request_write_approval("f", "+x", mode="allow") is True
    assert request_write_approval("f", "+x", mode="deny") is False


def test_request_write_approval_ask_uses_confirm(monkeypatch) -> None:
    monkeypatch.setattr("mycode.ui.print_diff", lambda *a, **k: None)
    monkeypatch.setattr("mycode.ui.confirm_write", lambda prompt: True)
    assert request_write_approval("f", "+x", mode="ask") is True
    monkeypatch.setattr("mycode.ui.confirm_write", lambda prompt: False)
    assert request_write_approval("f", "+x", mode="ask") is False
