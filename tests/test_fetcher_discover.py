"""Tests for sdwan_mcp.fetcher.discover."""

from __future__ import annotations

from pathlib import Path

import pytest

from sdwan_mcp.fetcher.discover import (
    Discovery,
    DiscoveryError,
    FragmentRef,
    discovery_url,
    parse_discovery_html,
    version_to_slug,
)

FIXTURES = Path(__file__).parent / "fetcher_fixtures"


def _load_minimal_html() -> str:
    return (FIXTURES / "devnet_minimal.html").read_text()


def test_version_to_slug() -> None:
    assert version_to_slug("20.18") == "20-18"
    assert version_to_slug("21.1") == "21-1"


def test_discovery_url() -> None:
    assert discovery_url("20.18") == "https://developer.cisco.com/docs/sdwan/20-18/"


def test_parse_returns_all_api_and_model_leaves() -> None:
    disc = parse_discovery_html(_load_minimal_html(), "20.99")
    assert isinstance(disc, Discovery)
    assert disc.version == "20.99"
    assert disc.slug == "20-99"
    assert disc.pubhub_bucket == "cisco-catalyst-sd-wan-api-guide-20-99"

    # Four api leaves in the fixture
    api_rests = sorted(f.rest for f in disc.api_fragments)
    assert api_rests == [
        "v1/device/get.json",
        "v1/device/{deviceId}/get.json",
        "v1/device/{deviceId}/post.json",
        "v1/template/policy/get.json",
    ]
    # Three model leaves
    model_rests = sorted(f.rest for f in disc.model_fragments)
    assert model_rests == ["Device.json", "DeviceList.json", "Policy.json"]


def test_parse_emits_absolute_pubhub_urls() -> None:
    disc = parse_discovery_html(_load_minimal_html(), "20.99")
    urls = {f.url for f in disc.api_fragments}
    expected = (
        "https://pubhub.devnetcloud.com/media/"
        "cisco-catalyst-sd-wan-api-guide-20-99/docs/"
        "aaaa1111-1111-1111-1111-111111111111/apis/v1/device/get.json"
    )
    assert expected in urls


def test_fragment_name_strips_dot_json() -> None:
    ref = FragmentRef(
        url="https://example/aaaa1111-1111-1111-1111-111111111111/models/Device.json",
        uuid="aaaa1111-1111-1111-1111-111111111111",
        kind="models",
        rest="Device.json",
    )
    assert ref.name == "Device"


def test_parse_deduplicates_repeated_leaves() -> None:
    html = (
        'x content:"./aaaa1111-1111-1111-1111-111111111111/apis/v1/x/get.json" y '
        'z content:"./aaaa1111-1111-1111-1111-111111111111/apis/v1/x/get.json" w'
    )
    disc = parse_discovery_html(html, "20.99")
    assert len(disc.api_fragments) == 1


def test_parse_raises_when_no_api_leaves_present() -> None:
    html = (
        "<html>this page has no webJson with apis/ fragments at all, "
        "just some random content</html>"
    )
    with pytest.raises(DiscoveryError):
        parse_discovery_html(html, "20.99")


def test_parse_falls_back_to_canonical_bucket_when_page_lacks_prefix() -> None:
    # No pubhub URL anywhere; still parses, uses canonical template
    html = 'webJson:{items:[{content:"./aaaa1111-1111-1111-1111-111111111111/apis/v1/x/get.json"}]}'
    disc = parse_discovery_html(html, "21.1")
    assert disc.pubhub_bucket == "cisco-catalyst-sd-wan-api-guide-21-1"
    assert disc.api_fragments[0].url.startswith(
        "https://pubhub.devnetcloud.com/media/cisco-catalyst-sd-wan-api-guide-21-1/docs/"
    )
