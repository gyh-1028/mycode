"""Provider registry / discovery tests."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from mycode.config import Config, ProviderConfig
from mycode.llm import build_provider, register_provider
from mycode.llm.anthropic import AnthropicProvider
from mycode.llm.base import BaseProvider, LLMResponse, StopReason
from mycode.llm.kimi import KimiProvider
from mycode.llm.openai_compatible import OpenAICompatibleProvider


def _make_config(ptype="openai", base_url=None):
    return Config(
        default_model="m",
        provider=ProviderConfig(
            type=ptype,
            api_key_env="K",
            base_url=base_url,
            timeout=30,
            max_retries=1,
            retry_backoff=0,
        ),
    )


def test_openai_type_resolves_to_openai_compatible() -> None:
    with patch("mycode.llm.openai_compatible.OpenAI") as mock_openai:
        client = MagicMock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="hi", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )
        mock_openai.return_value = client
        provider = build_provider(_make_config("openai"), api_key="k")
    assert isinstance(provider, OpenAICompatibleProvider)


def test_anthropic_type_resolves_to_anthropic() -> None:
    with patch("mycode.llm.anthropic.anthropic.Anthropic") as mock_anthropic:
        client = MagicMock()
        client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="hi")],
            stop_reason="end_turn",
            usage=SimpleNamespace(
                input_tokens=1, output_tokens=1,
                cache_read_input_tokens=0, cache_creation_input_tokens=0,
            ),
        )
        mock_anthropic.return_value = client
        provider = build_provider(_make_config("anthropic"), api_key="k")
    assert isinstance(provider, AnthropicProvider)


def test_unknown_type_falls_back_to_openai_compatible() -> None:
    with patch("mycode.llm.openai_compatible.OpenAI") as mock_openai:
        client = MagicMock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="hi", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )
        mock_openai.return_value = client
        provider = build_provider(_make_config("some-new-provider"), api_key="k")
    assert isinstance(provider, OpenAICompatibleProvider)


def test_kimi_type_resolves_to_kimi_provider() -> None:
    with patch("mycode.llm.openai_compatible.OpenAI") as mock_openai:
        client = MagicMock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="hi", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )
        mock_openai.return_value = client
        provider = build_provider(_make_config("kimi"), api_key="k")
    assert isinstance(provider, KimiProvider)
    assert provider.top_p == 0.95


def test_register_provider_decorator_adds_to_registry() -> None:
    @register_provider("demo")
    class DemoProvider(BaseProvider):
        def chat(self, messages, tools=None):
            return LLMResponse(text="demo", stop_reason=StopReason.END_TURN)

    provider = build_provider(_make_config("demo"), api_key="k")
    assert isinstance(provider, DemoProvider)
    assert provider.chat([]).text == "demo"


def test_registered_provider_with_kwargs_receives_standard_config() -> None:
    @register_provider("kwargs-demo")
    class KwargsProvider(BaseProvider):
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def chat(self, messages, tools=None):
            return LLMResponse(text="demo", stop_reason=StopReason.END_TURN)

    provider = build_provider(
        _make_config("kwargs-demo", base_url="https://example.invalid"),
        api_key="k",
    )

    assert isinstance(provider, KwargsProvider)
    assert provider.kwargs["api_key"] == "k"
    assert provider.kwargs["model"] == "m"
    assert provider.kwargs["base_url"] == "https://example.invalid"


def test_register_provider_rejects_invalid_registration() -> None:
    with pytest.raises(ValueError):
        register_provider("   ")
    with pytest.raises(TypeError):
        register_provider("bad", object)  # type: ignore[arg-type]
