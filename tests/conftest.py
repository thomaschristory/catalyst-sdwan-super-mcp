"""Shared fixtures for the sdwan-mcp test suite."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.mocking import MockRouter

# Paths chosen so each operation derives a distinct action_name:
#   GET  /devices              -> get_device_details_devices    (listAllDevices)
#   GET  /devices/{id}/info    -> get_device_details_info       (getDeviceById)
#   GET  /devices/count        -> get_device_details_count      (getDeviceCount)
#   POST /devices/{id}/config  -> post_device_actions_config    (updateDevice)
MINIMAL_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "test", "version": "1.0"},
    "paths": {
        "/devices": {
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
        "/devices/{deviceId}/info": {
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
        },
        "/devices/{deviceId}/config": {
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
        "/devices/count": {
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
    """Create a `specs/20.99/monitoring.yaml` tree the loader can consume."""
    version_dir = tmp_path / "specs" / "20.99"
    version_dir.mkdir(parents=True)
    (version_dir / "monitoring.yaml").write_text(yaml.safe_dump(MINIMAL_SPEC))
    return tmp_path / "specs"


@pytest.fixture
def mock_router(monkeypatch: pytest.MonkeyPatch) -> MockRouter:
    """A respx-shaped router whose transport is injected into clients under test.

    Dispatcher tests pass ``mock_router.transport`` to ``Dispatcher(...)`` directly.
    The fetcher builds its own client internally, so ``make_client`` is patched here
    to inject the same transport — note the bound name lives on ``sdwan_mcp.fetcher``
    (imported at module load), not on ``fetcher.fetch``.
    """
    import sdwan_mcp.fetcher as fetcher_pkg
    from sdwan_mcp.fetcher.fetch import make_client as real_make_client

    router = MockRouter()

    def _mock_client(**kwargs: object) -> object:
        kwargs["transport"] = router.transport
        return real_make_client(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(fetcher_pkg, "make_client", _mock_client)
    return router
