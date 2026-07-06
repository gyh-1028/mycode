"""Plugin support: controlled extension of tools, providers and skills.

Plugins are discovered via the ``mycode.plugins`` entry point group. They must
be explicitly enabled in config ``plugins`` or ``MYCODE_PLUGINS``; disabled
plugins are never imported.

A plugin entry point should be a callable that accepts a ``PluginRegistrar``::

    def register(registrar: PluginRegistrar) -> None:
        registrar.register_tool(...)
        registrar.register_provider("demo", DemoProvider)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any

from mycode.llm.base import BaseProvider, register_provider
from mycode.tools.registry import register as register_tool


@dataclass(frozen=True)
class PluginSpec:
    """Discovered plugin metadata."""

    name: str
    version: str
    api_version: str


class PluginRegistrar:
    """Controlled registration interface handed to each enabled plugin."""

    def __init__(self, spec: PluginSpec) -> None:
        self.spec = spec
        self.registered_tools: list[str] = []
        self.registered_providers: list[str] = []
        self.registered_skills: list[str] = []

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
    ) -> Callable[[Callable[..., str]], Callable[..., str]]:
        """Register a tool on behalf of the plugin."""

        def decorator(func: Callable[..., str]) -> Callable[..., str]:
            register_tool(name, description, parameters)(func)
            self.registered_tools.append(name)
            return func

        return decorator

    def register_provider(self, name: str, cls: type[BaseProvider]) -> None:
        """Register a provider on behalf of the plugin."""
        register_provider(name, cls)
        self.registered_providers.append(name)

    def register_skill(self, name: str) -> None:
        """Record that the plugin contributes a skill (actual skill files live on disk)."""
        self.registered_skills.append(name)


def _load_plugin_entry(name: str, ep: Any) -> PluginRegistrar:
    obj = ep.load()
    version = getattr(obj, "__version__", "unknown")
    api_version = getattr(obj, "API_VERSION", "1")
    registrar = PluginRegistrar(
        PluginSpec(name=name, version=version, api_version=api_version)
    )
    if callable(obj):
        obj(registrar)
    else:
        register_fn = getattr(obj, "register", None)
        if callable(register_fn):
            register_fn(registrar)
    return registrar


def load_enabled_plugins(enabled_names: list[str]) -> tuple[list[PluginSpec], list[str]]:
    """Load explicitly enabled plugins and return (loaded specs, missing names)."""
    specs: list[PluginSpec] = []
    missing: list[str] = []
    try:
        eps = entry_points(group="mycode.plugins")
    except Exception:  # pragma: no cover - defensive for older importlib.metadata
        eps = []
    by_name = {ep.name: ep for ep in eps}
    for name in enabled_names:
        ep = by_name.get(name)
        if ep is None:
            missing.append(name)
            continue
        try:
            registrar = _load_plugin_entry(name, ep)
        except Exception as exc:  # noqa: BLE001 - plugin errors should not crash CLI init
            raise RuntimeError(f"插件 {name} 加载失败:{exc}") from exc
        specs.append(registrar.spec)
    return specs, missing


def list_discovered_plugins() -> list[PluginSpec]:
    """List all plugins visible via entry points, regardless of enabled state."""
    try:
        eps = entry_points(group="mycode.plugins")
    except Exception:  # pragma: no cover
        eps = []
    specs: list[PluginSpec] = []
    for ep in eps:
        try:
            obj = ep.load()
            version = getattr(obj, "__version__", "unknown")
            api_version = getattr(obj, "API_VERSION", "1")
            specs.append(PluginSpec(name=ep.name, version=version, api_version=api_version))
        except Exception:  # noqa: BLE001 - listing should not crash on broken plugins
            specs.append(PluginSpec(name=ep.name, version="unknown", api_version="?"))
    return specs


def format_plugin_list(
    discovered: list[PluginSpec], enabled: set[str]
) -> str:
    lines = [f"发现 {len(discovered)} 个插件:"]
    for spec in discovered:
        marker = "[已启用]" if spec.name in enabled else "[未启用]"
        lines.append(
            f"  {marker} {spec.name} v{spec.version} (api v{spec.api_version})"
        )
    return "\n".join(lines)
