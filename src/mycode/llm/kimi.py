"""Dedicated provider for Kimi (Moonshot AI) endpoints.

Kimi exposes an OpenAI-compatible Chat Completions API, so this provider inherits
the bulk of the wire protocol logic from `OpenAICompatibleProvider` and only
injects Kimi-specific coding defaults (`top_p`, lower temperature, larger
`max_tokens`) and a dedicated provider identity.
"""

from typing import Any

from mycode.llm.base import register_provider
from mycode.llm.openai_compatible import OpenAICompatibleProvider

KIMI_DEFAULT_BASE_URL = "https://api.moonshot.cn/v1"
KIMI_DEFAULT_TEMPERATURE = 0.2
KIMI_DEFAULT_TOP_P = 0.95
KIMI_DEFAULT_MAX_TOKENS = 16384


@register_provider("kimi")
@register_provider("moonshot")
class KimiProvider(OpenAICompatibleProvider):
    """通过 openai SDK 访问 Kimi / Moonshot 的 OpenAI 兼容端点。

    为编码场景优化了默认参数：低 temperature、固定 top_p、更大的 max_tokens，
    同时复用父类的工具调用、流式输出和用量解析。
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = KIMI_DEFAULT_BASE_URL,
        timeout: float = 60.0,
        max_tokens: int = KIMI_DEFAULT_MAX_TOKENS,
        temperature: float = KIMI_DEFAULT_TEMPERATURE,
        top_p: float = KIMI_DEFAULT_TOP_P,
        thinking: str | None = None,
        thinking_format: str | None = None,
        thinking_budget: int | None = None,
        reasoning_effort: str | None = None,
        max_retries: int = 2,
        retry_backoff: float = 1.0,
        stream_usage: bool = True,
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout=timeout,
            max_tokens=max_tokens,
            temperature=temperature,
            thinking=thinking,
            thinking_format=thinking_format,
            thinking_budget=thinking_budget,
            reasoning_effort=reasoning_effort,
            max_retries=max_retries,
            retry_backoff=retry_backoff,
            stream_usage=stream_usage,
        )
        self.top_p = top_p

    def _base_kwargs(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        kwargs = super()._base_kwargs(messages)
        kwargs["top_p"] = self.top_p
        return kwargs
