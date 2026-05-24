"""End-to-end tests for the fetcher with all HTTP traffic mocked."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
import yaml

from sdwan_mcp.fetcher import (
    KNOWN_VERSIONS,
    FetchError,
    VersionInfo,
    default_target_path,
    fetch_version,
    list_known_versions,
)
from sdwan_mcp.fetcher.fetch import _request_with_retry, make_client

FIXTURES = Path(__file__).parent / "fetcher_fixtures"
HTML_FIXTURE = FIXTURES / "devnet_minimal.html"


def _frag_url(uuid: str, kind: str, rest: str) -> str:
    return (
        "https://pubhub.devnetcloud.com/media/"
        "cisco-catalyst-sd-wan-api-guide-20-99/docs/"
        f"{uuid}/{kind}/{rest}"
    )


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def mocked_devnet(respx_mock: respx.MockRouter) -> respx.MockRouter:
    """Stand in for the entire DevNet + pubhub stack."""
    respx_mock.get("https://developer.cisco.com/docs/sdwan/20-99/").respond(
        200, text=HTML_FIXTURE.read_text()
    )
    aaaa = "aaaa1111-1111-1111-1111-111111111111"
    bbbb = "bbbb2222-2222-2222-2222-222222222222"
    respx_mock.get(_frag_url(aaaa, "apis", "v1/device/get.json")).respond(
        200, json=_load("op_fragment_list.json")
    )
    respx_mock.get(_frag_url(aaaa, "apis", "v1/device/{deviceId}/get.json")).respond(
        200, json=_load("op_fragment_get.json")
    )
    respx_mock.get(_frag_url(aaaa, "apis", "v1/device/{deviceId}/post.json")).respond(
        200, json=_load("op_fragment_post.json")
    )
    respx_mock.get(_frag_url(bbbb, "apis", "v1/template/policy/get.json")).respond(
        200, json=_load("op_fragment_policy.json")
    )
    respx_mock.get(_frag_url(aaaa, "models", "Device.json")).respond(
        200, json=_load("model_fragment_device.json")
    )
    respx_mock.get(_frag_url(aaaa, "models", "DeviceList.json")).respond(
        200, json=_load("model_fragment_devicelist.json")
    )
    respx_mock.get(_frag_url(bbbb, "models", "Policy.json")).respond(
        200, json=_load("model_fragment_policy.json")
    )
    return respx_mock


async def test_fetch_version_writes_stitched_yaml(
    tmp_path: Path,
    mocked_devnet: respx.MockRouter,
) -> None:
    target = await fetch_version(
        "20.99",
        specs_dir=tmp_path,
        use_cache=False,
        log=False,
        min_paths=1,
        min_yaml_bytes=0,
    )
    assert target == default_target_path(tmp_path, "20.99")
    assert target.exists()
    doc = yaml.safe_load(target.read_text())
    # Three paths, 4 ops total
    assert sorted(doc["paths"]) == [
        "/v1/device",
        "/v1/device/{deviceId}",
        "/v1/template/policy",
    ]
    # Three schemas
    assert sorted(doc["components"]["schemas"]) == ["Device", "DeviceList", "Policy"]


async def test_fetch_version_skips_when_cached_and_not_forced(
    tmp_path: Path,
    mocked_devnet: respx.MockRouter,
) -> None:
    # First fetch
    target = await fetch_version(
        "20.99", specs_dir=tmp_path, use_cache=False, log=False, min_paths=1, min_yaml_bytes=0
    )
    orig_mtime = target.stat().st_mtime_ns
    # Sleep is annoying; just rewrite a sentinel so we can detect re-write
    target.write_text(target.read_text() + "\n# sentinel\n")
    await fetch_version(
        "20.99", specs_dir=tmp_path, use_cache=False, log=False, min_paths=1, min_yaml_bytes=0
    )
    # File untouched
    assert target.read_text().endswith("# sentinel\n")
    # And mtime not earlier than orig
    assert target.stat().st_mtime_ns >= orig_mtime


async def test_fetch_version_force_overwrites(
    tmp_path: Path,
    mocked_devnet: respx.MockRouter,
) -> None:
    target = await fetch_version(
        "20.99", specs_dir=tmp_path, use_cache=False, log=False, min_paths=1, min_yaml_bytes=0
    )
    target.write_text("garbage")
    await fetch_version(
        "20.99",
        specs_dir=tmp_path,
        use_cache=False,
        log=False,
        force=True,
        min_paths=1,
        min_yaml_bytes=0,
    )
    assert "openapi" in target.read_text()


async def test_fetch_version_uses_fragment_disk_cache(
    tmp_path: Path,
    mocked_devnet: respx.MockRouter,
) -> None:
    cache_root = tmp_path / "frag_cache"
    await fetch_version(
        "20.99",
        specs_dir=tmp_path / "specs",
        use_cache=True,
        cache_root=cache_root,
        log=False,
        min_paths=1,
        min_yaml_bytes=0,
    )
    cached = list((cache_root / "20.99").rglob("*.json"))
    assert cached, "expected fragment cache to be populated"
    # cached file should contain valid JSON identical to fixture
    aaaa = "aaaa1111-1111-1111-1111-111111111111"
    cached_file = cache_root / "20.99" / aaaa / "models" / "Device.json"
    assert cached_file.exists()
    assert json.loads(cached_file.read_text())["spec"]["title"] == "Device"


async def test_request_with_retry_eventually_raises(
    respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    respx_mock.get("https://example.com/always-503").respond(503)
    async with make_client(timeout=5.0) as client:
        with pytest.raises(FetchError):
            await _request_with_retry(client, "GET", "https://example.com/always-503")


async def test_request_with_retry_recovers_after_5xx(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get("https://example.com/flaky")
    route.side_effect = [
        httpx.Response(503),
        httpx.Response(503),
        httpx.Response(200, text="ok"),
    ]
    async with make_client(timeout=5.0) as client:
        resp = await _request_with_retry(client, "GET", "https://example.com/flaky")
    assert resp.status_code == 200


def test_known_versions_listed_with_cache_status(tmp_path: Path) -> None:
    (tmp_path / "20.18").mkdir()
    (tmp_path / "20.18" / "x.yaml").write_text("openapi: 3.0.0\n")
    rows = list_known_versions(tmp_path)
    by_version = {r.version: r for r in rows}
    for v in KNOWN_VERSIONS:
        assert v in by_version
    assert by_version["20.18"].cached is True
    assert by_version["20.15"].cached is False
    assert by_version["20.15"].layout == "monolith"
    assert by_version["20.18"].layout == "split"
    assert isinstance(rows[0], VersionInfo)
