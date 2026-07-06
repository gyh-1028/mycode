"""Provider normalization tests (mocked OpenAI SDK — no network/key needed)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from mycode.llm.base import BaseProvider, LLMResponse, ReasoningChunk, StopReason
from mycode.llm.openai_compatible import (
    OpenAICompatibleProvider,
    _map_finish_reason,
    _to_openai_tool,
)


def _completion(*, content=None, tool_calls=None, finish_reason="stop", usage=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls),
                finish_reason=finish_reason,
            )
        ],
        usage=usage,
    )


def _build_provider(completion):
    """Construct a provider whose underlying client returns `completion`."""
    with patch("mycode.llm.openai_compatible.OpenAI") as mock_openai:
        client = MagicMock()
        client.chat.completions.create.return_value = completion
        mock_openai.return_value = client
        provider = OpenAICompatibleProvider(api_key="k", model="deepseek-chat", base_url="https://api.deepseek.com")
    return provider, client


# --------------------------------------------------------------------------- #
# streaming helpers
# --------------------------------------------------------------------------- #
def _tc_delta(index, id=None, name=None, arguments=None):
    return SimpleNamespace(index=index, id=id, function=SimpleNamespace(name=name, arguments=arguments))


def _stream_chunk(content=None, tool_calls=None, finish_reason=None, usage=None, reasoning_content=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls, reasoning_content=reasoning_content)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


def _usage_only_chunk(prompt, completion, total):
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total),
    )


def _run_stream(gen):
    """Drain a stream() generator, returning (yielded_text_chunks, final_response)."""
    texts = []
    try:
        while True:
            texts.append(next(gen))
    except StopIteration as stop:
        return texts, stop.value


def test_text_response_normalized() -> None:
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=20, total_tokens=30)
    provider, _ = _build_provider(_completion(content="Flask 是一个轻量级 Python web 框架", finish_reason="stop", usage=usage))
    resp = provider.chat([{"role": "user", "content": "解释 Flask 是什么"}])
    assert resp.text == "Flask 是一个轻量级 Python web 框架"
    assert resp.tool_calls == []
    assert resp.stop_reason == StopReason.END_TURN
    assert resp.usage.total_tokens == 30


def test_tool_calls_normalized_and_args_json_parsed() -> None:
    tc = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="read_file", arguments='{"path": "app.py"}'),
    )
    provider, client = _build_provider(_completion(content=None, tool_calls=[tc], finish_reason="tool_calls"))
    tools = [{"name": "read_file", "description": "读文件", "parameters": {"type": "object"}}]
    resp = provider.chat([{"role": "user", "content": "读 app.py"}], tools=tools)

    assert resp.stop_reason == StopReason.TOOL_CALLS
    assert len(resp.tool_calls) == 1
    call = resp.tool_calls[0]
    assert call.id == "call_1"
    assert call.name == "read_file"
    # arguments parsed from JSON string into a real dict
    assert call.args == {"path": "app.py"}

    # tools were sent as native function-calling payload
    sent = client.chat.completions.create.call_args.kwargs
    assert sent["tools"][0] == {
        "type": "function",
        "function": {"name": "read_file", "description": "读文件", "parameters": {"type": "object"}},
    }
    assert sent["tool_choice"] == "auto"


def test_no_tools_omits_tools_kwarg() -> None:
    provider, client = _build_provider(_completion(content="hi"))
    provider.chat([{"role": "user", "content": "hi"}])
    sent = client.chat.completions.create.call_args.kwargs
    assert "tools" not in sent
    assert "tool_choice" not in sent


def test_configured_generation_options_are_sent() -> None:
    completion = _completion(content="hi")
    with patch("mycode.llm.openai_compatible.OpenAI") as mock_openai:
        client = MagicMock()
        client.chat.completions.create.return_value = completion
        mock_openai.return_value = client
        provider = OpenAICompatibleProvider(
            api_key="k",
            model="m",
            max_tokens=123,
            temperature=0.2,
        )

    provider.chat([{"role": "user", "content": "hi"}])

    sent = client.chat.completions.create.call_args.kwargs
    assert sent["max_tokens"] == 123
    assert sent["temperature"] == 0.2


def test_thinking_options_are_sent_to_openai_compatible_endpoint() -> None:
    completion = _completion(content="hi")
    with patch("mycode.llm.openai_compatible.OpenAI") as mock_openai:
        client = MagicMock()
        client.chat.completions.create.return_value = completion
        mock_openai.return_value = client
        provider = OpenAICompatibleProvider(
            api_key="k",
            model="deepseek-v4-pro",
            thinking="enabled",
            reasoning_effort="max",
        )

    provider.chat([{"role": "user", "content": "hi"}])

    sent = client.chat.completions.create.call_args.kwargs
    assert sent["extra_body"] == {"thinking": {"type": "enabled"}}
    assert sent["reasoning_effort"] == "max"


def test_qwen_thinking_uses_enable_flag_and_budget() -> None:
    completion = _completion(content="hi")
    with patch("mycode.llm.openai_compatible.OpenAI") as mock_openai:
        client = MagicMock()
        client.chat.completions.create.return_value = completion
        mock_openai.return_value = client
        provider = OpenAICompatibleProvider(
            api_key="k",
            model="qwen3.7-plus",
            thinking="enabled",
            thinking_format="qwen",
            thinking_budget=8192,
        )

    provider.chat([{"role": "user", "content": "hi"}])

    sent = client.chat.completions.create.call_args.kwargs
    assert sent["extra_body"] == {"enable_thinking": True, "thinking_budget": 8192}
    assert "reasoning_effort" not in sent


def test_openai_reasoning_effort_does_not_send_thinking_body() -> None:
    completion = _completion(content="hi")
    with patch("mycode.llm.openai_compatible.OpenAI") as mock_openai:
        client = MagicMock()
        client.chat.completions.create.return_value = completion
        mock_openai.return_value = client
        provider = OpenAICompatibleProvider(
            api_key="k",
            model="gpt-5.4-mini",
            thinking_format="openai",
            reasoning_effort="medium",
        )

    provider.chat([{"role": "user", "content": "hi"}])

    sent = client.chat.completions.create.call_args.kwargs
    assert sent["reasoning_effort"] == "medium"
    assert "extra_body" not in sent


def test_malformed_tool_arguments_become_empty_dict() -> None:
    tc = SimpleNamespace(id="c", function=SimpleNamespace(name="f", arguments="{not valid json"))
    provider, _ = _build_provider(_completion(tool_calls=[tc], finish_reason="tool_calls"))
    resp = provider.chat([{"role": "user", "content": "x"}])
    assert resp.tool_calls[0].args == {}


def test_usage_missing_defaults_to_zero() -> None:
    provider, _ = _build_provider(_completion(content="ok", usage=None))
    resp = provider.chat([{"role": "user", "content": "x"}])
    assert resp.usage.total_tokens == 0
    assert resp.usage.cached_tokens == 0


def test_cached_tokens_from_deepseek_field() -> None:
    usage = SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=20,
        total_tokens=120,
        prompt_cache_hit_tokens=64,
        prompt_cache_miss_tokens=36,
    )
    provider, _ = _build_provider(_completion(content="ok", usage=usage))
    resp = provider.chat([{"role": "user", "content": "x"}])
    assert resp.usage.cached_tokens == 64


def test_cached_tokens_from_openai_details() -> None:
    usage = SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=20,
        total_tokens=120,
        prompt_tokens_details=SimpleNamespace(cached_tokens=48),
    )
    provider, _ = _build_provider(_completion(content="ok", usage=usage))
    resp = provider.chat([{"role": "user", "content": "x"}])
    assert resp.usage.cached_tokens == 48


def test_finish_reason_mapping() -> None:
    assert _map_finish_reason("tool_calls") == StopReason.TOOL_CALLS
    assert _map_finish_reason("stop") == StopReason.END_TURN
    assert _map_finish_reason("length") == StopReason.MAX_TOKENS
    assert _map_finish_reason("weird") == StopReason.OTHER
    assert _map_finish_reason(None) == StopReason.OTHER


def test_to_openai_tool_shape_and_defaults() -> None:
    out = _to_openai_tool({"name": "x"})
    assert out == {
        "type": "function",
        "function": {"name": "x", "description": "", "parameters": {"type": "object", "properties": {}}},
    }


# --------------------------------------------------------------------------- #
# streaming
# --------------------------------------------------------------------------- #
def test_stream_yields_text_and_assembles_response() -> None:
    chunks = [
        _stream_chunk(content="Fl"),
        _stream_chunk(content="ask"),
        _stream_chunk(content=" 是 web 框架", finish_reason="stop"),
        _usage_only_chunk(3, 5, 8),
    ]
    provider, _ = _build_provider(chunks)
    texts, resp = _run_stream(provider.stream([{"role": "user", "content": "x"}]))
    assert texts == ["Fl", "ask", " 是 web 框架"]  # 逐段 yield
    assert resp.text == "Flask 是 web 框架"
    assert resp.stop_reason == StopReason.END_TURN
    assert resp.usage.total_tokens == 8
    assert resp.tool_calls == []


def test_stream_accumulates_tool_call_arguments_by_index() -> None:
    # arguments arrive in fragments; must be joined then parsed once
    chunks = [
        _stream_chunk(tool_calls=[_tc_delta(0, id="call_1", name="read_file", arguments='{"pa')]),
        _stream_chunk(tool_calls=[_tc_delta(0, arguments='th": "a.py')]),
        _stream_chunk(tool_calls=[_tc_delta(0, arguments='"}')], finish_reason="tool_calls"),
        _usage_only_chunk(4, 6, 10),
    ]
    provider, _ = _build_provider(chunks)
    texts, resp = _run_stream(provider.stream([{"role": "user", "content": "x"}], tools=[{"name": "read_file"}]))
    assert texts == []
    assert resp.stop_reason == StopReason.TOOL_CALLS
    assert len(resp.tool_calls) == 1
    call = resp.tool_calls[0]
    assert call.id == "call_1"
    assert call.name == "read_file"
    assert call.args == {"path": "a.py"}


def test_stream_multiple_tool_calls_by_index() -> None:
    chunks = [
        _stream_chunk(tool_calls=[_tc_delta(0, id="c0", name="list_files", arguments="{}")]),
        _stream_chunk(tool_calls=[_tc_delta(1, id="c1", name="read_file", arguments='{"path":')]),
        _stream_chunk(tool_calls=[_tc_delta(1, arguments=' "a.py"}')], finish_reason="tool_calls"),
    ]
    provider, _ = _build_provider(chunks)
    _, resp = _run_stream(provider.stream([{"role": "user", "content": "x"}], tools=[{"name": "x"}]))
    assert [c.name for c in resp.tool_calls] == ["list_files", "read_file"]
    assert resp.tool_calls[0].args == {}
    assert resp.tool_calls[1].args == {"path": "a.py"}


def test_stream_malformed_tool_args_fall_back_to_empty() -> None:
    chunks = [
        _stream_chunk(tool_calls=[_tc_delta(0, id="c", name="f", arguments="{bad")], finish_reason="tool_calls"),
    ]
    provider, _ = _build_provider(chunks)
    _, resp = _run_stream(provider.stream([{"role": "user", "content": "x"}]))
    assert resp.tool_calls[0].args == {}


def test_stream_falls_back_when_usage_option_unsupported() -> None:
    chunks = [_stream_chunk(content="ok", finish_reason="stop")]
    with patch("mycode.llm.openai_compatible.OpenAI") as mock_openai:
        client = MagicMock()
        client.chat.completions.create.side_effect = [RuntimeError("bad option"), chunks]
        mock_openai.return_value = client
        provider = OpenAICompatibleProvider(api_key="k", model="m", retry_backoff=0, max_retries=0)

    texts, resp = _run_stream(provider.stream([{"role": "user", "content": "x"}]))

    assert texts == ["ok"]
    assert resp.text == "ok"
    first = client.chat.completions.create.call_args_list[0].kwargs
    second = client.chat.completions.create.call_args_list[1].kwargs
    assert "stream_options" in first
    assert "stream_options" not in second


def test_base_stream_default_delegates_to_chat() -> None:
    class _ChatOnly(BaseProvider):
        def chat(self, messages, tools=None):
            return LLMResponse(text="hi there", stop_reason=StopReason.END_TURN)

    texts, resp = _run_stream(_ChatOnly().stream([{"role": "user", "content": "x"}]))
    assert texts == ["hi there"]  # whole text yielded once
    assert resp.text == "hi there"


def test_reasoning_content_in_chat_response() -> None:
    message = SimpleNamespace(
        content="答案是 42",
        reasoning_content="让我想想,6 乘 7 等于 42",
        tool_calls=None,
    )
    completion = _completion(
        content="答案是 42",
        finish_reason="stop",
    )
    # patch message attribute after _completion builds it
    completion.choices[0].message = message
    provider, _ = _build_provider(completion)
    resp = provider.chat([{"role": "user", "content": "x"}])
    assert resp.text == "答案是 42"
    assert resp.reasoning_content == "让我想想,6 乘 7 等于 42"


def test_stream_yields_reasoning_chunks_and_assembles_response() -> None:
    chunks = [
        _stream_chunk(reasoning_content="先思考"),
        _stream_chunk(reasoning_content="一下"),
        _stream_chunk(content="ok", finish_reason="stop"),
    ]
    provider, _ = _build_provider(chunks)
    yielded, resp = _run_stream(provider.stream([{"role": "user", "content": "x"}]))
    assert yielded == [ReasoningChunk("先思考"), ReasoningChunk("一下"), "ok"]
    assert resp.text == "ok"
    assert resp.reasoning_content == "先思考一下"
    assert resp.stop_reason == StopReason.END_TURN


def test_base_stream_yields_reasoning_before_final_text() -> None:
    class _ChatOnlyReasoning(BaseProvider):
        def chat(self, messages, tools=None):
            return LLMResponse(
                text="answer",
                reasoning_content="thinking",
                stop_reason=StopReason.END_TURN,
            )

    yielded, resp = _run_stream(_ChatOnlyReasoning().stream([{"role": "user", "content": "x"}]))

    assert yielded == [ReasoningChunk("thinking"), "answer"]
    assert resp.reasoning_content == "thinking"
