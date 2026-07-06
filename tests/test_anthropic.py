"""Anthropic provider tests (mocked SDK) + the abstraction health check (Task 14).

The key test is `test_loop_unchanged_with_anthropic_provider`: the SAME run_agent
drives the Anthropic provider with no loop changes, proving Task 2's normalization
is provider-agnostic.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from mycode.llm.anthropic import (
    AnthropicProvider,
    _map_stop_reason,
    _normalize,
    _to_anthropic_messages,
    _to_anthropic_tools,
)
from mycode.llm.base import StopReason
from tests.fakes import quiet_console


# --------------------------------------------------------------------------- #
# fakes for the Anthropic SDK shapes
# --------------------------------------------------------------------------- #
def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(id, name, input):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def _message(content, stop_reason="end_turn", input_tokens=5, output_tokens=3):
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


class _FakeStreamCM:
    def __init__(self, texts, final):
        self._texts = texts
        self._final = final

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self):
        return iter(self._texts)

    def get_final_message(self):
        return self._final


def _build_provider(*, stream_returns=None, create_returns=None):
    with patch("mycode.llm.anthropic.anthropic.Anthropic") as mock_anthropic:
        client = MagicMock()
        if stream_returns is not None:
            client.messages.stream.side_effect = stream_returns
        if create_returns is not None:
            client.messages.create.side_effect = create_returns
        mock_anthropic.return_value = client
        provider = AnthropicProvider(api_key="k", model="claude-sonnet-4-6")
    return provider, client


# --------------------------------------------------------------------------- #
# conversion: internal (OpenAI shape) -> Anthropic
# --------------------------------------------------------------------------- #
def test_system_lifted_to_top_level() -> None:
    system, msgs = _to_anthropic_messages(
        [{"role": "system", "content": "你是助手"}, {"role": "user", "content": "hi"}]
    )
    assert system == "你是助手"
    assert msgs == [{"role": "user", "content": "hi"}]


def test_assistant_tool_calls_become_tool_use_blocks() -> None:
    _, msgs = _to_anthropic_messages(
        [
            {"role": "user", "content": "读 a.py"},
            {
                "role": "assistant",
                "content": "好的",
                "tool_calls": [
                    {"id": "toolu_1", "type": "function",
                     "function": {"name": "read_file", "arguments": '{"path": "a.py"}'}}
                ],
            },
            {"role": "tool", "tool_call_id": "toolu_1", "content": "X = 1"},
            {"role": "assistant", "content": "完成"},
        ]
    )
    # assistant turn carries text + tool_use (input parsed to a dict)
    assistant = msgs[1]
    assert assistant["role"] == "assistant"
    blocks = assistant["content"]
    assert blocks[0] == {"type": "text", "text": "好的"}
    assert blocks[1] == {"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {"path": "a.py"}}
    # tool result lands in a *user* message as a tool_result block, id matched
    tool_msg = msgs[2]
    assert tool_msg["role"] == "user"
    assert tool_msg["content"] == [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "X = 1"}
    ]
    assert msgs[3] == {"role": "assistant", "content": "完成"}


def test_consecutive_tool_results_merge_into_one_user_message() -> None:
    _, msgs = _to_anthropic_messages(
        [
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "t1", "type": "function", "function": {"name": "a", "arguments": "{}"}},
                    {"id": "t2", "type": "function", "function": {"name": "b", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "t1", "content": "r1"},
            {"role": "tool", "tool_call_id": "t2", "content": "r2"},
        ]
    )
    # both tool results in ONE user message, in order
    last = msgs[-1]
    assert last["role"] == "user"
    assert [b["tool_use_id"] for b in last["content"]] == ["t1", "t2"]


def test_to_anthropic_tools_shape() -> None:
    out = _to_anthropic_tools([{"name": "x", "description": "d", "parameters": {"type": "object"}}])
    assert out == [{"name": "x", "description": "d", "input_schema": {"type": "object"}}]
    assert _to_anthropic_tools(None) is None


# --------------------------------------------------------------------------- #
# normalize: Anthropic Message -> LLMResponse
# --------------------------------------------------------------------------- #
def test_normalize_text_and_usage() -> None:
    resp = _normalize(_message([_text_block("Flask 是框架")], stop_reason="end_turn", input_tokens=10, output_tokens=4))
    assert resp.text == "Flask 是框架"
    assert resp.tool_calls == []
    assert resp.stop_reason == StopReason.END_TURN
    assert resp.usage.total_tokens == 14


def test_normalize_tool_use() -> None:
    msg = _message(
        [_tool_use_block("toolu_9", "read_file", {"path": "a.py"})], stop_reason="tool_use"
    )
    resp = _normalize(msg)
    assert resp.stop_reason == StopReason.TOOL_CALLS
    assert len(resp.tool_calls) == 1
    tc = resp.tool_calls[0]
    assert tc.id == "toolu_9" and tc.name == "read_file"
    assert tc.args == {"path": "a.py"}  # already a dict, no JSON parsing


def test_normalize_reads_cache_tokens() -> None:
    msg = SimpleNamespace(
        content=[_text_block("hi")],
        stop_reason="end_turn",
        usage=SimpleNamespace(
            input_tokens=12,
            output_tokens=5,
            cache_read_input_tokens=200,
            cache_creation_input_tokens=300,
        ),
    )
    resp = _normalize(msg)
    assert resp.usage.cached_tokens == 200
    assert resp.usage.cache_write_tokens == 300


def test_map_stop_reason() -> None:
    assert _map_stop_reason("tool_use") == StopReason.TOOL_CALLS
    assert _map_stop_reason("end_turn") == StopReason.END_TURN
    assert _map_stop_reason("max_tokens") == StopReason.MAX_TOKENS
    assert _map_stop_reason("refusal") == StopReason.OTHER
    assert _map_stop_reason(None) == StopReason.OTHER


# --------------------------------------------------------------------------- #
# chat() / stream()
# --------------------------------------------------------------------------- #
def test_chat_normalizes_and_sends_system_and_tools() -> None:
    provider, client = _build_provider(create_returns=[_message([_text_block("hi")])])
    resp = provider.chat(
        [{"role": "system", "content": "S"}, {"role": "user", "content": "x"}],
        tools=[{"name": "read_file", "description": "d", "parameters": {"type": "object"}}],
    )
    assert resp.text == "hi"
    sent = client.messages.create.call_args.kwargs
    # system lifted to a top-level text block, with a cache breakpoint
    assert sent["system"] == [
        {"type": "text", "text": "S", "cache_control": {"type": "ephemeral"}}
    ]
    assert sent["model"] == "claude-sonnet-4-6"
    assert sent["max_tokens"] == 8192
    assert sent["tools"][0]["input_schema"] == {"type": "object"}
    # the last tool definition carries a cache breakpoint too
    assert sent["tools"][-1]["cache_control"] == {"type": "ephemeral"}


def test_chat_sends_configured_temperature_and_tokens() -> None:
    provider, client = _build_provider(create_returns=[_message([_text_block("hi")])])
    provider.max_tokens = 123
    provider.temperature = 0.1

    provider.chat([{"role": "user", "content": "x"}])

    sent = client.messages.create.call_args.kwargs
    assert sent["max_tokens"] == 123
    assert sent["temperature"] == 0.1


def test_chat_sends_adaptive_thinking_and_effort() -> None:
    provider, client = _build_provider(create_returns=[_message([_text_block("hi")])])
    provider.thinking = "enabled"
    provider.thinking_format = "anthropic"
    provider.reasoning_effort = "medium"

    provider.chat([{"role": "user", "content": "x"}])

    sent = client.messages.create.call_args.kwargs
    assert sent["thinking"] == {"type": "adaptive"}
    assert sent["output_config"] == {"effort": "medium"}


def test_stream_yields_text_and_returns_response() -> None:
    final = _message([_text_block("ab")], stop_reason="end_turn")
    provider, _ = _build_provider(stream_returns=[_FakeStreamCM(["a", "b"], final)])
    gen = provider.stream([{"role": "user", "content": "x"}])
    chunks = []
    try:
        while True:
            chunks.append(next(gen))
    except StopIteration as stop:
        resp = stop.value
    assert chunks == ["a", "b"]
    assert resp.text == "ab"


# --------------------------------------------------------------------------- #
# THE HEALTH CHECK: run_agent unchanged with the Anthropic provider
# --------------------------------------------------------------------------- #
def test_loop_unchanged_with_anthropic_provider(tmp_path) -> None:
    from mycode.agent.loop import run_agent

    (tmp_path / "a.py").write_text("X = 1\n", encoding="utf-8")

    msg_tool = _message(
        [_tool_use_block("toolu_1", "list_files", {"path": str(tmp_path)})],
        stop_reason="tool_use",
    )
    msg_done = _message([_text_block("完成")], stop_reason="end_turn")
    provider, client = _build_provider(
        stream_returns=[_FakeStreamCM([], msg_tool), _FakeStreamCM(["完", "成"], msg_done)]
    )

    messages = [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "列出文件"},
    ]
    tools = [{"name": "list_files", "description": "列目录",
              "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}]

    result = run_agent(provider, messages, max_steps=5, tools=tools, console=quiet_console())

    assert result == "完成"
    # the loop kept messages in the internal (OpenAI) shape, with paired tool result
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    assert len(tool_msgs) == 1 and tool_msgs[0]["tool_call_id"] == "toolu_1"
    assert "a.py" in tool_msgs[0]["content"]

    # the 2nd Anthropic request carried system + a matching tool_result block
    second = client.messages.stream.call_args_list[1].kwargs
    assert second["system"][0]["text"] == "你是助手"
    anth_msgs = second["messages"]
    has_tool_result = any(
        isinstance(m["content"], list)
        and any(b.get("type") == "tool_result" and b["tool_use_id"] == "toolu_1" for b in m["content"])
        for m in anth_msgs
    )
    assert has_tool_result
