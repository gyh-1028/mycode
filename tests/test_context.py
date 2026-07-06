"""Tests for context.py: token estimate, turn splitting, compaction (Task 13)."""

from mycode.context import estimate_tokens, maybe_compact, split_turns


def _assert_paired(messages: list[dict]) -> None:
    """No orphan tool_calls / tool_results: every assistant.tool_calls is
    immediately followed by matching tool results, and no tool message stands
    alone."""
    i = 0
    n = len(messages)
    while i < n:
        m = messages[i]
        if m["role"] == "assistant" and m.get("tool_calls"):
            ids = [tc["id"] for tc in m["tool_calls"]]
            following = messages[i + 1 : i + 1 + len(ids)]
            assert [x["role"] for x in following] == ["tool"] * len(ids)
            assert [x["tool_call_id"] for x in following] == ids
            i += 1 + len(ids)
        else:
            assert m["role"] != "tool", f"orphan tool result at index {i}"
            i += 1


def _tool_turn(user_text: str, call_id: str, result: str) -> list[dict]:
    return [
        {"role": "user", "content": user_text},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": call_id, "type": "function",
                 "function": {"name": "read_file", "arguments": '{"path": "x"}'}}
            ],
        },
        {"role": "tool", "tool_call_id": call_id, "content": result},
        {"role": "assistant", "content": f"已处理 {user_text}"},
    ]


def _convo(n_turns: int) -> list[dict]:
    msgs: list[dict] = [{"role": "system", "content": "系统提示"}]
    for k in range(n_turns):
        msgs += _tool_turn(f"任务{k}", f"call_{k}", f"结果{k} " * 20)
    return msgs


# --------------------------------------------------------------------------- #
def test_estimate_tokens_counts_content_and_tool_args() -> None:
    msgs = [
        {"role": "user", "content": "a" * 40},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c", "type": "function",
                         "function": {"name": "read_file", "arguments": "b" * 40}}]},
    ]
    # ~ (40 + 40 + len("read_file")) chars / 4, plus per-message overhead
    assert estimate_tokens(msgs) > 20


def test_split_turns_keeps_tool_block_in_one_turn() -> None:
    msgs = _convo(3)
    system_msgs, turns = split_turns(msgs)
    assert len(system_msgs) == 1 and system_msgs[0]["role"] == "system"
    assert len(turns) == 3
    for turn in turns:
        assert turn[0]["role"] == "user"
        # the assistant tool_calls and its tool result live in the same turn
        roles = [m["role"] for m in turn]
        assert roles == ["user", "assistant", "tool", "assistant"]


def test_maybe_compact_noop_under_budget() -> None:
    msgs = _convo(3)
    before = list(msgs)
    changed = maybe_compact(None, msgs, context_limit=10_000_000, summarizer=lambda old: "S")
    assert changed is False
    assert msgs == before


def test_maybe_compact_summarizes_old_turns_and_keeps_pairing() -> None:
    msgs = _convo(8)
    tokens_before = estimate_tokens(msgs)

    changed = maybe_compact(
        None, msgs, context_limit=10, keep_recent_turns=2,
        summarizer=lambda old_turns: f"压缩了 {len(old_turns)} 轮",
    )
    assert changed is True

    # system kept at front
    assert msgs[0] == {"role": "system", "content": "系统提示"}
    # summary message injected right after system
    assert msgs[1]["role"] == "user"
    assert "前文摘要" in msgs[1]["content"]
    assert "压缩了 6 轮" in msgs[1]["content"]  # 8 - 2 kept = 6 summarized

    # the 2 most recent turns are preserved verbatim (4 msgs each)
    _, turns = split_turns(_convo(8))
    recent = [m for t in turns[-2:] for m in t]
    assert msgs[-len(recent):] == recent

    # CRITICAL: no orphan tool_calls / tool_results after compaction
    _assert_paired(msgs)
    # and it actually shrank
    assert estimate_tokens(msgs) < tokens_before


def test_maybe_compact_when_too_few_turns_is_noop() -> None:
    msgs = _convo(2)
    changed = maybe_compact(
        None, msgs, context_limit=1, keep_recent_turns=6, summarizer=lambda old: "S"
    )
    assert changed is False  # over budget but nothing older than the kept window


def test_maybe_compact_summary_failure_is_noop() -> None:
    msgs = _convo(8)
    before = [dict(m) for m in msgs]

    def fail(_old_turns):
        raise RuntimeError("boom")

    changed = maybe_compact(
        None, msgs, context_limit=10, keep_recent_turns=2, summarizer=fail
    )

    assert changed is False
    assert msgs == before


def test_repeated_compaction_folds_previous_summary() -> None:
    msgs = _convo(8)
    maybe_compact(None, msgs, context_limit=10, keep_recent_turns=2,
                  summarizer=lambda old: "第一次摘要")
    # grow again then compact a second time
    msgs += _tool_turn("新任务", "call_new", "新结果 " * 20)
    changed = maybe_compact(None, msgs, context_limit=10, keep_recent_turns=2,
                            summarizer=lambda old: "第二次摘要")
    assert changed is True
    assert msgs[0]["role"] == "system"
    assert "第二次摘要" in msgs[1]["content"]
    _assert_paired(msgs)
