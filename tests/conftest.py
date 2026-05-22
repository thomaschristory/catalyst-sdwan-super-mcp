"""Shared fixtures for the sdwan-mcp test suite."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

MINIMAL_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "test", "version": "1.0"},
    "paths": {
        "/device": {
            "get": {
                "tags": ["Monitoring - Device Details"],
                "operationId": "listAllDevices",
                "summary": "List all devices",
                "parameters": [
                    {
                        "name": "site-id",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "string"},
                        "description": "Filter by site id",
                    },
                ],
            },
        },
        "/device/{deviceId}": {
            "get": {
                "tags": ["Monitoring - Device Details"],
                "operationId": "getDeviceById",
                "summary": "Get one device",
                "parameters": [
                    {
                        "name": "deviceId",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                ],
            },
            "post": {
                "tags": ["Configuration - Device Actions"],
                "operationId": "updateDevice",
                "summary": "Update one device",
                "parameters": [
                    {
                        "name": "deviceId",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                ],
                "requestBody": {"description": "Device config"},
            },
        },
        "/count": {
            "get": {
                "tags": ["Monitoring - Device Details"],
                "operationId": "getDeviceCount",
                "summary": "Total device count",
            },
        },
    },
}


@pytest.fixture
def specs_dir(tmp_path: Path) -> Path:
    """Create a `specs/test/monitoring.yaml` tree the loader can consume."""
    version_dir = tmp_path / "specs" / "20.99"
    version_dir.mkdir(parents=True)
    (version_dir / "monitoring.yaml").write_text(yaml.safe_dump(MINIMAL_SPEC))
    return tmp_path / "specs"
