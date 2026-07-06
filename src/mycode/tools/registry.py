"""Tool registry: register tools, expose their schemas, dispatch by name.

A tool is a plain Python function returning a string. The registry records
``{name, description, parameters(JSON schema), func}``. ``get_schemas()`` feeds
the provider (internal ``{name, description, parameters}`` shape); ``dispatch``
runs a tool and — per the project convention — turns *any* failure into a
``错误:``-prefixed string instead of raising, so the agent loop never crashes
and the model can read the error and retry.

``dispatch_tool`` is the structured sibling introduced in P5: it returns a
:class:`ToolResult` carrying the content, an error flag and a stable error
signature, so the runner / trace can reason about tool outcomes without
re-parsing strings. The legacy ``dispatch`` is retained verbatim for callers
that expect a plain string.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema
    func: Callable[..., str]


@dataclass(frozen=True)
class ToolResult:
    """Structured outcome of a tool call.

    ``content`` is always a string (errors are ``错误:...``-prefixed, matching
    the legacy convention) so existing callers keep working. ``is_error`` and
    ``error_signature`` let the runner classify stuck loops and record traces
    without re-parsing the content.
    """

    name: str
    args: dict[str, Any]
    content: str
    is_error: bool = False
    error_signature: str | None = None


_REGISTRY: dict[str, Tool] = {}


def register(
    name: str, description: str, parameters: dict[str, Any]
) -> Callable[[Callable[..., str]], Callable[..., str]]:
    """装饰器:把一个返回字符串的函数登记为工具。"""

    def decorator(func: Callable[..., str]) -> Callable[..., str]:
        if name in _REGISTRY:
            raise ValueError(f"工具重复注册:{name}")
        _REGISTRY[name] = Tool(
            name=name, description=description, parameters=parameters, func=func
        )
        return func

    return decorator


def get_tool(name: str) -> Tool | None:
    return _REGISTRY.get(name)


def register_dynamic(
    name: str,
    description: str,
    parameters: dict[str, Any],
    func: Callable[..., str],
) -> None:
    """Register a tool programmatically (no decorator).

    Used by the MCP client and plugin system to register tools discovered at
    runtime. Raises ValueError on name collision, matching ``register``.
    """
    if name in _REGISTRY:
        raise ValueError(f"工具重复注册:{name}")
    _REGISTRY[name] = Tool(
        name=name, description=description, parameters=parameters, func=func
    )


def unregister(name: str) -> bool:
    """Remove a tool from the registry. Returns True if it existed."""
    return _REGISTRY.pop(name, None) is not None


def get_schemas() -> list[dict[str, Any]]:
    """返回内部工具 schema 列表 {name, description, parameters},喂给 provider。"""
    return [
        {"name": t.name, "description": t.description, "parameters": t.parameters}
        for t in _REGISTRY.values()
    ]


def dispatch(name: str, args: dict[str, Any]) -> str:
    """按名执行工具;未知工具、参数错误、执行异常都返回「错误:」字符串。"""
    return dispatch_tool(name, args).content


def _classify_error(content: str) -> str | None:
    """Extract a stable error signature from a tool result string.

    Mirrors the heuristic previously embedded in the agent loop so stuck-loop
    detection keeps the same semantics. Returns None for non-error results.
    """
    r = content.strip()
    if r.startswith("错误:"):
        return r
    if r.startswith("[退出码 ") and not r.startswith("[退出码 0]"):
        return r
    return None


def dispatch_tool(name: str, args: dict[str, Any]) -> ToolResult:
    """按名执行工具,返回结构化 ToolResult。

    未知工具、参数错误、执行异常都体现在 ``is_error=True`` 的结果里(content 仍
    为「错误:」前缀字符串),绝不抛异常,保持 agent 循环不会因工具而崩溃。
    """
    tool = _REGISTRY.get(name)
    if tool is None:
        available = ", ".join(sorted(_REGISTRY)) or "(无)"
        content = f"错误:未知工具 {name!r}。可用工具:{available}"
        return ToolResult(name=name, args=dict(args), content=content, is_error=True, error_signature=content)
    if not isinstance(args, dict):
        content = f"错误:工具 {name} 的参数必须是对象,实际为 {type(args).__name__}"
        return ToolResult(name=name, args={}, content=content, is_error=True, error_signature=content)
    try:
        result = tool.func(**args)
    except TypeError as exc:
        # 参数绑定失败(缺少必填项 / 多了未知参数)。
        content = f"错误:调用工具 {name} 的参数有误:{exc}"
        return ToolResult(name=name, args=dict(args), content=content, is_error=True, error_signature=content)
    except Exception as exc:  # noqa: BLE001 - 兜底:工具不应抛异常,但避免拖垮 agent 循环
        content = f"错误:工具 {name} 执行失败:{type(exc).__name__}: {exc}"
        return ToolResult(name=name, args=dict(args), content=content, is_error=True, error_signature=content)
    content = result if isinstance(result, str) else str(result)
    sig = _classify_error(content)
    return ToolResult(
        name=name,
        args=dict(args),
        content=content,
        is_error=sig is not None,
        error_signature=sig,
    )
