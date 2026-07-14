from __future__ import annotations

import json
from importlib.resources import files

import pytest

from mycode.catalog import (
    CATALOG_RESOURCE,
    MODEL_CATALOG_DATA,
    ModelCatalogError,
    load_model_catalog,
)


def test_bundled_catalog_defaults_and_pricing_status_are_valid() -> None:
    assert MODEL_CATALOG_DATA.schema_version == 1
    assert MODEL_CATALOG_DATA.catalog_version
    assert MODEL_CATALOG_DATA.verified_at is None
    assert len(MODEL_CATALOG_DATA.catalogs) == 9
    for catalog in MODEL_CATALOG_DATA.catalogs:
        models = {model["id"]: model for model in catalog["models"]}
        default = models[catalog["defaultModel"]]
        assert default["pricingStatus"] in {"known", "unknown"}
        assert "\ufffd" not in catalog["label"]
        assert all("\ufffd" not in model["description"] for model in models.values())
    kimi_coding = next(
        item for item in MODEL_CATALOG_DATA.catalogs if item["id"] == "kimi-coding"
    )
    assert "密钥不能与开放平台混用" in kimi_coding["models"][0]["description"]


def test_catalog_rejects_default_model_not_in_catalog(tmp_path) -> None:
    raw = json.loads(
        files("mycode.data").joinpath(CATALOG_RESOURCE).read_text(encoding="utf-8")
    )
    raw["catalogs"][0]["defaultModel"] = "missing-model"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ModelCatalogError, match="default model"):
        load_model_catalog(path)


def test_catalog_rejects_invalid_price(tmp_path) -> None:
    raw = json.loads(
        files("mycode.data").joinpath(CATALOG_RESOURCE).read_text(encoding="utf-8")
    )
    raw["prices"]["gpt-4o-mini"]["input"] = -1
    path = tmp_path / "invalid-price.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ModelCatalogError, match="invalid input"):
        load_model_catalog(path)
