"""Tests for sdwan_mcp.fetcher.stitch."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sdwan_mcp.fetcher.discover import FragmentRef
from sdwan_mcp.fetcher.stitch import StitchError, stitch

FIXTURES = Path(__file__).parent / "fetcher_fixtures"


def _ref(rest: str, kind: str = "apis") -> FragmentRef:
    return FragmentRef(
        url=f"https://example/aaaa1111-1111-1111-1111-111111111111/{kind}/{rest}",
        uuid="aaaa1111-1111-1111-1111-111111111111",
        kind=kind,
        rest=rest,
    )


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_stitch_builds_paths_and_components() -> None:
    ops = [
        (_ref("v1/device/get.json"), _load("op_fragment_list.json")),
        (_ref("v1/device/{deviceId}/get.json"), _load("op_fragment_get.json")),
        (_ref("v1/device/{deviceId}/post.json"), _load("op_fragment_post.json")),
        (_ref("v1/template/policy/get.json"), _load("op_fragment_policy.json")),
    ]
    models = [
        (_ref("Device.json", "models"), _load("model_fragment_device.json")),
        (_ref("DeviceList.json", "models"), _load("model_fragment_devicelist.json")),
        (_ref("Policy.json", "models"), _load("model_fragment_policy.json")),
    ]
    doc = stitch(version="20.99", op_fragments=ops, model_fragments=models)

    assert doc["openapi"] == "3.1.0"
    assert doc["info"]["version"] == "20.99"
    assert sorted(doc["paths"]) == [
        "/v1/device",
        "/v1/device/{deviceId}",
        "/v1/template/policy",
    ]
    # Two methods on /v1/device/{deviceId}
    assert sorted(doc["paths"]["/v1/device/{deviceId}"]) == ["get", "post"]
    # method/path stripped from each operation object
    assert "method" not in doc["paths"]["/v1/device"]["get"]
    assert "path" not in doc["paths"]["/v1/device"]["get"]
    # operationId preserved
    assert doc["paths"]["/v1/device"]["get"]["operationId"] == "listDevices"

    # Schemas
    assert set(doc["components"]["schemas"]) == {"Device", "DeviceList", "Policy"}
    assert doc["components"]["schemas"]["Device"]["properties"]["deviceId"]["type"] == "string"

    # Tags collected and deduped
    tag_names = [t["name"] for t in doc["tags"]]
    assert set(tag_names) == {"Monitoring - Device", "Configuration - Policy"}

    # Servers pulled from fragment meta
    assert doc["servers"][0]["url"] == "/dataservice"


def test_stitch_handles_duplicate_path_method_pairs() -> None:
    op = _load("op_fragment_list.json")
    ops = [
        (_ref("v1/device/get.json"), op),
        (_ref("v1/device/get.json"), op),  # duplicate, must be ignored, not error
    ]
    doc = stitch(version="20.99", op_fragments=ops, model_fragments=[])
    assert sorted(doc["paths"]["/v1/device"]) == ["get"]


def test_stitch_rejects_missing_method_or_path() -> None:
    op = _load("op_fragment_list.json")
    op["spec"].pop("method")
    with pytest.raises(StitchError):
        stitch(
            version="20.99",
            op_fragments=[(_ref("v1/x/get.json"), op)],
            model_fragments=[],
        )


def test_stitch_keeps_first_schema_on_collision_and_reports() -> None:
    """Two sections publish the same schema name → keep first, surface collision."""
    a = _load("model_fragment_device.json")
    b = _load("model_fragment_device.json")
    b["spec"]["properties"]["hostname"]["type"] = "integer"  # mark it different
    doc = stitch(
        version="20.99",
        op_fragments=[(_ref("v1/x/get.json"), _load("op_fragment_list.json"))],
        model_fragments=[
            (_ref("Device.json", "models"), a),
            (_ref("Device.json", "models"), b),  # collision
        ],
    )
    # First one wins
    assert doc["components"]["schemas"]["Device"]["properties"]["hostname"]["type"] == "string"
    # Collision is surfaced via the side-channel key
    assert "x-sdwan-mcp-schema-collisions" in doc
    assert doc["x-sdwan-mcp-schema-collisions"]["Device"] == 2


def test_stitch_falls_back_to_default_servers_when_meta_missing() -> None:
    op = _load("op_fragment_list.json")
    op["meta"].pop("servers", None)
    doc = stitch(
        version="20.99",
        op_fragments=[(_ref("v1/x/get.json"), op)],
        model_fragments=[],
    )
    assert doc["servers"][0]["url"].endswith("/dataservice")
