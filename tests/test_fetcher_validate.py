"""Tests for sdwan_mcp.fetcher.validate."""

from __future__ import annotations

import pytest

from sdwan_mcp.fetcher.validate import (
    MIN_PATHS,
    FetcherValidationError,
    validate,
)


def _minimal_doc(*, paths: int = MIN_PATHS, with_schema: bool = True) -> dict:
    return {
        "openapi": "3.1.0",
        "info": {"title": "x", "version": "y"},
        "paths": {
            f"/p{i}": {"get": {"responses": {"200": {"description": "ok"}}}} for i in range(paths)
        },
        "components": {"schemas": {"X": {"type": "object"}}} if with_schema else {"schemas": {}},
    }


def test_valid_doc_returns_empty_warnings() -> None:
    assert validate(_minimal_doc()) == []


def test_missing_openapi_key_fails() -> None:
    doc = _minimal_doc()
    del doc["openapi"]
    with pytest.raises(FetcherValidationError, match="openapi"):
        validate(doc)


def test_too_few_paths_fails() -> None:
    with pytest.raises(FetcherValidationError, match="Too few paths"):
        validate(_minimal_doc(paths=MIN_PATHS - 1))


def test_empty_schemas_fails() -> None:
    with pytest.raises(FetcherValidationError, match="schemas"):
        validate(_minimal_doc(with_schema=False))


def test_yaml_size_floor_enforced_when_supplied() -> None:
    with pytest.raises(FetcherValidationError, match="bytes"):
        validate(_minimal_doc(), yaml_bytes=1024)


def test_unresolved_schema_ref_fails() -> None:
    doc = _minimal_doc()
    doc["paths"]["/p0"]["get"]["responses"]["200"]["content"] = {
        "application/json": {"schema": {"$ref": "#/components/schemas/MissingThing"}}
    }
    with pytest.raises(FetcherValidationError, match="Unresolved schema"):
        validate(doc)


def test_unresolved_example_ref_is_only_a_warning() -> None:
    doc = _minimal_doc()
    doc["paths"]["/p0"]["get"]["responses"]["200"]["content"] = {
        "application/json": {"examples": {"a": {"$ref": "#/components/examples/MissingExample"}}}
    }
    warnings = validate(doc)
    assert any("examples" in w for w in warnings)
