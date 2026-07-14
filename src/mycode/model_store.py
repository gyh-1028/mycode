"""Persistent model profiles and OS-backed API credentials."""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, cast

from mycode.catalog import MODEL_CATALOG_DATA
from mycode.persistence import atomic_write_text, bytes_fingerprint, file_fingerprint, path_lock

MODEL_STORE_PATH = Path.home() / ".mycode" / "models.toml"
MODEL_STORE_ENV = "MYCODE_MODELS_FILE"
KEYRING_SERVICE = "mycode-ai-cli"
_PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class ModelStoreError(RuntimeError):
    pass


class ModelStoreConflictError(ModelStoreError):
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
    _fingerprint: str | None = field(default=None, repr=False, compare=False)

    @classmethod
    def load(cls, path: Path | None = None) -> ModelStore:
        env_path = os.environ.get(MODEL_STORE_ENV)
        resolved = (path or (Path(env_path) if env_path else MODEL_STORE_PATH)).expanduser()
        if not resolved.is_file():
            return cls(path=resolved)
        try:
            raw_content = resolved.read_bytes()
            content = raw_content.decode("utf-8")
            data = tomllib.loads(content)
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
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
        return cls(
            active=active,
            profiles=profiles,
            path=resolved,
            _fingerprint=bytes_fingerprint(raw_content),
        )

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
        with path_lock(self.path):
            if file_fingerprint(self.path) != self._fingerprint:
                raise ModelStoreConflictError(
                    f"model profile store changed on disk; reload before saving: {self.path}"
                )
            self._fingerprint = atomic_write_text(self.path, content)

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


_CATALOG_DATA = MODEL_CATALOG_DATA
PRESETS: dict[str, ModelProfile] = {
    name: ModelProfile(**raw)
    for name, raw in _CATALOG_DATA.presets.items()
}
MODEL_CATALOGS: tuple[dict[str, Any], ...] = _CATALOG_DATA.catalogs
MODEL_CATALOG_METADATA = {
    "schema_version": _CATALOG_DATA.schema_version,
    "catalog_version": _CATALOG_DATA.catalog_version,
    "verified_at": _CATALOG_DATA.verified_at,
    "pricing_verified_at": _CATALOG_DATA.pricing_verified_at,
}

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
    "ModelStoreConflictError",
    "ModelStoreError",
    "MODEL_CATALOGS",
    "MODEL_CATALOG_METADATA",
    "PRESETS",
    "active_profile_overlay",
    "profile_from_preset",
]
