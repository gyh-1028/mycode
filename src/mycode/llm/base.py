"""Internal, provider-agnostic LLM types and the provider interface.

Every concrete provider (OpenAI, DeepSeek, or any other OpenAI-compatible
endpoint) normalizes its wire response into these structures, so the rest of
mycode never has to know which backend produced a reply.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any, TypeAlias, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T", bound=type["BaseProvider"])

# Provider 注册表。新增 backend 只需在定义类时通过 ``register_provider`` 注册,
# ``build_provider`` 会自动按 ``config.provider.type`` 查找,无需修改工厂代码。
_PROVIDER_REGISTRY: dict[str, type["BaseProvider"]] = {}


@dataclass(frozen=True)
class ReasoningChunk:
    """A streamed reasoning delta kept separate from final answer text."""

    content: str


StreamChunk: TypeAlias = str | ReasoningChunk


def register_provider(name: str, cls: T | None = None) -> T | Callable[[T], T]:
    """把 BaseProvider 子类注册到 provider 发现表。

    既可用作装饰器 ``@register_provider("xxx")``,也可函数式调用
    ``register_provider("xxx", MyProvider)``。
    """

    normalized = name.strip().lower()
    if not normalized:
        raise ValueError("provider name cannot be empty")

    def decorator(provider_cls: T) -> T:
        if not issubclass(provider_cls, BaseProvider):
            raise TypeError("registered provider must inherit BaseProvider")
        _PROVIDER_REGISTRY[normalized] = provider_cls
        return provider_cls

    if cls is None:
        return decorator
    return decorator(cls)


def get_provider_class(name: str) -> type["BaseProvider"] | None:
    return _PROVIDER_REGISTRY.get(name.lower())


class StopReason:
    """归一化后的停止原因(provider 的 finish_reason 映射到这里)。"""

    TOOL_CALLS = "tool_calls"  # 模型要求调用工具,需执行后把结果回灌
    END_TURN = "end_turn"      # 模型正常结束本轮
    MAX_TOKENS = "max_tokens"  # 触达长度上限被截断
    OTHER = "other"            # 其它/未知原因


class ToolCall(BaseModel):
    """一次工具调用请求。args 已从 provider 的 JSON 字符串解析为 dict。"""

    id: str
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class Usage(BaseModel):
    """token 用量统计。

    cached_tokens:本轮输入里命中 provider 端缓存的 token 数(省下的)。
    cache_write_tokens:写入缓存的 token 数(如 Anthropic 的 cache_creation,通常首轮)。
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0


class LLMResponse(BaseModel):
    """一次 chat 调用的归一化结果。

    ``reasoning_content`` 用于承载 DeepSeek R1 这类推理模型的思考链;它只在输出
    阶段展示给用户,不会被追加到会话 messages 中。
    """

    text: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    stop_reason: str = StopReason.OTHER
    usage: Usage = Field(default_factory=Usage)


class BaseProvider(ABC):
    """LLM provider 抽象基类。"""

    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """发送一轮对话,返回归一化的 LLMResponse。

        messages: 内部消息列表(每项含 role/content 等)。
        tools:    内部工具 schema 列表,每项形如
                  ``{"name", "description", "parameters"}``;provider 负责转成
                  各自的原生 function-calling 格式。传 None 表示本轮不带工具。
        """
        raise NotImplementedError

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Generator[StreamChunk, None, LLMResponse]:
        """流式对话:逐段 yield 文本增量,生成器结束时 return 完整 LLMResponse。

        默认实现退化为非流式:调用 chat() 后一次性 yield 全部文本。支持真正增量
        流式的 provider 应覆盖本方法。用法::

            gen = provider.stream(messages, tools)
            try:
                while True:
                    print(next(gen), end="")
            except StopIteration as stop:
                response = stop.value
        """
        response = self.chat(messages, tools)
        if response.reasoning_content:
            yield ReasoningChunk(response.reasoning_content)
        if response.text:
            yield response.text
        return response
