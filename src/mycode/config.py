"""Configuration loading for mycode.

When called without an explicit path, config is layered in this order:

1. built-in defaults
2. user-global ``~/.mycode/config.toml``
3. project-local ``.mycode/config.toml``
4. active model profile from ``~/.mycode/models.toml``
5. ``MYCODE_*`` environment overrides

Passing an explicit path keeps the old test-friendly behavior: only that file is
loaded over defaults. API keys are stored in the operating-system keyring or
read from environment variables; they are never written to TOML config files.
"""

from __future__ import annotations

import dataclasses
import os
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from mycode.mcp.config import MCPConfig
from mycode.model_store import ModelStoreError
from mycode.trace import TraceConfig

LOCAL_CONFIG_PATH = Path(".mycode") / "config.toml"
GLOBAL_CONFIG_PATH = Path.home() / ".mycode" / "config.toml"
ENV_CONFIG_PATH = "MYCODE_CONFIG"

# Backwards-compatible public name used by older code/tests.
DEFAULT_CONFIG_PATH = LOCAL_CONFIG_PATH


class ConfigError(Exception):
    """配置或凭证相关的错误;消息本身即面向用户的中文提示。"""


class ProviderConfig(BaseModel):
    """LLM provider settings and references to external credentials."""

    type: str = "openai"
    profile: str | None = None
    api_key_env: str | None = "OPENAI_API_KEY"
    credential_id: str | None = None
    base_url: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    thinking: str | None = None
    thinking_format: str | None = None
    thinking_budget: int | None = None
    reasoning_effort: str | None = None
    timeout: float = 60.0
    max_retries: int = 2
    retry_backoff: float = 1.0
    stream_usage: bool = True

    def resolve_api_key(self) -> str:
        """Resolve an API key from environment first, then the OS keyring."""
        if self.api_key_env:
            key = os.environ.get(self.api_key_env, "").strip()
            if key:
                return key
        if self.credential_id:
            from mycode.model_store import CredentialStore, ModelStoreError

            try:
                key = CredentialStore().get(self.credential_id)
            except ModelStoreError as exc:
                raise ConfigError(str(exc)) from exc
            if key:
                return key
        env_hint = self.api_key_env or "(未配置)"
        profile_hint = self.profile or "当前模型"
        raise ConfigError(
            f"未检测到 API Key。模型配置档: {profile_hint}，环境变量: {env_hint}。\n"
            f"  推荐长期保存: mycode model key {profile_hint}\n"
            f'  CMD 临时设置: set "{env_hint}=sk-..."'
        )

    def api_key_source(self) -> str | None:
        if self.api_key_env and os.environ.get(self.api_key_env, "").strip():
            return "environment"
        if self.credential_id:
            try:
                from mycode.model_store import CredentialStore

                if CredentialStore().get(self.credential_id):
                    return "keyring"
            except Exception:  # noqa: BLE001 - display checks must not fail config loading
                return None
        return None


class PermissionsConfig(BaseModel):
    """工具写/执行操作的权限模式。"""

    write: str = "ask"
    command: str = "ask"
    mcp: str = "ask"


class PricingConfig(BaseModel):
    """USD per 1M tokens for a model; optional cache prices may override input."""

    input: float | None = None
    output: float | None = None
    cache_read: float | None = None
    cache_write: float | None = None


class CodeIntelConfig(BaseModel):
    """Local symbol index, LSP, and automatic context selection settings."""

    enabled: bool = True
    auto_context: bool = True
    max_context_tokens: int = Field(default=12_000, ge=0)
    max_context_fraction: float = Field(default=0.20, ge=0.0, le=0.50)
    max_files: int = Field(default=12, ge=1, le=100)
    max_chunks: int = Field(default=30, ge=1, le=200)
    lsp_timeout: float = Field(default=5.0, gt=0)
    language_servers: dict[str, list[str]] = Field(default_factory=dict)


class Config(BaseModel):
    """mycode 运行配置。字段缺省值即「无配置文件」时的默认行为。"""

    default_model: str = "gpt-4o-mini"
    max_steps: int = 20
    planning: Literal["auto", "always", "off"] = "auto"
    planning_max_steps: int = Field(default=5, ge=1, le=10)
    max_file_lines: int = 1500
    max_command_output: int = 20000
    context_limit: int = 65536
    command_timeout: int = 60
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    pricing: dict[str, PricingConfig] = Field(default_factory=dict)
    mcp: MCPConfig = Field(default_factory=lambda: MCPConfig())
    skills: list[str] = Field(default_factory=list)
    plugins: list[str] = Field(default_factory=list)
    trace: TraceConfig = Field(default_factory=TraceConfig)
    codeintel: CodeIntelConfig = Field(default_factory=CodeIntelConfig)


@dataclass(frozen=True)
class ConfigLoadResult:
    """Loaded config plus source metadata for diagnostics."""

    config: Config
    files: list[Path] = field(default_factory=list)
    env_overrides: list[str] = field(default_factory=list)
    explicit_path: Path | None = None


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"配置文件解析失败({path}):{exc}") from exc


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _set_nested(data: dict[str, Any], keys: tuple[str, ...], value: Any) -> None:
    node = data
    for key in keys[:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            child = {}
            node[key] = child
        node = child
    node[keys[-1]] = value


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_optional_str(value: str) -> str | None:
    stripped = value.strip()
    return stripped or None


def _parse_path(value: str) -> Path:
    return Path(value.strip()).expanduser()


def _parse_planning_mode(value: str) -> str:
    mode = value.strip().lower()
    if mode not in {"auto", "always", "off"}:
        raise ValueError("planning must be auto, always, or off")
    return mode


def _parse_comma_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_str_dict(value: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError(f"dict entry must be key=value: {item!r}")
        key, _, val = item.partition("=")
        result[key.strip()] = val.strip()
    return result


_ENV_OVERRIDES: dict[str, tuple[tuple[str, ...], Callable[[str], Any]]] = {
    "MYCODE_DEFAULT_MODEL": (("default_model",), str),
    "MYCODE_MAX_STEPS": (("max_steps",), int),
    "MYCODE_PLANNING": (("planning",), _parse_planning_mode),
    "MYCODE_PLANNING_MAX_STEPS": (("planning_max_steps",), int),
    "MYCODE_MAX_FILE_LINES": (("max_file_lines",), int),
    "MYCODE_MAX_COMMAND_OUTPUT": (("max_command_output",), int),
    "MYCODE_CONTEXT_LIMIT": (("context_limit",), int),
    "MYCODE_COMMAND_TIMEOUT": (("command_timeout",), int),
    "MYCODE_PROVIDER_TYPE": (("provider", "type"), str),
    "MYCODE_API_KEY_ENV": (("provider", "api_key_env"), _parse_optional_str),
    "MYCODE_BASE_URL": (("provider", "base_url"), _parse_optional_str),
    "MYCODE_MAX_TOKENS": (("provider", "max_tokens"), int),
    "MYCODE_TEMPERATURE": (("provider", "temperature"), float),
    "MYCODE_TOP_P": (("provider", "top_p"), float),
    "MYCODE_PROVIDER_TIMEOUT": (("provider", "timeout"), float),
    "MYCODE_MAX_RETRIES": (("provider", "max_retries"), int),
    "MYCODE_RETRY_BACKOFF": (("provider", "retry_backoff"), float),
    "MYCODE_STREAM_USAGE": (("provider", "stream_usage"), _parse_bool),
    "MYCODE_PERMISSION_WRITE": (("permissions", "write"), str),
    "MYCODE_PERMISSION_COMMAND": (("permissions", "command"), str),
    "MYCODE_PERMISSION_MCP": (("permissions", "mcp"), str),
    "MYCODE_SKILLS": (("skills",), _parse_comma_list),
    "MYCODE_PLUGINS": (("plugins",), _parse_comma_list),
    "MYCODE_TRACE": (("trace", "enabled"), _parse_bool),
    "MYCODE_TRACE_ENABLED": (("trace", "enabled"), _parse_bool),
    "MYCODE_TRACE_DIRECTORY": (("trace", "directory"), _parse_path),
    "MYCODE_TRACE_RECORD_PROMPTS": (("trace", "record_prompts"), _parse_bool),
    "MYCODE_TRACE_RECORD_TOOL_IO": (("trace", "record_tool_io"), _parse_bool),
    "MYCODE_TRACE_RECORD_OUTPUTS": (("trace", "record_outputs"), _parse_bool),
    "MYCODE_TRACE_OTLP_ENABLED": (("trace", "otlp_enabled"), _parse_bool),
    "MYCODE_TRACE_OTLP_ENDPOINT": (("trace", "otlp_endpoint"), _parse_optional_str),
    "MYCODE_TRACE_OTLP_HEADERS": (("trace", "otlp_headers"), _parse_str_dict),
    "MYCODE_CODEINTEL_ENABLED": (("codeintel", "enabled"), _parse_bool),
    "MYCODE_CODEINTEL_AUTO_CONTEXT": (("codeintel", "auto_context"), _parse_bool),
    "MYCODE_CODEINTEL_MAX_TOKENS": (("codeintel", "max_context_tokens"), int),
}


def _apply_env_overrides(data: dict[str, Any]) -> list[str]:
    applied: list[str] = []
    for env_name, (keys, parser) in _ENV_OVERRIDES.items():
        raw = os.environ.get(env_name)
        if raw is None:
            continue
        try:
            value = parser(raw)
        except ValueError as exc:
            raise ConfigError(f"环境变量 {env_name} 的值无效:{raw!r}") from exc
        _set_nested(data, keys, value)
        applied.append(env_name)
    return applied


def _build_config(data: dict[str, Any], source: str) -> Config:
    try:
        return Config(**data)
    except ValidationError as exc:
        raise ConfigError(f"配置字段无效({source}):\n{exc}") from exc


def load_config_result(path: Path | None = None) -> ConfigLoadResult:
    """加载配置并保留来源信息,供 CLI 诊断使用。"""
    data: dict[str, Any] = {}
    files: list[Path] = []

    if path is not None:
        if path.exists():
            data = _load_toml(path)
            files.append(path)
        return ConfigLoadResult(
            config=_build_config(data, str(path)),
            files=files,
            explicit_path=path,
        )

    env_config = os.environ.get(ENV_CONFIG_PATH)
    if env_config:
        cfg_path = Path(env_config).expanduser()
        if not cfg_path.exists():
            raise ConfigError(f"环境变量 {ENV_CONFIG_PATH} 指向的配置文件不存在:{cfg_path}")
        data = _load_toml(cfg_path)
        files.append(cfg_path)
    else:
        for cfg_path in (GLOBAL_CONFIG_PATH, LOCAL_CONFIG_PATH):
            if cfg_path.exists():
                data = _deep_merge(data, _load_toml(cfg_path))
                files.append(cfg_path)

    try:
        from mycode.model_store import active_profile_overlay

        model_overlay, model_path = active_profile_overlay()
    except ModelStoreError as exc:
        raise ConfigError(str(exc)) from exc
    if model_overlay:
        data = _deep_merge(data, model_overlay)
        if model_path is not None:
            files.append(model_path)

    env_overrides = _apply_env_overrides(data)
    source = " + ".join(str(p) for p in files) or "默认配置"
    return ConfigLoadResult(
        config=_build_config(data, source),
        files=files,
        env_overrides=env_overrides,
    )


def load_config(path: Path | None = None) -> Config:
    """加载配置;文件不存在时返回默认值,解析/校验失败时抛出 ConfigError。"""
    return load_config_result(path).config


def config_with_trace_overrides(
    config: Config,
    *,
    enabled: bool | None = None,
    directory: Path | None = None,
    record_prompts: bool | None = None,
    record_tool_io: bool | None = None,
    record_outputs: bool | None = None,
) -> Config:
    """Return a copy of *config* with the given trace overrides applied."""
    updates: dict[str, Any] = {}
    if enabled is not None:
        updates["enabled"] = enabled
    if directory is not None:
        updates["directory"] = directory
    if record_prompts is not None:
        updates["record_prompts"] = record_prompts
    if record_tool_io is not None:
        updates["record_tool_io"] = record_tool_io
    if record_outputs is not None:
        updates["record_outputs"] = record_outputs
    if not updates:
        return config
    trace = dataclasses.replace(config.trace, **updates)
    return config.model_copy(update={"trace": trace})


def preset_config(provider: str = "deepseek") -> str:
    """返回常见 provider 的 starter config。"""
    normalized = (provider or "deepseek").strip().lower()
    if normalized in {"deepseek", "ds"}:
        return """default_model = "deepseek-v4-flash"
max_steps = 20
planning = "auto"
planning_max_steps = 5
max_file_lines = 1500
max_command_output = 20000
command_timeout = 60

[provider]
type = "openai"
api_key_env = "DEEPSEEK_API_KEY"
base_url = "https://api.deepseek.com"
timeout = 60.0
max_retries = 2
retry_backoff = 1.0
stream_usage = true

[permissions]
write = "ask"
command = "ask"
"""
    if normalized in {"glm", "zhipu", "zai"}:
        return """default_model = "glm-5.2"
max_steps = 20
planning = "auto"
planning_max_steps = 5
max_file_lines = 1500
max_command_output = 20000
command_timeout = 60

[provider]
type = "openai"
api_key_env = "ZAI_API_KEY"
base_url = "https://open.bigmodel.cn/api/paas/v4/"
timeout = 60.0
max_retries = 2
retry_backoff = 1.0
stream_usage = true

[permissions]
write = "ask"
command = "ask"
"""
    if normalized in {"openai", "gpt"}:
        return """default_model = "gpt-4o-mini"
max_steps = 20
planning = "auto"
planning_max_steps = 5
max_file_lines = 1500
max_command_output = 20000
command_timeout = 60

[provider]
type = "openai"
api_key_env = "OPENAI_API_KEY"
timeout = 60.0
max_retries = 2
retry_backoff = 1.0
stream_usage = true

[permissions]
write = "ask"
command = "ask"
"""
    if normalized in {"anthropic", "claude"}:
        return """default_model = "claude-sonnet-4-6"
max_steps = 20
planning = "auto"
planning_max_steps = 5
max_file_lines = 1500
max_command_output = 20000
command_timeout = 60

[provider]
type = "anthropic"
api_key_env = "ANTHROPIC_API_KEY"
timeout = 60.0
max_retries = 2
retry_backoff = 1.0

[permissions]
write = "ask"
command = "ask"
"""
    if normalized in {"kimi", "moonshot"}:
        return """default_model = "kimi-k2.7-code"
max_steps = 20
planning = "auto"
planning_max_steps = 5
max_file_lines = 1500
max_command_output = 20000
command_timeout = 60

[provider]
type = "kimi"
api_key_env = "MOONSHOT_API_KEY"
base_url = "https://api.moonshot.cn/v1"
max_tokens = 16384
temperature = 0.2
top_p = 0.95
timeout = 60.0
max_retries = 2
retry_backoff = 1.0
stream_usage = true

[permissions]
write = "ask"
command = "ask"
"""
    raise ConfigError(f"未知 provider preset:{provider}。可用: deepseek / glm / openai / anthropic / kimi")
