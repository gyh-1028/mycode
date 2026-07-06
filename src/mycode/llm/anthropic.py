"""Anthropic (Claude) provider — the abstraction's health check.

Implements BaseProvider for the Anthropic Messages API. The rest of mycode
(agent loop, session persistence, context compaction) is UNCHANGED: this
provider alone converts the internal OpenAI-shaped message list into Anthropic's
wire format and normalizes the reply back into the shared LLMResponse.

Anthropic specifics handled here:
- ``system`` is a top-level parameter, not a message with role "system".
- Tools are ``{name, description, input_schema}`` (not OpenAI's nested function).
- A tool call is a ``tool_use`` content block (id / name / input — input is
  already a dict); ``stop_reason == "tool_use"`` means run the tools.
- Tool results go back as ``tool_result`` blocks inside a *user* message, with
  ``tool_use_id`` matching the originating ``tool_use`` id. Consecutive internal
  ``tool`` messages are merged into one user message.
"""

import json
import time
from collections.abc import Callable, Generator
from typing import Any

import anthropic

from mycode.llm.base import (
    BaseProvider,
    LLMResponse,
    StopReason,
    ToolCall,
    Usage,
    register_provider,
)

# Anthropic stop_reason -> 内部 StopReason
_STOP_REASON_MAP = {
    "tool_use": StopReason.TOOL_CALLS,
    "end_turn": StopReason.END_TURN,
    "max_tokens": StopReason.MAX_TOKENS,
}


def _map_stop_reason(stop_reason: str | None) -> str:
    return _STOP_REASON_MAP.get(stop_reason or "", StopReason.OTHER)


def _to_anthropic_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """内部工具 schema {name, description, parameters} -> Anthropic {name, description, input_schema}。"""
    if not tools:
        return None
    return [
        {
            "name": t["name"],
            "description": t.get("description", ""),
            "input_schema": t.get("parameters", {"type": "object", "properties": {}}),
        }
        for t in tools
    ]


def _parse_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            value = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
    else:
        value = raw or {}
    return value if isinstance(value, dict) else {}


def _to_anthropic_messages(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """内部(OpenAI 形态)messages -> (system 字符串, Anthropic messages 列表)。"""
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []
    pending_results: list[dict[str, Any]] = []

    def flush() -> None:
        if pending_results:
            out.append({"role": "user", "content": list(pending_results)})
            pending_results.clear()

    for m in messages:
        role = m.get("role")
        if role == "system":
            if m.get("content"):
                system_parts.append(m["content"])
            continue
        if role == "tool":
            # tool 结果回填进随后的 user 消息(tool_result 块,tool_use_id 对上)。
            pending_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": m.get("tool_call_id", ""),
                    "content": m.get("content") or "",
                }
            )
            continue

        flush()  # 任何非 tool 消息之前,先把累积的 tool 结果作为一条 user 消息落定
        if role == "user":
            out.append({"role": "user", "content": m.get("content") or ""})
        elif role == "assistant":
            tool_calls = m.get("tool_calls") or []
            if not tool_calls:
                # 纯文本回复:直接用字符串 content。
                out.append({"role": "assistant", "content": m.get("content") or ""})
                continue
            # 含工具调用:用内容块(可选 text 块在前 + 每个 tool_use 块)。
            blocks: list[dict[str, Any]] = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in tool_calls:
                fn = tc.get("function", {})
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "input": _parse_args(fn.get("arguments")),
                    }
                )
            out.append({"role": "assistant", "content": blocks})

    flush()
    system = "\n\n".join(system_parts) if system_parts else None
    return system, out


def _normalize(message: Any) -> LLMResponse:
    """Anthropic Message -> 内部归一化 LLMResponse。"""
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in message.content:
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(block.text)
        elif btype == "tool_use":
            args = block.input if isinstance(block.input, dict) else {}
            tool_calls.append(ToolCall(id=block.id, name=block.name, args=args))

    usage = getattr(message, "usage", None)
    in_tok = getattr(usage, "input_tokens", 0) or 0
    out_tok = getattr(usage, "output_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return LLMResponse(
        text="".join(text_parts) or None,
        tool_calls=tool_calls,
        stop_reason=_map_stop_reason(getattr(message, "stop_reason", None)),
        usage=Usage(
            prompt_tokens=in_tok,
            completion_tokens=out_tok,
            total_tokens=in_tok + out_tok,
            cached_tokens=cache_read,
            cache_write_tokens=cache_write,
        ),
    )


@register_provider("anthropic")
class AnthropicProvider(BaseProvider):
    """通过 anthropic SDK 访问 Claude(Messages API)。"""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_tokens: int = 8192,
        timeout: float = 60.0,
        temperature: float | None = None,
        thinking: str | None = None,
        thinking_format: str | None = None,
        reasoning_effort: str | None = None,
        max_retries: int = 2,
        retry_backoff: float = 1.0,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.thinking = thinking
        self.thinking_format = thinking_format
        self.reasoning_effort = reasoning_effort
        self.max_retries = max(0, max_retries)
        self.retry_backoff = max(0.0, retry_backoff)
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout)

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

    def _build_kwargs(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
    ) -> dict[str, Any]:
        system, anth_messages = _to_anthropic_messages(messages)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": anth_messages,
        }
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.thinking_format == "anthropic" and self.thinking is not None:
            kwargs["thinking"] = {
                "type": "adaptive" if self.thinking == "enabled" else "disabled"
            }
        if self.reasoning_effort is not None:
            kwargs["output_config"] = {"effort": self.reasoning_effort}
        if system:
            # 在 system(含拼接的 MYCODE.md)末尾打缓存断点 —— 多轮里这段稳定前缀
            # (以及它之前渲染的 tools)会命中缓存。
            kwargs["system"] = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        anth_tools = _to_anthropic_tools(tools)
        if anth_tools:
            # 工具定义末尾也打一个断点,让工具块独立缓存。
            anth_tools[-1] = {**anth_tools[-1], "cache_control": {"type": "ephemeral"}}
            kwargs["tools"] = anth_tools
        return kwargs

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        kwargs = self._build_kwargs(messages, tools)
        message = self._with_retries(lambda: self._client.messages.create(**kwargs))
        return _normalize(message)

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Generator[str, None, LLMResponse]:
        # SDK 的 messages.stream() 会自动累积 tool_use 的 input_json 分片,
        # get_final_message() 给出装配好的完整 Message。
        kwargs = self._build_kwargs(messages, tools)
        with self._with_retries(lambda: self._client.messages.stream(**kwargs)) as stream:
            yield from stream.text_stream
            final = stream.get_final_message()
        return _normalize(final)
