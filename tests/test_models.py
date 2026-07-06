from __future__ import annotations

import sys
from types import SimpleNamespace

from typer.testing import CliRunner

from mycode.cli import app
from mycode.config import ProviderConfig, load_config_result
from mycode.model_store import MODEL_CATALOGS, PRESETS, CredentialStore, ModelProfile, ModelStore, profile_from_preset

runner = CliRunner()


def test_model_store_roundtrip_and_active_overlay(tmp_path, monkeypatch) -> None:
    path = tmp_path / "models.toml"
    monkeypatch.setenv("MYCODE_MODELS_FILE", str(path))
    store = ModelStore.load()
    store.add(profile_from_preset("deepseek", profile_name="work"))
    store.add(profile_from_preset("anthropic", profile_name="claude"))
    store.use("claude")

    loaded = ModelStore.load()
    assert loaded.active == "claude"
    assert loaded.profiles["work"].model == "deepseek-v4-flash"
    assert "sk-" not in path.read_text(encoding="utf-8")

    config = load_config_result().config
    assert config.provider.profile == "claude"
    assert config.provider.type == "anthropic"
    assert config.default_model == "claude-sonnet-5"


def test_provider_key_environment_has_priority(monkeypatch) -> None:
    monkeypatch.setenv("TEST_MODEL_KEY", "env-key")
    monkeypatch.setattr(CredentialStore, "get", lambda self, credential_id: "vault-key")
    provider = ProviderConfig(api_key_env="TEST_MODEL_KEY", credential_id="profile:test")
    assert provider.resolve_api_key() == "env-key"
    assert provider.api_key_source() == "environment"


def test_provider_key_falls_back_to_system_keyring(monkeypatch) -> None:
    monkeypatch.delenv("TEST_MODEL_KEY", raising=False)
    monkeypatch.setattr(CredentialStore, "get", lambda self, credential_id: "vault-key")
    provider = ProviderConfig(api_key_env="TEST_MODEL_KEY", credential_id="profile:test")
    assert provider.resolve_api_key() == "vault-key"
    assert provider.api_key_source() == "keyring"


def test_credential_store_uses_keyring_without_plaintext_file(monkeypatch) -> None:
    values = {}
    fake = SimpleNamespace(
        set_password=lambda service, username, password: values.__setitem__((service, username), password),
        get_password=lambda service, username: values.get((service, username)),
        delete_password=lambda service, username: values.pop((service, username), None),
    )
    monkeypatch.setitem(sys.modules, "keyring", fake)
    credentials = CredentialStore("test-service")
    credentials.set("profile:demo", "secret")
    assert credentials.get("profile:demo") == "secret"
    assert credentials.delete("profile:demo")


def test_model_cli_add_list_switch_update_key_and_remove(tmp_path, monkeypatch) -> None:
    path = tmp_path / "models.toml"
    monkeypatch.setenv("MYCODE_MODELS_FILE", str(path))
    secrets = {}
    monkeypatch.setattr(CredentialStore, "set", lambda self, key, value: secrets.__setitem__(key, value))
    monkeypatch.setattr(CredentialStore, "get", lambda self, key: secrets.get(key))
    monkeypatch.setattr(CredentialStore, "delete", lambda self, key: secrets.pop(key, None) is not None)

    added = runner.invoke(app, ["model", "add", "ds", "--preset", "deepseek"], input="token-one\ntoken-one\n")
    assert added.exit_code == 0
    assert secrets["profile:ds"] == "token-one"

    second = runner.invoke(app, ["model", "add", "oa", "--preset", "openai", "--no-key"])
    assert second.exit_code == 0
    switched = runner.invoke(app, ["model", "use", "oa"])
    assert switched.exit_code == 0
    assert ModelStore.load().active == "oa"

    updated = runner.invoke(app, ["model", "key", "oa"], input="token-two\ntoken-two\n")
    assert updated.exit_code == 0
    assert secrets["profile:oa"] == "token-two"

    listing = runner.invoke(app, ["model", "list"])
    assert listing.exit_code == 0
    assert "* oa" in listing.output
    assert "system-keyring" in listing.output

    removed = runner.invoke(app, ["model", "remove", "oa", "--force"])
    assert removed.exit_code == 0
    assert "oa" not in ModelStore.load().profiles


def test_custom_model_profile_validation(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MYCODE_MODELS_FILE", str(tmp_path / "models.toml"))
    result = runner.invoke(
        app,
        [
            "model",
            "add",
            "local",
            "--provider",
            "openai",
            "--model",
            "custom-model",
            "--base-url",
            "http://localhost:8000/v1",
            "--no-key",
        ],
    )
    assert result.exit_code == 0
    profile = ModelStore.load().profiles["local"]
    assert profile == ModelProfile(
        name="local",
        provider="openai",
        model="custom-model",
        base_url="http://localhost:8000/v1",
        credential_id="profile:local",
    )


def test_model_store_persists_thinking_options(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MYCODE_MODELS_FILE", str(tmp_path / "models.toml"))
    store = ModelStore.load()
    store.add(
        ModelProfile(
            name="deepseek-pro",
            provider="openai",
            model="deepseek-v4-pro",
            thinking="enabled",
            thinking_format="standard",
            thinking_budget=8192,
            reasoning_effort="max",
        )
    )

    profile = ModelStore.load().profiles["deepseek-pro"]
    assert profile.thinking == "enabled"
    assert profile.thinking_format == "standard"
    assert profile.thinking_budget == 8192
    assert profile.reasoning_effort == "max"
    assert profile.provider_overlay()["provider"]["thinking"] == "enabled"


def test_catalogs_separate_kimi_coding_plan_from_open_platform() -> None:
    catalogs = {item["id"]: item for item in MODEL_CATALOGS}

    coding = catalogs["kimi-coding"]
    open_platform = catalogs["kimi"]
    assert coding["baseUrl"] == "https://api.kimi.com/coding/v1"
    assert coding["defaultModel"] == "kimi-for-coding"
    assert coding["provider"] == "kimi"
    assert coding["reasoningEfforts"] == ["low", "high"]
    assert coding["models"][0]["thinking"] == "enabled"
    assert coding["apiKeyEnv"] != open_platform["apiKeyEnv"]
    assert open_platform["baseUrl"] == "https://api.moonshot.cn/v1"
    assert open_platform["provider"] == "kimi"
    assert open_platform["defaultModel"] == "kimi-k2.7-code"
    assert any(model["id"] == "kimi-k2.7-code" for model in open_platform["models"])
    assert {model["id"] for model in open_platform["models"]} >= {"kimi-k2.6", "kimi-k2.5"}


def test_catalogs_cover_supported_model_families() -> None:
    assert {catalog["id"] for catalog in MODEL_CATALOGS} == {
        "anthropic",
        "deepseek",
        "gemini",
        "glm",
        "kimi",
        "kimi-coding",
        "minimax",
        "openai",
        "qwen",
    }


def test_kimi_presets_carry_coding_defaults() -> None:
    for name in ("kimi", "kimi-coding"):
        preset = PRESETS[name]
        assert preset.provider == "kimi"
        assert preset.max_tokens == 16384
        assert preset.temperature == 0.2
        assert preset.top_p == 0.95
    assert PRESETS["kimi"].model == "kimi-k2.7-code"
    assert PRESETS["kimi-coding"].model == "kimi-for-coding"
    assert PRESETS["kimi-coding"].thinking == "enabled"
