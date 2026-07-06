"""Tests for plugin registrar and discovery."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from mycode.llm.base import BaseProvider, LLMResponse, StopReason, get_provider_class
from mycode.plugins import (
    PluginRegistrar,
    PluginSpec,
    list_discovered_plugins,
    load_enabled_plugins,
)
from mycode.tools import dispatch, get_schemas


def test_registrar_records_tool_registration() -> None:
    registrar = PluginRegistrar(PluginSpec(name="demo", version="1", api_version="1"))

    @registrar.register_tool(
        name="demo_hello",
        description="Say hello.",
        parameters={"type": "object", "properties": {}},
    )
    def demo_hello() -> str:
        return "hello from plugin"

    assert "demo_hello" in registrar.registered_tools
    schemas = {s["name"] for s in get_schemas()}
    assert "demo_hello" in schemas
    assert dispatch("demo_hello", {}) == "hello from plugin"


def test_registrar_records_provider_registration() -> None:
    registrar = PluginRegistrar(PluginSpec(name="demo", version="1", api_version="1"))

    class DemoProvider(BaseProvider):
        def chat(self, messages, tools=None):
            return LLMResponse(text="demo", stop_reason=StopReason.END_TURN)

    registrar.register_provider("demo", DemoProvider)
    assert "demo" in registrar.registered_providers
    assert get_provider_class("demo") is DemoProvider


def test_load_enabled_plugins_imports_and_returns_specs(monkeypatch) -> None:
    calls = []

    def fake_register(registrar: PluginRegistrar) -> None:
        calls.append(registrar.spec.name)

    fake_ep = SimpleNamespace(
        name="my-plugin",
        load=lambda: fake_register,
    )

    with patch("mycode.plugins.entry_points", return_value=[fake_ep]):
        specs, missing = load_enabled_plugins(["my-plugin", "missing"])

    assert specs == [PluginSpec(name="my-plugin", version="unknown", api_version="1")]
    assert missing == ["missing"]
    assert calls == ["my-plugin"]


def test_list_discovered_plugins_reads_metadata(monkeypatch) -> None:
    mod = SimpleNamespace(__version__="2.0.0", API_VERSION="1")
    fake_ep = SimpleNamespace(name="listed", load=lambda: mod)

    with patch("mycode.plugins.entry_points", return_value=[fake_ep]):
        specs = list_discovered_plugins()

    assert [s.name for s in specs] == ["listed"]
    assert specs[0].version == "2.0.0"
    assert specs[0].api_version == "1"


def test_load_enabled_plugins_raises_on_broken_plugin() -> None:
    fake_ep = SimpleNamespace(name="bad", load=MagicMock(side_effect=ImportError("nope")))

    with patch("mycode.plugins.entry_points", return_value=[fake_ep]):
        try:
            load_enabled_plugins(["bad"])
            raise AssertionError("expected RuntimeError")
        except RuntimeError as exc:
            assert "bad" in str(exc)
