"""Tests for config loading and API-key resolution (Task 1)."""

import textwrap

import pytest

from mycode.config import (
    ConfigError,
    ProviderConfig,
    load_config,
    load_config_result,
    preset_config,
)


def test_defaults_when_file_missing(tmp_path) -> None:
    cfg = load_config(tmp_path / "nope.toml")
    assert cfg.default_model  # a non-empty default model
    assert cfg.max_steps == 20
    assert cfg.planning == "auto"
    assert cfg.planning_max_steps == 5
    assert cfg.max_file_lines == 1500
    assert cfg.max_command_output == 20000
    assert cfg.provider.api_key_env == "OPENAI_API_KEY"


def test_load_overrides_from_toml(tmp_path) -> None:
    p = tmp_path / "config.toml"
    p.write_text(
        textwrap.dedent(
            """
            default_model = "my-model"
            max_steps = 5

            [provider]
            api_key_env = "MY_KEY"
            base_url = "http://localhost:1234/v1"
            """
        ),
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.default_model == "my-model"
    assert cfg.max_steps == 5
    # Untouched fields keep their defaults.
    assert cfg.max_file_lines == 1500
    assert cfg.provider.api_key_env == "MY_KEY"
    assert cfg.provider.base_url == "http://localhost:1234/v1"


def test_resolve_api_key_present(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert ProviderConfig().resolve_api_key() == "sk-test"


def test_resolve_api_key_missing_raises(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ConfigError) as excinfo:
        ProviderConfig().resolve_api_key()
    assert "OPENAI_API_KEY" in str(excinfo.value)


def test_resolve_api_key_uses_configured_env_name(monkeypatch) -> None:
    monkeypatch.delenv("CUSTOM_KEY", raising=False)
    with pytest.raises(ConfigError) as excinfo:
        ProviderConfig(api_key_env="CUSTOM_KEY").resolve_api_key()
    assert "CUSTOM_KEY" in str(excinfo.value)


def test_malformed_toml_raises_configerror(tmp_path) -> None:
    p = tmp_path / "bad.toml"
    p.write_text("this is = = not valid", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)


def test_global_local_and_env_layering(tmp_path, monkeypatch) -> None:
    global_cfg = tmp_path / "global.toml"
    local_cfg = tmp_path / "project" / ".mycode" / "config.toml"
    local_cfg.parent.mkdir(parents=True)
    global_cfg.write_text(
        textwrap.dedent(
            """
            default_model = "global-model"
            max_steps = 3

            [provider]
            api_key_env = "GLOBAL_KEY"
            base_url = "https://global.example"
            """
        ),
        encoding="utf-8",
    )
    local_cfg.write_text(
        textwrap.dedent(
            """
            default_model = "local-model"

            [provider]
            api_key_env = "LOCAL_KEY"
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path / "project")
    monkeypatch.setattr("mycode.config.GLOBAL_CONFIG_PATH", global_cfg)
    monkeypatch.setenv("MYCODE_MAX_STEPS", "9")
    monkeypatch.setenv("MYCODE_PLANNING", "always")
    monkeypatch.setenv("MYCODE_PLANNING_MAX_STEPS", "3")

    result = load_config_result()

    assert [p.name for p in result.files] == ["global.toml", "config.toml"]
    assert result.config.default_model == "local-model"
    assert result.config.max_steps == 9
    assert result.config.planning == "always"
    assert result.config.planning_max_steps == 3
    assert result.config.provider.api_key_env == "LOCAL_KEY"
    assert result.config.provider.base_url == "https://global.example"
    assert "MYCODE_MAX_STEPS" in result.env_overrides
    assert "MYCODE_PLANNING" in result.env_overrides
    assert "MYCODE_PLANNING_MAX_STEPS" in result.env_overrides


def test_preset_config_deepseek_contains_key_env() -> None:
    text = preset_config("deepseek")
    assert 'default_model = "deepseek-v4-flash"' in text
    assert 'planning = "auto"' in text
    assert "planning_max_steps = 5" in text
    assert 'api_key_env = "DEEPSEEK_API_KEY"' in text


def test_preset_config_glm_contains_current_model_and_endpoint() -> None:
    text = preset_config("glm")
    assert 'default_model = "glm-5.2"' in text
    assert 'api_key_env = "ZAI_API_KEY"' in text
    assert 'base_url = "https://open.bigmodel.cn/api/paas/v4/"' in text


def test_preset_config_kimi_contains_key_env_and_base_url() -> None:
    text = preset_config("kimi")
    assert 'default_model = "kimi-k2.7-code"' in text
    assert 'api_key_env = "MOONSHOT_API_KEY"' in text
    assert 'base_url = "https://api.moonshot.cn/v1"' in text
    assert 'type = "kimi"' in text
    assert "max_tokens = 16384" in text
    assert "temperature = 0.2" in text
    assert "top_p = 0.95" in text


def test_provider_config_accepts_top_p() -> None:
    assert ProviderConfig(top_p=0.9).top_p == 0.9


def test_provider_config_rejects_out_of_range_top_p() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ProviderConfig(top_p=1.1)
    with pytest.raises(ValidationError):
        ProviderConfig(top_p=-0.1)
