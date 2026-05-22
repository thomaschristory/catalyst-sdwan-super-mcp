"""Tests for the OpenAPI spec loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from sdwan_mcp.loader import SpecLoader


def test_loader_groups_operations_by_tag(specs_dir: Path) -> None:
    index = SpecLoader(str(specs_dir), "20.99", read_write=True, granularity="tag").load()

    slugs = {g.slug for g in index.groups}
    assert "monitoring_device_details" in slugs
    assert "configuration_device_actions" in slugs


def test_loader_section_granularity_collapses_tags(specs_dir: Path) -> None:
    index = SpecLoader(str(specs_dir), "20.99", read_write=True, granularity="section").load()

    slugs = {g.slug for g in index.groups}
    # Section grouping collapses "Monitoring - Device Details" -> "Monitoring".
    assert "monitoring" in slugs
    assert "configuration" in slugs
    assert "monitoring_device_details" not in slugs


def test_loader_read_only_drops_writes(specs_dir: Path) -> None:
    index = SpecLoader(str(specs_dir), "20.99", read_write=False, granularity="tag").load()

    # The POST-only tag group is entirely removed in RO mode.
    slugs = {g.slug for g in index.groups}
    assert "configuration_device_actions" not in slugs
    # And the POST operationId is no longer in the index.
    assert "updateDevice" not in index.by_operation_id


def test_loader_read_write_keeps_mutations(specs_dir: Path) -> None:
    index = SpecLoader(str(specs_dir), "20.99", read_write=True).load()

    assert "updateDevice" in index.by_operation_id
    assert index.by_operation_id["updateDevice"].method == "post"


def test_loader_builds_operation_index(specs_dir: Path) -> None:
    index = SpecLoader(str(specs_dir), "20.99", read_write=True).load()

    op = index.by_operation_id["getDeviceById"]
    assert op.method == "get"
    assert op.path == "/device/{deviceId}"
    assert any(p.name == "deviceId" and p.location == "path" for p in op.parameters)


def test_loader_missing_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        SpecLoader(str(tmp_path), "does-not-exist", read_write=True)


def test_loader_rejects_invalid_granularity(specs_dir: Path) -> None:
    with pytest.raises(ValueError, match="Invalid granularity"):
        SpecLoader(str(specs_dir), "20.99", granularity="nope")
