"""Tests for prompts.py: base prompt + MYCODE.md injection (Task 9)."""

from mycode.prompts import KIMI_SYSTEM_PERSONA, SYSTEM_PROMPT, build_system_prompt


def test_base_prompt_lists_tools_and_constraints(tmp_path) -> None:
    prompt = build_system_prompt(root=tmp_path)  # no MYCODE.md here
    assert prompt == SYSTEM_PROMPT
    for tool in ("list_files", "read_file", "search_code", "edit_file", "apply_patch", "write_file", "run_bash", "git_status", "git_log", "git_branch"):
        assert tool in prompt
    assert "先探索" in prompt  # explore-first constraint
    assert "提问" in prompt    # ask-when-uncertain constraint


def test_mycode_md_is_appended(tmp_path) -> None:
    (tmp_path / "MYCODE.md").write_text("缩进用 4 个空格,不要改 legacy/。", encoding="utf-8")
    prompt = build_system_prompt(root=tmp_path)
    assert SYSTEM_PROMPT in prompt
    assert "缩进用 4 个空格" in prompt
    assert "MYCODE.md" in prompt


def test_empty_mycode_md_ignored(tmp_path) -> None:
    (tmp_path / "MYCODE.md").write_text("   \n", encoding="utf-8")
    assert build_system_prompt(root=tmp_path) == SYSTEM_PROMPT


def test_kimi_persona_appended_for_provider() -> None:
    prompt = build_system_prompt(provider="kimi")
    assert SYSTEM_PROMPT in prompt
    assert KIMI_SYSTEM_PERSONA in prompt
    assert "256K" in prompt


def test_kimi_persona_appended_for_model() -> None:
    prompt = build_system_prompt(model="kimi-k2.7-code")
    assert SYSTEM_PROMPT in prompt
    assert KIMI_SYSTEM_PERSONA in prompt


def test_non_kimi_provider_does_not_append_persona() -> None:
    prompt = build_system_prompt(provider="openai", model="gpt-5.4-mini")
    assert prompt == SYSTEM_PROMPT
