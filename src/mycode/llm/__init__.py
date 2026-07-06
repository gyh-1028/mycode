"""LLM provider layer: internal types, provider backends, and a factory."""

import inspect
from typing import TYPE_CHECKING

from mycode.llm.base import (
    BaseProvider,
    LLMResponse,
    ReasoningChunk,
    StopReason,
    ToolCall,
    Usage,
    get_provider_class,
    register_provider,
)
from mycode.llm.openai_compatible import OpenAICompatibleProvider

if TYPE_CHECKING:
    from mycode.config import Config


def build_provider(config: "Config", api_key: str) -> BaseProvider:
    """按 config.provider.type 选择并构建 provider。

    优先从 provider 注册表查找;未知的 type 仍回退到 OpenAI 兼容端点,
    保持与旧配置的向后兼容。Anthropic 模块只在真正需要时才导入。
    """
    ptype = (config.provider.type or "openai").strip().lower()
    cls = get_provider_class(ptype)
    if cls is None and ptype == "anthropic":
        # 触发类装饰器,把 AnthropicProvider 注册到注册表。
        import mycode.llm.anthropic as _  # noqa: F401

        cls = get_provider_class(ptype)
    if cls is None and ptype in {"kimi", "moonshot"}:
        # 触发类装饰器,把 KimiProvider 注册到注册表。
        import mycode.llm.kimi as _  # noqa: F401

        cls = get_provider_class(ptype)
    if cls is None:
        cls = OpenAICompatibleProvider

    fields = {
        "api_key": api_key,
        "model": config.default_model,
        "base_url": config.provider.base_url,
        "timeout": config.provider.timeout,
        "max_tokens": config.provider.max_tokens,
        "temperature": config.provider.temperature,
        "thinking": config.provider.thinking,
        "thinking_format": config.provider.thinking_format,
        "thinking_budget": config.provider.thinking_budget,
        "reasoning_effort": config.provider.reasoning_effort,
        "max_retries": config.provider.max_retries,
        "retry_backoff": config.provider.retry_backoff,
        "stream_usage": config.provider.stream_usage,
    }
    if cls.__init__ is object.__init__:
        return cls()  # type: ignore[reportCallIssue]

    sig = inspect.signature(cls.__init__)
    accepts_extra = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    allowed = {
        p.name for p in sig.parameters.values() if p.name != "self" and p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    kwargs = {k: v for k, v in fields.items() if v is not None and (accepts_extra or k in allowed)}
    return cls(**kwargs)


__all__ = [
    "BaseProvider",
    "LLMResponse",
    "ReasoningChunk",
    "StopReason",
    "ToolCall",
    "Usage",
    "OpenAICompatibleProvider",
    "build_provider",
    "register_provider",
]
