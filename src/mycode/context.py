"""Context-window management: estimate size, compact old turns into a summary.

When the conversation approaches the model's context limit, keep the system
message(s) and the most recent K *turns* verbatim, and replace everything older
with one LLM-generated summary.

CRITICAL invariant: compaction operates only on whole turns. A "turn" begins at
a ``user`` message and includes every assistant/tool message up to (but not
including) the next ``user`` message — so an assistant ``tool_calls`` block and
its matching ``tool`` results are always within the same turn and are never
split. Dropping/summarizing whole turns therefore keeps the message list
API-valid (no orphan tool_call without its tool_result, or vice versa).
"""

from collections.abc import Callable
from typing import Any

CHARS_PER_TOKEN = 4
THRESHOLD = 0.7
KEEP_RECENT_TURNS = 6

Message = dict[str, Any]


def estimate_tokens(messages: list[Message]) -> int:
    """粗略估算 token 数:字符数 / 4(含 tool_calls 的 name/arguments)。"""
    total = 0
    for m in messages:
        total += len(m.get("content") or "")
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function", {})
            total += len(fn.get("name") or "") + len(fn.get("arguments") or "")
        total += 8  # 每条消息角色/结构的粗略开销
    return total // CHARS_PER_TOKEN


def split_turns(
    messages: list[Message],
) -> tuple[list[Message], list[list[Message]]]:
    """拆成 (前导 system 消息列表, 轮次列表)。

    每个轮次从一条 user 消息开始,直到下一条 user 之前 —— 因此 assistant(tool_calls)
    与它的 tool 结果一定落在同一轮,不会被拆散。
    """
    system_msgs: list[Message] = []
    i = 0
    while i < len(messages) and messages[i].get("role") == "system":
        system_msgs.append(messages[i])
        i += 1

    turns: list[list[Message]] = []
    current: list[Message] = []
    for m in messages[i:]:
        if m.get("role") == "user" and current:
            turns.append(current)
            current = []
        current.append(m)
    if current:
        turns.append(current)
    return system_msgs, turns


def _render_transcript(turns: list[list[Message]]) -> str:
    lines: list[str] = []
    for turn in turns:
        for m in turn:
            role = m.get("role")
            content = m.get("content") or ""
            if role == "assistant":
                if content:
                    lines.append(f"助手:{content}")
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function", {})
                    lines.append(
                        f"助手调用工具:{fn.get('name')}({fn.get('arguments')})"
                    )
            elif role == "tool":
                if len(content) > 500:
                    content = content[:500] + "…(已截断)"
                lines.append(f"工具结果:{content}")
            elif role == "user":
                lines.append(f"用户:{content}")
            else:
                lines.append(f"{role}:{content}")
    return "\n".join(lines)


def _summarize_with_llm(provider: Any, old_turns: list[list[Message]]) -> str:
    prompt = [
        {
            "role": "system",
            "content": (
                "你是对话摘要器。把下面 agent 与用户的历史对话压缩成简洁中文摘要,"
                "保留:用户目标/需求、已读或已改的文件名、运行过的命令及其结果、"
                "重要结论与决定、以及尚未完成的事项。不要遗漏关键事实。只输出摘要正文。"
            ),
        },
        {"role": "user", "content": _render_transcript(old_turns)},
    ]
    resp = provider.chat(prompt)
    return (resp.text or "").strip() or "(无可用摘要)"


def maybe_compact(
    provider: Any,
    messages: list[Message],
    *,
    context_limit: int,
    threshold: float = THRESHOLD,
    keep_recent_turns: int = KEEP_RECENT_TURNS,
    summarizer: Callable[[list[list[Message]]], str] | None = None,
) -> bool:
    """若估算 token 超过 context_limit*threshold,则把较早的整轮压成一段摘要,原地
    修改 messages —— 保留 system + 最近 keep_recent_turns 轮。返回是否进行了压缩。"""
    budget = int(context_limit * threshold)
    if estimate_tokens(messages) <= budget:
        return False

    system_msgs, turns = split_turns(messages)
    if len(turns) <= keep_recent_turns:
        return False  # 没有更早的整轮可压,只能保留现状

    old_turns = turns[:-keep_recent_turns]
    recent_turns = turns[-keep_recent_turns:]

    summarize = summarizer or (lambda old: _summarize_with_llm(provider, old))
    try:
        summary_text = summarize(old_turns)
    except Exception:
        return False

    summary_msg: Message = {
        "role": "user",
        "content": "【前文摘要】(为节省上下文,较早的对话已压缩为以下摘要)\n"
        + summary_text,
    }
    new_messages: list[Message] = [*system_msgs, summary_msg]
    for turn in recent_turns:
        new_messages.extend(turn)
    messages[:] = new_messages  # 原地替换,保持同一列表对象
    return True
