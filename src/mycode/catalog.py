"""Load and validate the bundled model catalog and pricing data."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

CATALOG_RESOURCE = "model_catalog-v1.json"
CATALOG_SCHEMA_VERSION = 1


class ModelCatalogError(ValueError):
    pass


@dataclass(frozen=True)
class ModelCatalogData:
    schema_version: int
    catalog_version: str
    verified_at: str | None
    pricing_verified_at: str | None
    notes: str
    presets: dict[str, dict[str, Any]]
    catalogs: tuple[dict[str, Any], ...]
    prices: dict[str, dict[str, float | None]]

    @property
    def model_ids(self) -> frozenset[str]:
        return frozenset(
            str(model["id"])
            for catalog in self.catalogs
            for model in catalog["models"]
        )


def load_model_catalog(path: Path | None = None) -> ModelCatalogData:
    try:
        text = (
            path.read_text(encoding="utf-8")
            if path is not None
            else files("mycode.data").joinpath(CATALOG_RESOURCE).read_text(encoding="utf-8")
        )
        raw = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelCatalogError(f"unable to load model catalog: {exc}") from exc
    if not isinstance(raw, dict):
        raise ModelCatalogError("model catalog root must be an object")
    if raw.get("schemaVersion") != CATALOG_SCHEMA_VERSION:
        raise ModelCatalogError(
            f"unsupported model catalog schema: {raw.get('schemaVersion')!r}"
        )

    presets = _string_mapping(raw.get("presets"), "presets")
    prices = _string_mapping(raw.get("prices"), "prices")
    catalogs_raw = raw.get("catalogs")
    if not isinstance(catalogs_raw, list):
        raise ModelCatalogError("catalogs must be an array")
    catalogs = tuple(_catalog(item, index) for index, item in enumerate(catalogs_raw))
    catalog_ids = [str(item["id"]) for item in catalogs]
    if len(catalog_ids) != len(set(catalog_ids)):
        raise ModelCatalogError("catalog ids must be unique")

    all_models = {str(model["id"]) for item in catalogs for model in item["models"]}
    for name, preset in presets.items():
        for field in ("name", "provider", "model"):
            if not isinstance(preset.get(field), str) or not preset[field]:
                raise ModelCatalogError(f"preset {name!r} has invalid {field}")
        if preset["model"] not in all_models:
            raise ModelCatalogError(
                f"preset {name!r} references unknown model {preset['model']!r}"
            )
    for model, price in prices.items():
        for field in ("input", "output"):
            value = price.get(field)
            if not isinstance(value, (int, float)) or value < 0:
                raise ModelCatalogError(f"price {model!r} has invalid {field}")
        for field in ("cache_read", "cache_write"):
            value = price.get(field)
            if value is not None and (not isinstance(value, (int, float)) or value < 0):
                raise ModelCatalogError(f"price {model!r} has invalid {field}")

    for catalog in catalogs:
        for model in catalog["models"]:
            model["pricingStatus"] = "known" if model["id"] in prices else "unknown"

    return ModelCatalogData(
        schema_version=CATALOG_SCHEMA_VERSION,
        catalog_version=str(raw.get("catalogVersion", "")),
        verified_at=_optional_string(raw.get("verifiedAt")),
        pricing_verified_at=_optional_string(raw.get("pricingVerifiedAt")),
        notes=str(raw.get("notes", "")),
        presets=presets,
        catalogs=catalogs,
        prices=prices,
    )


def _string_mapping(value: object, field: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        raise ModelCatalogError(f"{field} must be an object")
    result: dict[str, dict[str, Any]] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, dict):
            raise ModelCatalogError(f"{field} entries must be named objects")
        result[key] = item
    return result


def _catalog(value: object, index: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ModelCatalogError(f"catalogs[{index}] must be an object")
    catalog = dict(value)
    for field in ("id", "provider", "defaultModel"):
        if not isinstance(catalog.get(field), str) or not catalog[field]:
            raise ModelCatalogError(f"catalogs[{index}] has invalid {field}")
    _display_text(catalog.get("label"), f"catalogs[{index}].label")
    models = catalog.get("models")
    if not isinstance(models, list) or not models:
        raise ModelCatalogError(f"catalogs[{index}].models must be a non-empty array")
    model_ids: list[str] = []
    for model_index, model in enumerate(models):
        if not isinstance(model, dict) or not isinstance(model.get("id"), str) or not model["id"]:
            raise ModelCatalogError(
                f"catalogs[{index}].models[{model_index}] has invalid id"
            )
        _display_text(model.get("label"), f"catalogs[{index}].models[{model_index}].label")
        _display_text(
            model.get("description"),
            f"catalogs[{index}].models[{model_index}].description",
        )
        model_ids.append(str(model["id"]))
    if len(model_ids) != len(set(model_ids)):
        raise ModelCatalogError(f"catalog {catalog['id']!r} contains duplicate model ids")
    if catalog["defaultModel"] not in model_ids:
        raise ModelCatalogError(
            f"catalog {catalog['id']!r} default model is not listed"
        )
    return catalog


def _display_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\ufffd" in value:
        raise ModelCatalogError(f"{field} contains invalid display text")
    return value


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


MODEL_CATALOG_DATA = load_model_catalog()


__all__ = [
    "CATALOG_RESOURCE",
    "CATALOG_SCHEMA_VERSION",
    "MODEL_CATALOG_DATA",
    "ModelCatalogData",
    "ModelCatalogError",
    "load_model_catalog",
]
