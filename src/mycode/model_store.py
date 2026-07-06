"""Persistent model profiles and OS-backed API credentials."""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, cast

MODEL_STORE_PATH = Path.home() / ".mycode" / "models.toml"
MODEL_STORE_ENV = "MYCODE_MODELS_FILE"
KEYRING_SERVICE = "mycode-ai-cli"
_PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class ModelStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelProfile:
    name: str
    provider: str
    model: str
    base_url: str | None = None
    api_key_env: str | None = None
    credential_id: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    thinking: str | None = None
    thinking_format: str | None = None
    thinking_budget: int | None = None
    reasoning_effort: str | None = None

    def provider_overlay(self) -> dict[str, Any]:
        provider: dict[str, Any] = {"type": self.provider, "profile": self.name}
        for key in (
            "base_url",
            "api_key_env",
            "credential_id",
            "max_tokens",
            "temperature",
            "top_p",
            "thinking",
            "thinking_format",
            "thinking_budget",
            "reasoning_effort",
        ):
            value = getattr(self, key)
            if value is not None:
                provider[key] = value
        return {"default_model": self.model, "provider": provider}


@dataclass
class ModelStore:
    active: str | None = None
    profiles: dict[str, ModelProfile] = field(default_factory=dict)
    path: Path = MODEL_STORE_PATH

    @classmethod
    def load(cls, path: Path | None = None) -> ModelStore:
        env_path = os.environ.get(MODEL_STORE_ENV)
        resolved = (path or (Path(env_path) if env_path else MODEL_STORE_PATH)).expanduser()
        if not resolved.is_file():
            return cls(path=resolved)
        try:
            with resolved.open("rb") as handle:
                data = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ModelStoreError(f"模型配置档无法读取: {resolved}: {exc}") from exc
        profiles: dict[str, ModelProfile] = {}
        for name, raw in (data.get("profiles") or {}).items():
            if not isinstance(raw, dict):
                raise ModelStoreError(f"模型配置档 {name!r} 必须是 TOML table")
            profiles[name] = ModelProfile(
                name=name,
                provider=str(raw.get("provider", "openai")),
                model=str(raw.get("model", "")),
                base_url=_optional_str(raw.get("base_url")),
                api_key_env=_optional_str(raw.get("api_key_env")),
                credential_id=_optional_str(raw.get("credential_id")),
                max_tokens=_optional_int(raw.get("max_tokens")),
                temperature=_optional_float(raw.get("temperature")),
                top_p=_optional_float(raw.get("top_p")),
                thinking=_optional_str(raw.get("thinking")),
                thinking_format=_optional_str(raw.get("thinking_format")),
                thinking_budget=_optional_int(raw.get("thinking_budget")),
                reasoning_effort=_optional_str(raw.get("reasoning_effort")),
            )
        active = _optional_str(data.get("active"))
        if active and active not in profiles:
            raise ModelStoreError(f"活动模型配置档不存在: {active}")
        return cls(active=active, profiles=profiles, path=resolved)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["version = 1"]
        if self.active:
            lines.append(f"active = {_toml_string(self.active)}")
        for name in sorted(self.profiles):
            profile = self.profiles[name]
            lines.extend(["", f"[profiles.{_toml_key(name)}]"])
            values = asdict(profile)
            values.pop("name", None)
            for key, value in values.items():
                if value is not None:
                    lines.append(f"{key} = {_toml_value(value)}")
        content = "\n".join(lines) + "\n"
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(self.path)

    def add(self, profile: ModelProfile, *, replace: bool = False) -> None:
        validate_profile(profile)
        if profile.name in self.profiles and not replace:
            raise ModelStoreError(f"模型配置档已存在: {profile.name}")
        self.profiles[profile.name] = profile
        if self.active is None:
            self.active = profile.name
        self.save()

    def use(self, name: str) -> ModelProfile:
        try:
            profile = self.profiles[name]
        except KeyError as exc:
            raise ModelStoreError(f"找不到模型配置档: {name}") from exc
        self.active = name
        self.save()
        return profile

    def remove(self, name: str) -> ModelProfile:
        try:
            profile = self.profiles.pop(name)
        except KeyError as exc:
            raise ModelStoreError(f"找不到模型配置档: {name}") from exc
        if self.active == name:
            self.active = next(iter(sorted(self.profiles)), None)
        self.save()
        return profile

    def active_profile(self) -> ModelProfile | None:
        return self.profiles.get(self.active or "")


class CredentialStore:
    """Thin wrapper around the system keyring, with user-facing errors."""

    def __init__(self, service: str = KEYRING_SERVICE) -> None:
        self.service = service

    def get(self, credential_id: str) -> str | None:
        keyring = _load_keyring()
        try:
            value = keyring.get_password(self.service, credential_id)
        except Exception as exc:  # noqa: BLE001 - backend exceptions vary
            raise ModelStoreError(f"无法读取系统凭据库: {exc}") from exc
        return value.strip() if value else None

    def set(self, credential_id: str, value: str) -> None:
        secret = value.strip()
        if not secret:
            raise ModelStoreError("API Key 不能为空")
        keyring = _load_keyring()
        try:
            keyring.set_password(self.service, credential_id, secret)
        except Exception as exc:  # noqa: BLE001
            raise ModelStoreError(f"无法写入系统凭据库: {exc}") from exc

    def delete(self, credential_id: str) -> bool:
        keyring = _load_keyring()
        try:
            if keyring.get_password(self.service, credential_id) is None:
                return False
            keyring.delete_password(self.service, credential_id)
        except Exception as exc:  # noqa: BLE001
            raise ModelStoreError(f"无法删除系统凭据: {exc}") from exc
        return True


PRESETS: dict[str, ModelProfile] = {
    "openai": ModelProfile(
        "openai",
        "openai",
        "gpt-5.4-mini",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        thinking_format="openai",
    ),
    "deepseek": ModelProfile(
        "deepseek",
        "openai",
        "deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        thinking_format="standard",
    ),
    "anthropic": ModelProfile(
        "anthropic",
        "anthropic",
        "claude-sonnet-5",
        api_key_env="ANTHROPIC_API_KEY",
        thinking_format="anthropic",
    ),
    "kimi": ModelProfile(
        "kimi",
        "kimi",
        "kimi-k2.7-code",
        base_url="https://api.moonshot.cn/v1",
        api_key_env="MOONSHOT_API_KEY",
        thinking_format="standard",
        max_tokens=16384,
        temperature=0.2,
        top_p=0.95,
    ),
    "kimi-coding": ModelProfile(
        "kimi-coding",
        "kimi",
        "kimi-for-coding",
        base_url="https://api.kimi.com/coding/v1",
        api_key_env="KIMI_CODING_API_KEY",
        thinking="enabled",
        thinking_format="standard",
        reasoning_effort="high",
        max_tokens=16384,
        temperature=0.2,
        top_p=0.95,
    ),
    "qwen": ModelProfile(
        "qwen",
        "openai",
        "qwen3.7-plus",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key_env="DASHSCOPE_API_KEY",
        thinking_format="qwen",
    ),
    "gemini": ModelProfile(
        "gemini",
        "openai",
        "gemini-3.5-flash",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key_env="GEMINI_API_KEY",
        thinking_format="openai",
    ),
    "glm": ModelProfile(
        "glm",
        "openai",
        "glm-5.2",
        base_url="https://open.bigmodel.cn/api/paas/v4/",
        api_key_env="ZAI_API_KEY",
        thinking_format="standard",
    ),
    "minimax": ModelProfile(
        "minimax",
        "openai",
        "MiniMax-M2.7",
        base_url="https://api.minimax.io/v1",
        api_key_env="MINIMAX_API_KEY",
        thinking_format="none",
    ),
}


MODEL_CATALOGS: tuple[dict[str, Any], ...] = (
    {
        "id": "openai",
        "label": "OpenAI GPT",
        "provider": "openai",
        "baseUrl": "https://api.openai.com/v1",
        "apiKeyEnv": "OPENAI_API_KEY",
        "defaultModel": "gpt-5.4-mini",
        "thinkingFormat": "openai",
        "reasoningEfforts": ["none", "low", "medium", "high", "xhigh"],
        "models": [
            {"id": "gpt-5.5", "label": "GPT-5.5", "description": "最强通用与复杂编码模型", "thinking": "effort"},
            {"id": "gpt-5.4", "label": "GPT-5.4", "description": "高能力通用与编码模型", "thinking": "effort"},
            {"id": "gpt-5.4-mini", "label": "GPT-5.4 mini", "description": "兼顾速度、成本与编码能力", "thinking": "effort"},
            {"id": "gpt-5.4-nano", "label": "GPT-5.4 nano", "description": "低延迟、低成本轻量模型", "thinking": "effort"},
        ],
    },
    {
        "id": "deepseek",
        "label": "DeepSeek",
        "provider": "openai",
        "baseUrl": "https://api.deepseek.com",
        "apiKeyEnv": "DEEPSEEK_API_KEY",
        "defaultModel": "deepseek-v4-flash",
        "thinkingFormat": "standard",
        "reasoningEfforts": ["high", "max"],
        "models": [
            {
                "id": "deepseek-v4-flash",
                "label": "DeepSeek V4 Flash",
                "description": "速度更快、成本更低，支持思考与非思考模式",
                "thinking": "toggle",
            },
            {
                "id": "deepseek-v4-pro",
                "label": "DeepSeek V4 Pro",
                "description": "复杂编码与推理能力更强，支持思考与非思考模式",
                "thinking": "toggle",
            },
            {
                "id": "deepseek-chat",
                "label": "DeepSeek Chat（旧别名）",
                "description": "兼容别名，固定非思考模式；2026-07-24 停用",
                "thinking": "disabled",
                "deprecated": True,
            },
            {
                "id": "deepseek-reasoner",
                "label": "DeepSeek Reasoner（旧别名）",
                "description": "兼容别名，固定思考模式；2026-07-24 停用",
                "thinking": "enabled",
                "deprecated": True,
            },
        ],
    },
    {
        "id": "glm",
        "label": "智谱 GLM",
        "provider": "openai",
        "baseUrl": "https://open.bigmodel.cn/api/paas/v4/",
        "apiKeyEnv": "ZAI_API_KEY",
        "defaultModel": "glm-5.2",
        "thinkingFormat": "standard",
        "reasoningEfforts": [],
        "models": [
            {"id": "glm-5.2", "label": "GLM-5.2", "description": "最新旗舰，1M 上下文", "thinking": "toggle"},
            {"id": "glm-5.1", "label": "GLM-5.1", "description": "高能力 Agentic Coding 模型", "thinking": "toggle"},
            {"id": "glm-5", "label": "GLM-5", "description": "复杂编码、规划与工具协作", "thinking": "toggle"},
            {"id": "glm-5-turbo", "label": "GLM-5 Turbo", "description": "长任务的速度与连续性优化", "thinking": "toggle"},
            {"id": "glm-4.7", "label": "GLM-4.7", "description": "通用对话、推理与智能体模型", "thinking": "toggle"},
            {"id": "glm-4.7-flashx", "label": "GLM-4.7 FlashX", "description": "轻量高速版本", "thinking": "toggle"},
            {"id": "glm-4.7-flash", "label": "GLM-4.7 Flash", "description": "免费轻量版本", "thinking": "toggle"},
            {"id": "glm-4.6", "label": "GLM-4.6", "description": "200K 上下文与工具调用", "thinking": "toggle"},
            {"id": "glm-4.5-air", "label": "GLM-4.5 Air", "description": "高性价比轻量模型", "thinking": "toggle"},
        ],
    },
    {
        "id": "kimi-coding",
        "label": "Kimi Coding Plan",
        "provider": "kimi",
        "baseUrl": "https://api.kimi.com/coding/v1",
        "apiKeyEnv": "KIMI_CODING_API_KEY",
        "defaultModel": "kimi-for-coding",
        "thinkingFormat": "standard",
        "reasoningEfforts": ["low", "high"],
        "models": [
            {
                "id": "kimi-for-coding",
                "label": "Kimi For Coding",
                "description": "Coding Plan 专用固定模型名；密钥不能与开放平台混用",
                "thinking": "enabled",
            },
        ],
    },
    {
        "id": "kimi",
        "label": "Kimi 开放平台 API",
        "provider": "kimi",
        "baseUrl": "https://api.moonshot.cn/v1",
        "apiKeyEnv": "MOONSHOT_API_KEY",
        "defaultModel": "kimi-k2.7-code",
        "thinkingFormat": "standard",
        "reasoningEfforts": [],
        "models": [
            {"id": "kimi-k2.7-code", "label": "Kimi K2.7 Code", "description": "最新长程编码模型，固定开启思考", "thinking": "enabled"},
            {"id": "kimi-k2.6", "label": "Kimi K2.6", "description": "256K 上下文，支持思考与非思考模式", "thinking": "toggle"},
            {"id": "kimi-k2.5", "label": "Kimi K2.5", "description": "256K 上下文，支持思考与非思考模式", "thinking": "toggle"},
            {"id": "moonshot-v1-128k", "label": "Moonshot V1 128K", "description": "长上下文通用模型", "thinking": "disabled"},
            {"id": "moonshot-v1-32k", "label": "Moonshot V1 32K", "description": "中等上下文通用模型", "thinking": "disabled"},
            {"id": "moonshot-v1-8k", "label": "Moonshot V1 8K", "description": "低成本短上下文通用模型", "thinking": "disabled"},
        ],
    },
    {
        "id": "qwen",
        "label": "阿里云百炼 · 千问",
        "provider": "openai",
        "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "apiKeyEnv": "DASHSCOPE_API_KEY",
        "defaultModel": "qwen3.7-plus",
        "thinkingFormat": "qwen",
        "thinkingBudget": {"min": 0, "default": 8192, "step": 1024},
        "reasoningEfforts": [],
        "models": [
            {"id": "qwen3.7-max", "label": "Qwen3.7 Max", "description": "旗舰复杂任务与推理模型", "thinking": "toggle"},
            {"id": "qwen3.7-plus", "label": "Qwen3.7 Plus", "description": "高能力通用与编码模型", "thinking": "toggle"},
            {"id": "qwen3.6-flash", "label": "Qwen3.6 Flash", "description": "低延迟、高性价比模型", "thinking": "toggle"},
            {"id": "qwen3-coder-plus", "label": "Qwen3 Coder Plus", "description": "复杂编码与 Agent 任务", "thinking": "toggle"},
            {"id": "qwen3-coder-flash", "label": "Qwen3 Coder Flash", "description": "高速编码模型", "thinking": "toggle"},
        ],
    },
    {
        "id": "anthropic",
        "label": "Anthropic Claude",
        "provider": "anthropic",
        "baseUrl": "",
        "apiKeyEnv": "ANTHROPIC_API_KEY",
        "defaultModel": "claude-sonnet-5",
        "thinkingFormat": "anthropic",
        "reasoningEfforts": ["low", "medium", "high", "xhigh", "max"],
        "models": [
            {"id": "claude-opus-4-8", "label": "Claude Opus 4.8", "description": "复杂推理与长程 Agent 编码", "thinking": "toggle"},
            {"id": "claude-sonnet-5", "label": "Claude Sonnet 5", "description": "速度与能力平衡的最新 Sonnet", "thinking": "toggle"},
            {
                "id": "claude-sonnet-4-6",
                "label": "Claude Sonnet 4.6",
                "description": "成熟稳定的 Agent 编码模型",
                "thinking": "toggle",
                "reasoningEfforts": ["low", "medium", "high", "max"],
            },
        ],
    },
    {
        "id": "gemini",
        "label": "Google Gemini",
        "provider": "openai",
        "baseUrl": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "apiKeyEnv": "GEMINI_API_KEY",
        "defaultModel": "gemini-3.5-flash",
        "thinkingFormat": "openai",
        "reasoningEfforts": ["minimal", "low", "medium", "high"],
        "models": [
            {"id": "gemini-3.5-flash", "label": "Gemini 3.5 Flash", "description": "稳定版高性能编码与 Agent 模型", "thinking": "effort"},
            {"id": "gemini-3.1-pro-preview", "label": "Gemini 3.1 Pro Preview", "description": "复杂推理和多步工具工作流", "thinking": "effort"},
            {"id": "gemini-3-flash-preview", "label": "Gemini 3 Flash Preview", "description": "Pro 级能力与 Flash 延迟", "thinking": "effort"},
        ],
    },
    {
        "id": "minimax",
        "label": "MiniMax",
        "provider": "openai",
        "baseUrl": "https://api.minimax.io/v1",
        "apiKeyEnv": "MINIMAX_API_KEY",
        "defaultModel": "MiniMax-M2.7",
        "thinkingFormat": "none",
        "reasoningEfforts": [],
        "models": [
            {"id": "MiniMax-M2.7", "label": "MiniMax M2.7", "description": "复杂工程、工具调用与推理", "thinking": "enabled"},
            {"id": "MiniMax-M2.7-highspeed", "label": "MiniMax M2.7 Highspeed", "description": "同等能力的高速版本", "thinking": "enabled"},
            {"id": "MiniMax-M2.5", "label": "MiniMax M2.5", "description": "代码生成与重构推理模型", "thinking": "enabled"},
            {"id": "MiniMax-M2.5-highspeed", "label": "MiniMax M2.5 Highspeed", "description": "M2.5 高速版本", "thinking": "enabled"},
        ],
    },
)


def profile_from_preset(name: str, *, profile_name: str | None = None) -> ModelProfile:
    try:
        preset = PRESETS[name.lower()]
    except KeyError as exc:
        raise ModelStoreError(f"未知模型预设: {name}。可用: {', '.join(sorted(PRESETS))}") from exc
    values = asdict(preset)
    values["name"] = profile_name or preset.name
    values["credential_id"] = f"profile:{values['name']}"
    return ModelProfile(**values)


def validate_profile(profile: ModelProfile) -> None:
    if not _PROFILE_RE.fullmatch(profile.name):
        raise ModelStoreError("配置档名称只能包含字母、数字、点、下划线和连字符，最长 64 字符")
    if not profile.model.strip():
        raise ModelStoreError("模型名称不能为空")
    if not profile.provider.strip():
        raise ModelStoreError("Provider 类型不能为空")
    if profile.base_url and not profile.base_url.startswith(("http://", "https://")):
        raise ModelStoreError("base_url 必须以 http:// 或 https:// 开头")
    if profile.thinking not in {None, "enabled", "disabled"}:
        raise ModelStoreError("thinking 只能是 enabled 或 disabled")
    if profile.thinking_format not in {None, "standard", "qwen", "openai", "anthropic", "none"}:
        raise ModelStoreError("thinking_format 无效")
    if profile.thinking_budget is not None and profile.thinking_budget < 0:
        raise ModelStoreError("thinking_budget 不能小于 0")
    if profile.top_p is not None and not (0 <= profile.top_p <= 1):
        raise ModelStoreError("top_p 必须在 0 到 1 之间")
    if profile.reasoning_effort not in {None, "none", "minimal", "low", "medium", "high", "max", "xhigh"}:
        raise ModelStoreError("reasoning_effort 无效")


def active_profile_overlay(path: Path | None = None) -> tuple[dict[str, Any], Path | None]:
    store = ModelStore.load(path)
    profile = store.active_profile()
    return (profile.provider_overlay(), store.path) if profile else ({}, None)


def _load_keyring():
    try:
        import keyring
    except ImportError as exc:
        raise ModelStoreError("系统凭据功能需要 keyring。请重新安装: pip install -e .") from exc
    return keyring


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None and value != "" else None


def _optional_int(value: object) -> int | None:
    return int(cast(Any, value)) if value is not None else None


def _optional_float(value: object) -> float | None:
    return float(cast(Any, value)) if value is not None else None


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_key(value: str) -> str:
    return value if re.fullmatch(r"[A-Za-z0-9_-]+", value) else _toml_string(value)


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _toml_string(value)
    return str(value)


__all__ = [
    "CredentialStore",
    "KEYRING_SERVICE",
    "MODEL_STORE_PATH",
    "MODEL_STORE_ENV",
    "ModelProfile",
    "ModelStore",
    "ModelStoreError",
    "MODEL_CATALOGS",
    "PRESETS",
    "active_profile_overlay",
    "profile_from_preset",
]
