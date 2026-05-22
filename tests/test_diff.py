"""Tests for the version diff utility."""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from sdwan_mcp.diff import diff_versions
from tests.conftest import MINIMAL_SPEC


def _write_spec(root: Path, version: str, spec: dict) -> None:
    d = root / "specs" / version
    d.mkdir(parents=True, exist_ok=True)
    (d / "monitoring.yaml").write_text(yaml.safe_dump(spec))


def test_diff_detects_added_removed_changed(tmp_path: Path) -> None:
    old_spec = copy.deepcopy(MINIMAL_SPEC)
    new_spec = copy.deepcopy(MINIMAL_SPEC)

    # Removed: drop getDeviceCount
    del new_spec["paths"]["/count"]
    # Added: new operation
    new_spec["paths"]["/health"] = {
        "get": {
            "tags": ["Monitoring - Device Details"],
            "operationId": "getDeviceHealth",
            "summary": "Health check",
        },
    }
    # Changed: add a query param to listAllDevices
    new_spec["paths"]["/device"]["get"]["parameters"].append(
        {
            "name": "includeOffline",
            "in": "query",
            "required": False,
            "schema": {"type": "boolean"},
        }
    )

    _write_spec(tmp_path, "old", old_spec)
    _write_spec(tmp_path, "new", new_spec)

    result = diff_versions(str(tmp_path / "specs"), "old", "new")

    removed_ids = {op.operation_id for op in result.removed}
    added_ids = {op.operation_id for op in result.added}
    changed_ids = {od.operation_id for od in result.changed}

    assert "getDeviceCount" in removed_ids
    assert "getDeviceHealth" in added_ids
    assert "listAllDevices" in changed_ids
