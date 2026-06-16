"""Tests for debug mode — upstream request/response capture (#72).

Covers three surfaces:
  - config: SDWAN_MCP_DEBUG* env parsing + defaults
  - dispatcher: capture on error vs all, redaction on/off, request-body shape
  - CLI: --debug / --debug-all-calls / --debug-no-redact precedence
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from sdwan_mcp.auth import VManageAuth
from sdwan_mcp.config import DebugConfig, load_config
from sdwan_mcp.dispatcher import Dispatcher, _cap_body, _redact_data, _redact_headers
from sdwan_mcp.loader import SpecLoader
from sdwan_mcp.server import parse_args, resolve_debug_config

REST0001_BODY = {
    "error": {
        "message": "Server error",
        "code": "REST0001",
        "details": "vManage server experience an unexpected error",
    }
}


def _make_dispatcher(specs_dir: Path, debug: DebugConfig) -> Dispatcher:
    index = SpecLoader(str(specs_dir), "20.99", read_write=True).load()
    auth = VManageAuth(
        host="vm.test",
        port=8443,
        username="admin",
        password="pwd",
        verify_ssl=False,
        use_jwt=True,
    )
    auth._jwt_token = "super-secret-jwt"
    auth._xsrf_token = "super-secret-xsrf"
    auth._token_expires_at = 1e18
    d = Dispatcher(
        base_url="https://vm.test:8443/dataservice",
        auth=auth,
        verify_ssl=False,
        debug=debug,
    )
    d.set_index(index)
    return d


# ---------------------------------------------------------------------------
# config — env parsing + defaults
# ---------------------------------------------------------------------------


def test_debug_defaults_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("SDWAN_MCP_DEBUG", "SDWAN_MCP_DEBUG_REDACT", "SDWAN_MCP_DEBUG_CAPTURE"):
        monkeypatch.delenv(var, raising=False)
    cfg = load_config(str(tmp_path / "nope.yaml"))
    assert cfg.debug.enabled is False
    assert cfg.debug.redact is True
    assert cfg.debug.capture == "errors"


def test_debug_env_enables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDWAN_MCP_DEBUG", "1")
    cfg = load_config(str(tmp_path / "nope.yaml"))
    assert cfg.debug.enabled is True
    assert cfg.debug.redact is True  # untouched default


def test_debug_env_redact_off_and_capture_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SDWAN_MCP_DEBUG", "true")
    monkeypatch.setenv("SDWAN_MCP_DEBUG_REDACT", "0")
    monkeypatch.setenv("SDWAN_MCP_DEBUG_CAPTURE", "all")
    cfg = load_config(str(tmp_path / "nope.yaml"))
    assert cfg.debug.enabled is True
    assert cfg.debug.redact is False
    assert cfg.debug.capture == "all"


def test_debug_env_overrides_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Env wins over YAML, mirroring the VMANAGE_* precedence."""
    cfg_file = tmp_path / "sdwan-mcp.yaml"
    cfg_file.write_text("debug:\n  enabled: false\n  capture: errors\n")
    monkeypatch.setenv("SDWAN_MCP_DEBUG", "1")
    monkeypatch.setenv("SDWAN_MCP_DEBUG_CAPTURE", "all")
    cfg = load_config(str(cfg_file))
    assert cfg.debug.enabled is True
    assert cfg.debug.capture == "all"


def test_debug_yaml_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("SDWAN_MCP_DEBUG", "SDWAN_MCP_DEBUG_REDACT", "SDWAN_MCP_DEBUG_CAPTURE"):
        monkeypatch.delenv(var, raising=False)
    cfg_file = tmp_path / "sdwan-mcp.yaml"
    cfg_file.write_text("debug:\n  enabled: true\n  redact: false\n")
    cfg = load_config(str(cfg_file))
    assert cfg.debug.enabled is True
    assert cfg.debug.redact is False


# ---------------------------------------------------------------------------
# redaction helper (unit)
# ---------------------------------------------------------------------------


def test_redact_headers_masks_auth_when_on() -> None:
    headers = {
        "Authorization": "Bearer abc",
        "X-XSRF-TOKEN": "xyz",
        "Cookie": "JSESSIONID=1",
        "Content-Type": "application/json",
    }
    out = _redact_headers(headers, redact=True)
    assert out["Authorization"] == "<redacted>"
    assert out["X-XSRF-TOKEN"] == "<redacted>"
    assert out["Cookie"] == "<redacted>"
    assert out["Content-Type"] == "application/json"  # non-secret untouched


def test_redact_headers_case_insensitive() -> None:
    out = _redact_headers({"authorization": "Bearer abc"}, redact=True)
    assert out["authorization"] == "<redacted>"


def test_redact_headers_passthrough_when_off() -> None:
    out = _redact_headers({"Authorization": "Bearer abc"}, redact=False)
    assert out["Authorization"] == "Bearer abc"


# ---------------------------------------------------------------------------
# dispatcher — capture behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_debug_key_when_disabled(specs_dir: Path) -> None:
    d = _make_dispatcher(specs_dir, DebugConfig(enabled=False))
    with respx.mock(assert_all_called=True) as router:
        router.get("https://vm.test:8443/dataservice/devices").mock(
            return_value=httpx.Response(500, json=REST0001_BODY)
        )
        result = await d.call("get_device_details_devices", {})
    assert isinstance(result, dict)
    assert result["error"] is True
    assert "debug" not in result


@pytest.mark.asyncio
async def test_debug_captures_on_error(specs_dir: Path, capsys: pytest.CaptureFixture) -> None:
    d = _make_dispatcher(specs_dir, DebugConfig(enabled=True))
    with respx.mock(assert_all_called=True) as router:
        router.get("https://vm.test:8443/dataservice/devices").mock(
            return_value=httpx.Response(500, json=REST0001_BODY)
        )
        result = await d.call("get_device_details_devices", {}, tool_name="monitoring")

    assert isinstance(result, dict)
    dbg = result["debug"]
    assert dbg["tool"] == "monitoring"
    assert dbg["action"] == "get_device_details_devices"
    assert dbg["request"]["method"] == "GET"
    assert dbg["request"]["path"] == "/devices"
    assert dbg["response"]["status_code"] == 500
    assert dbg["response"]["error_code"] == "REST0001"
    assert dbg["response"]["body"] == REST0001_BODY
    assert isinstance(dbg["timing_ms"], float)
    # also emitted to stderr as JSON
    err = capsys.readouterr().err
    assert "[dispatcher][debug]" in err


@pytest.mark.asyncio
async def test_debug_redacts_auth_headers_by_default(specs_dir: Path) -> None:
    d = _make_dispatcher(specs_dir, DebugConfig(enabled=True))
    with respx.mock(assert_all_called=True) as router:
        router.get("https://vm.test:8443/dataservice/devices").mock(
            return_value=httpx.Response(500, json=REST0001_BODY)
        )
        result = await d.call("get_device_details_devices", {})

    assert isinstance(result, dict)
    hdrs = result["debug"]["request"]["headers"]
    assert hdrs["Authorization"] == "<redacted>"
    assert hdrs["X-XSRF-TOKEN"] == "<redacted>"
    # The secret must not leak anywhere in the serialized debug object.
    assert "super-secret-jwt" not in json.dumps(result["debug"])


@pytest.mark.asyncio
async def test_debug_no_redact_keeps_token(specs_dir: Path) -> None:
    d = _make_dispatcher(specs_dir, DebugConfig(enabled=True, redact=False))
    with respx.mock(assert_all_called=True) as router:
        router.get("https://vm.test:8443/dataservice/devices").mock(
            return_value=httpx.Response(500, json=REST0001_BODY)
        )
        result = await d.call("get_device_details_devices", {})

    assert isinstance(result, dict)
    assert result["debug"]["request"]["headers"]["Authorization"] == "Bearer super-secret-jwt"


@pytest.mark.asyncio
async def test_debug_captures_request_body_shape(specs_dir: Path) -> None:
    """A POST forwards params straight to the body — debug must show that the
    'query' payload sits at the top level (the #72 request-shape gotcha)."""
    d = _make_dispatcher(specs_dir, DebugConfig(enabled=True))
    query = {"query": {"condition": "AND", "rules": []}}
    with respx.mock(assert_all_called=True) as router:
        router.post("https://vm.test:8443/dataservice/devices/abc/config").mock(
            return_value=httpx.Response(500, json=REST0001_BODY)
        )
        result = await d.call("post_device_actions_config", {"deviceId": "abc", **query})

    assert isinstance(result, dict)
    body = result["debug"]["request"]["body"]
    assert body["query"] == query["query"]  # top-level, not nested under "body"
    assert "deviceId" not in body  # consumed as a path param


@pytest.mark.asyncio
async def test_capture_errors_skips_successful_call(specs_dir: Path) -> None:
    d = _make_dispatcher(specs_dir, DebugConfig(enabled=True, capture="errors"))
    with respx.mock(assert_all_called=True) as router:
        router.get("https://vm.test:8443/dataservice/devices/count").mock(
            return_value=httpx.Response(200, json={"count": 3})
        )
        result = await d.call("get_device_details_count", {})
    assert result == {"count": 3}  # untouched, no debug key


@pytest.mark.asyncio
async def test_capture_all_attaches_on_success_dict(specs_dir: Path) -> None:
    d = _make_dispatcher(specs_dir, DebugConfig(enabled=True, capture="all"))
    with respx.mock(assert_all_called=True) as router:
        router.get("https://vm.test:8443/dataservice/devices/count").mock(
            return_value=httpx.Response(200, json={"count": 3})
        )
        result = await d.call("get_device_details_count", {})
    assert isinstance(result, dict)
    assert result["count"] == 3
    assert result["debug"]["response"]["status_code"] == 200


@pytest.mark.asyncio
async def test_capture_all_leaves_list_success_unwrapped(
    specs_dir: Path, capsys: pytest.CaptureFixture
) -> None:
    """A list-shaped success can't carry a debug key without reshaping; it is
    returned verbatim and the record goes to stderr only."""
    d = _make_dispatcher(specs_dir, DebugConfig(enabled=True, capture="all"))
    with respx.mock(assert_all_called=True) as router:
        router.get("https://vm.test:8443/dataservice/devices/count").mock(
            return_value=httpx.Response(200, json=[1, 2, 3])
        )
        result = await d.call("get_device_details_count", {})
    assert result == [1, 2, 3]
    assert "[dispatcher][debug]" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# CLI flags
# ---------------------------------------------------------------------------


def test_cli_debug_flags_default_none() -> None:
    args = parse_args([])
    assert args.debug is None
    assert args.debug_all_calls is None
    assert args.debug_no_redact is None


def test_cli_debug_flags_set() -> None:
    args = parse_args(["--debug", "--debug-all-calls", "--debug-no-redact"])
    assert args.debug is True
    assert args.debug_all_calls is True
    assert args.debug_no_redact is True


# ---------------------------------------------------------------------------
# env-disable semantics (SDWAN_MCP_DEBUG=0/false must yield enabled=False)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["0", "false", "False", "no", "off"])
def test_debug_env_disable_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """The `if value:` forwarding guard treats "0" as truthy, so disabling
    relies on pydantic's bool coercion — pin that it actually disables."""
    monkeypatch.setenv("SDWAN_MCP_DEBUG", value)
    cfg = load_config(str(tmp_path / "nope.yaml"))
    assert cfg.debug.enabled is False


# ---------------------------------------------------------------------------
# CLI-over-config merge (resolve_debug_config) — the None-default invariant
# ---------------------------------------------------------------------------


def test_resolve_debug_unset_flags_preserve_config() -> None:
    base = DebugConfig(enabled=True, capture="all", redact=False)
    out = resolve_debug_config(base, debug=None, all_calls=None, no_redact=None)
    assert out == base  # all-None must not override env/YAML state


def test_resolve_debug_flag_enables_without_touching_other_fields() -> None:
    base = DebugConfig(enabled=False, capture="all", redact=True)
    out = resolve_debug_config(base, debug=True, all_calls=None, no_redact=None)
    assert out.enabled is True
    assert out.capture == "all"  # untouched
    assert out.redact is True


def test_resolve_debug_all_and_no_redact_flags() -> None:
    base = DebugConfig(enabled=True)
    out = resolve_debug_config(base, debug=None, all_calls=True, no_redact=True)
    assert out.capture == "all"
    assert out.redact is False


# ---------------------------------------------------------------------------
# body / query credential scrubbing (#72 review finding — headers aren't enough)
# ---------------------------------------------------------------------------


def test_redact_data_masks_credential_keys() -> None:
    obj = {"token": "live-xsrf", "data": [{"sessionId": "s"}], "field": "ok"}
    out = _redact_data(obj, redact=True)
    assert out["token"] == "<redacted>"
    assert out["data"][0]["sessionId"] == "<redacted>"
    assert out["field"] == "ok"  # non-sensitive passes through


def test_redact_data_passthrough_when_off() -> None:
    obj = {"token": "live-xsrf"}
    assert _redact_data(obj, redact=False) == obj


@pytest.mark.asyncio
async def test_debug_scrubs_token_returning_response_body(specs_dir: Path) -> None:
    """GET /client/token-style endpoints return a live token in the BODY;
    redaction must mask it, not just the auth headers."""
    d = _make_dispatcher(specs_dir, DebugConfig(enabled=True, capture="all"))
    with respx.mock(assert_all_called=True) as router:
        router.get("https://vm.test:8443/dataservice/devices/count").mock(
            return_value=httpx.Response(200, json={"token": "LIVE-XSRF-TOKEN"})
        )
        result = await d.call("get_device_details_count", {})

    assert isinstance(result, dict)
    assert result["token"] == "LIVE-XSRF-TOKEN"  # real payload untouched
    # ...but the captured copy in debug must be scrubbed, and the secret must not
    # appear anywhere in the serialized debug object.
    assert result["debug"]["response"]["body"]["token"] == "<redacted>"
    assert "LIVE-XSRF-TOKEN" not in json.dumps(result["debug"])


@pytest.mark.asyncio
async def test_debug_caps_oversized_body(specs_dir: Path) -> None:
    big = {"blob": "x" * 50_000}
    d = _make_dispatcher(specs_dir, DebugConfig(enabled=True))
    with respx.mock(assert_all_called=True) as router:
        router.get("https://vm.test:8443/dataservice/devices").mock(
            return_value=httpx.Response(500, json=big)
        )
        result = await d.call("get_device_details_devices", {})

    body = result["debug"]["response"]["body"]
    assert body["_truncated"] is True
    assert body["_original_chars"] > 20_000


def test_cap_body_passes_small_payload() -> None:
    small = {"a": 1}
    assert _cap_body(small) is small


# ---------------------------------------------------------------------------
# Set-Cookie response-header redaction (response-only leak surface)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debug_redacts_response_set_cookie(
    specs_dir: Path, capsys: pytest.CaptureFixture
) -> None:
    d = _make_dispatcher(specs_dir, DebugConfig(enabled=True))
    with respx.mock(assert_all_called=True) as router:
        router.get("https://vm.test:8443/dataservice/devices").mock(
            return_value=httpx.Response(
                500, json=REST0001_BODY, headers={"Set-Cookie": "JSESSIONID=leakme; Path=/"}
            )
        )
        result = await d.call("get_device_details_devices", {})

    assert isinstance(result, dict)
    resp_headers = result["debug"]["response"]["headers"]
    # httpx lowercases header names; the value must be masked either way.
    assert resp_headers.get("set-cookie", resp_headers.get("Set-Cookie")) == "<redacted>"
    # the cookie must not leak into the result OR the stderr log
    assert "leakme" not in json.dumps(result["debug"])
    assert "leakme" not in capsys.readouterr().err


# ---------------------------------------------------------------------------
# transport-level failure (httpx.RequestError) capture path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debug_captures_request_error(specs_dir: Path, capsys: pytest.CaptureFixture) -> None:
    d = _make_dispatcher(specs_dir, DebugConfig(enabled=True))
    with respx.mock(assert_all_called=True) as router:
        router.get("https://vm.test:8443/dataservice/devices/count").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        result = await d.call("get_device_details_count", {})

    assert isinstance(result, dict)
    assert result["error"] is True
    dbg = result["debug"]
    assert "connection refused" in dbg["request_error"]
    assert "response" not in dbg  # no response was received
    assert dbg["request"]["method"] == "GET"
    assert "[dispatcher][debug]" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# capture="all" on an error — failures captured under BOTH modes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capture_all_still_captures_error_once(specs_dir: Path) -> None:
    d = _make_dispatcher(specs_dir, DebugConfig(enabled=True, capture="all"))
    with respx.mock(assert_all_called=True) as router:
        router.get("https://vm.test:8443/dataservice/devices").mock(
            return_value=httpx.Response(500, json=REST0001_BODY)
        )
        result = await d.call("get_device_details_devices", {})

    assert isinstance(result, dict)
    assert result["error"] is True
    assert result["debug"]["response"]["status_code"] == 500
