"""OpenAI-compatible provider.

Talks to any OpenAI-compatible Chat Completions endpoint via the ``openai`` SDK
(OpenAI itself, or DeepSeek via ``base_url="https://api.deepseek.com"``).
Tools always go through native function calling — the model returns structured
``tool_calls`` rather than writing calls into the message body.
"""

import json
import time
from collections.abc import Callable, Generator
from typing import Any

from openai import OpenAI

from mycode.llm.base import (
    BaseProvider,
    LLMResponse,
    ReasoningChunk,
    StopReason,
    StreamChunk,
    ToolCall,
    Usage,
    register_provider,
)

# OpenAI 的 finish_reason -> 内部 StopReason
_FINISH_REASON_MAP = {
    "tool_calls": StopReason.TOOL_CALLS,
    "function_call": StopReason.TOOL_CALLS,  # 兼容旧式 function_call
    "stop": StopReason.END_TURN,
    "length": StopReason.MAX_TOKENS,
}


def _map_finish_reason(finish_reason: str | None) -> str:
    return _FINISH_REASON_MAP.get(finish_reason or "", StopReason.OTHER)


def _to_openai_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """内部工具 schema -> OpenAI 原生 function-calling 格式。"""
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
        },
    }


def _parse_tool_calls(raw_tool_calls: Any) -> list[ToolCall]:
    """把返回的 message.tool_calls 解析成内部 ToolCall 列表。

    OpenAI 的 function.arguments 是 JSON 字符串,需 json.loads;解析失败或
    非对象时退化为空 dict,避免上层崩溃。
    """
    calls: list[ToolCall] = []
    for tc in raw_tool_calls or []:
        raw_args = tc.function.arguments or "{}"
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError:
            args = {}
        if not isinstance(args, dict):
            args = {}
        calls.append(ToolCall(id=tc.id, name=tc.function.name, args=args))
    return calls


def _assemble_tool_calls(tool_acc: dict[int, dict[str, str]]) -> list[ToolCall]:
    """把流式累积的工具调用片段(按 index)拼成完整 ToolCall。

    关键:arguments 是逐片到达的,必须先按 index 拼完整字符串,流结束后再
    json.loads —— 绝不能拿到一片就 parse。
    """
    calls: list[ToolCall] = []
    for index in sorted(tool_acc):
        slot = tool_acc[index]
        try:
            args = json.loads(slot["args"] or "{}")
        except json.JSONDecodeError:
            args = {}
        if not isinstance(args, dict):
            args = {}
        calls.append(ToolCall(id=slot["id"] or f"call_{index}", name=slot["name"], args=args))
    return calls


def _cached_tokens(raw_usage: Any) -> int:
    """提取命中缓存的输入 token,兼容 DeepSeek 与 OpenAI 两种字段。"""
    # DeepSeek:usage.prompt_cache_hit_tokens
    hit = getattr(raw_usage, "prompt_cache_hit_tokens", None)
    if hit is not None:
        return hit or 0
    # OpenAI:usage.prompt_tokens_details.cached_tokens
    details = getattr(raw_usage, "prompt_tokens_details", None)
    if details is not None:
        return getattr(details, "cached_tokens", 0) or 0
    return 0


def _parse_usage(raw_usage: Any) -> Usage:
    if raw_usage is None:
        return Usage()
    return Usage(
        prompt_tokens=getattr(raw_usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(raw_usage, "completion_tokens", 0) or 0,
        total_tokens=getattr(raw_usage, "total_tokens", 0) or 0,
        cached_tokens=_cached_tokens(raw_usage),
    )


@register_provider("openai")
@register_provider("deepseek")
class OpenAICompatibleProvider(BaseProvider):
    """通过 openai SDK 访问任意 OpenAI 兼容端点(如 DeepSeek)。"""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout: float = 60.0,
        max_tokens: int | None = None,
        temperature: float | None = None,
        thinking: str | None = None,
        thinking_format: str | None = None,
        thinking_budget: int | None = None,
        reasoning_effort: str | None = None,
        max_retries: int = 2,
        retry_backoff: float = 1.0,
        stream_usage: bool = True,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.thinking = thinking
        self.thinking_format = thinking_format or "standard"
        self.thinking_budget = thinking_budget
        self.reasoning_effort = reasoning_effort
        self.max_retries = max(0, max_retries)
        self.retry_backoff = max(0.0, retry_backoff)
        self.stream_usage = stream_usage
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    def _base_kwargs(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"model": self.model, "messages": messages}
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.reasoning_effort is not None and self.thinking_format in {"openai", "standard"}:
            kwargs["reasoning_effort"] = self.reasoning_effort
        if self.thinking_format == "qwen":
            extra_body: dict[str, Any] = {}
            if self.thinking is not None:
                extra_body["enable_thinking"] = self.thinking == "enabled"
            if self.thinking == "enabled" and self.thinking_budget is not None:
                extra_body["thinking_budget"] = self.thinking_budget
            if extra_body:
                kwargs["extra_body"] = extra_body
        elif self.thinking_format == "standard" and self.thinking is not None:
            kwargs["extra_body"] = {"thinking": {"type": self.thinking}}
        return kwargs

    def _with_retries(self, call: Callable[[], Any]) -> Any:
        for attempt in range(self.max_retries + 1):
            try:
                return call()
            except Exception:
                if attempt >= self.max_retries:
                    raise
                if self.retry_backoff:
                    time.sleep(self.retry_backoff * (2**attempt))
        raise RuntimeError("unreachable retry state")

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        kwargs = self._base_kwargs(messages)
        if tools:
            kwargs["tools"] = [_to_openai_tool(t) for t in tools]
            kwargs["tool_choice"] = "auto"

        completion = self._with_retries(lambda: self._client.chat.completions.create(**kwargs))
        choice = completion.choices[0]
        message = choice.message

        return LLMResponse(
            text=message.content,
            reasoning_content=getattr(message, "reasoning_content", None) or None,
            tool_calls=_parse_tool_calls(getattr(message, "tool_calls", None)),
            stop_reason=_map_finish_reason(choice.finish_reason),
            usage=_parse_usage(getattr(completion, "usage", None)),
        )

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Generator[StreamChunk, None, LLMResponse]:
        kwargs = self._base_kwargs(messages)
        kwargs["stream"] = True
        if self.stream_usage:
            # 让最后一帧带上 usage(OpenAI/DeepSeek 支持;部分兼容端点不支持,会降级)。
            kwargs["stream_options"] = {"include_usage": True}
        if tools:
            kwargs["tools"] = [_to_openai_tool(t) for t in tools]
            kwargs["tool_choice"] = "auto"

        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        # index -> 累积中的工具调用片段。arguments 是分片到达的,必须拼完整再解析。
        tool_acc: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        usage_raw: Any = None

        try:
            stream = self._with_retries(lambda: self._client.chat.completions.create(**kwargs))
        except Exception:
            if "stream_options" not in kwargs:
                raise
            fallback_kwargs = dict(kwargs)
            fallback_kwargs.pop("stream_options", None)
            stream = self._with_retries(lambda: self._client.chat.completions.create(**fallback_kwargs))

        for chunk in stream:
            if getattr(chunk, "usage", None) is not None:
                usage_raw = chunk.usage
            if not chunk.choices:
                continue  # 最后只带 usage 的那一帧 choices 为空
            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            delta = choice.delta
            if delta is None:
                continue
            if getattr(delta, "reasoning_content", None):
                rc = delta.reasoning_content
                reasoning_parts.append(rc)
                yield ReasoningChunk(rc)  # loop 会按思考链渲染
            if delta.content:
                text_parts.append(delta.content)
                yield delta.content  # 文本增量即时吐出
            for tc_delta in delta.tool_calls or []:
                slot = tool_acc.setdefault(tc_delta.index, {"id": "", "name": "", "args": ""})
                if tc_delta.id:
                    slot["id"] = tc_delta.id
                if tc_delta.function:
                    if tc_delta.function.name:
                        slot["name"] += tc_delta.function.name
                    if tc_delta.function.arguments:
                        slot["args"] += tc_delta.function.arguments

        return LLMResponse(
            text="".join(text_parts) or None,
            reasoning_content="".join(reasoning_parts) or None,
            tool_calls=_assemble_tool_calls(tool_acc),
            stop_reason=_map_finish_reason(finish_reason),
            usage=_parse_usage(usage_raw),
        )
