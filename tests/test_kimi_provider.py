"""Kimi provider-specific defaults and parameter tests (mocked)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from mycode.llm.kimi import (
    KIMI_DEFAULT_BASE_URL,
    KIMI_DEFAULT_MAX_TOKENS,
    KIMI_DEFAULT_TEMPERATURE,
    KIMI_DEFAULT_TOP_P,
    KimiProvider,
)


def _completion(content="hi", finish_reason="stop", usage=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=None),
                finish_reason=finish_reason,
            )
        ],
        usage=usage,
    )


def _build_provider(**kwargs):
    with patch("mycode.llm.openai_compatible.OpenAI") as mock_openai:
        client = MagicMock()
        client.chat.completions.create.return_value = _completion()
        mock_openai.return_value = client
        merged = {
            "api_key": "k",
            "model": "kimi-k2.7-code",
        }
        merged.update(kwargs)
        provider = KimiProvider(**merged)
    return provider, client, mock_openai


def test_kimi_defaults_sent_in_chat_kwargs() -> None:
    provider, client, _ = _build_provider()
    provider.chat([{"role": "user", "content": "hello"}])
    sent = client.chat.completions.create.call_args.kwargs
    assert sent["model"] == "kimi-k2.7-code"
    assert sent["temperature"] == KIMI_DEFAULT_TEMPERATURE
    assert sent["top_p"] == KIMI_DEFAULT_TOP_P
    assert sent["max_tokens"] == KIMI_DEFAULT_MAX_TOKENS


def test_kimi_default_base_url_is_moonshot_open_platform() -> None:
    _, _, mock_openai = _build_provider()
    assert mock_openai.call_args.kwargs["base_url"] == KIMI_DEFAULT_BASE_URL


def test_kimi_overrides_sent_in_chat_kwargs() -> None:
    provider, client, _ = _build_provider(
        model="kimi-k2.6",
        max_tokens=8192,
        temperature=0.1,
        top_p=0.9,
    )
    provider.chat([{"role": "user", "content": "hello"}])
    sent = client.chat.completions.create.call_args.kwargs
    assert sent["max_tokens"] == 8192
    assert sent["temperature"] == 0.1
    assert sent["top_p"] == 0.9


def test_kimi_thinking_enabled_sends_standard_extra_body() -> None:
    provider, client, _ = _build_provider(
        model="kimi-for-coding",
        thinking="enabled",
        reasoning_effort="high",
    )
    provider.chat([{"role": "user", "content": "hello"}])
    sent = client.chat.completions.create.call_args.kwargs
    assert sent["extra_body"] == {"thinking": {"type": "enabled"}}
    assert sent["reasoning_effort"] == "high"


def test_kimi_stream_includes_top_p() -> None:
    chunk = SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content="ok", tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=None,
    )
    provider, client, _ = _build_provider()
    client.chat.completions.create.return_value = [chunk]

    gen = provider.stream([{"role": "user", "content": "hello"}])
    try:
        while True:
            next(gen)
    except StopIteration:
        pass

    sent = client.chat.completions.create.call_args.kwargs
    assert sent["stream"] is True
    assert sent["top_p"] == KIMI_DEFAULT_TOP_P
    assert sent["temperature"] == KIMI_DEFAULT_TEMPERATURE
