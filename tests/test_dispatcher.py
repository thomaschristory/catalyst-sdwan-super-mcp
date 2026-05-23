"""Tests for the request dispatcher — auth, param routing, retry."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from sdwan_mcp.auth import VManageAuth
from sdwan_mcp.dispatcher import Dispatcher
from sdwan_mcp.loader import SpecLoader


@pytest.fixture
def dispatcher(specs_dir: Path) -> Dispatcher:
    index = SpecLoader(str(specs_dir), "20.99", read_write=True).load()
    auth = VManageAuth(
        host="vm.test",
        port=8443,
        username="admin",
        password="pwd",
        verify_ssl=False,
        use_jwt=True,
    )
    # Pre-populate auth state so we don't need to mock /j_security_check.
    auth._jwt_token = "fake-jwt"
    auth._xsrf_token = "fake-xsrf"
    auth._token_expires_at = 1e18

    d = Dispatcher(
        base_url="https://vm.test:8443/dataservice",
        auth=auth,
        verify_ssl=False,
    )
    d.set_index(index)
    return d


@pytest.mark.asyncio
async def test_dispatcher_substitutes_path_params(dispatcher: Dispatcher) -> None:
    with respx.mock(assert_all_called=True) as router:
        route = router.get("https://vm.test:8443/dataservice/devices/10.0.0.1/info").mock(
            return_value=httpx.Response(200, json={"deviceId": "10.0.0.1"})
        )
        result = await dispatcher.call("get_device_details_info", {"deviceId": "10.0.0.1"})

    assert route.called
    assert result == {"deviceId": "10.0.0.1"}


@pytest.mark.asyncio
async def test_dispatcher_routes_query_params(dispatcher: Dispatcher) -> None:
    with respx.mock(assert_all_called=True) as router:
        route = router.get("https://vm.test:8443/dataservice/devices").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        await dispatcher.call("get_device_details_devices", {"site-id": "500"})

    assert route.calls.last.request.url.params["site-id"] == "500"


@pytest.mark.asyncio
async def test_dispatcher_missing_path_param_returns_error(dispatcher: Dispatcher) -> None:
    result = await dispatcher.call("get_device_details_info", {})
    assert isinstance(result, dict)
    assert result.get("error") is True
    assert "deviceId" in result["message"]


@pytest.mark.asyncio
async def test_dispatcher_unknown_action_returns_error(dispatcher: Dispatcher) -> None:
    result = await dispatcher.call("does_not_exist", {})
    assert isinstance(result, dict)
    assert result.get("error") is True
    assert "Unknown action" in result["message"]


@pytest.mark.asyncio
async def test_dispatcher_post_routes_body(dispatcher: Dispatcher) -> None:
    with respx.mock(assert_all_called=True) as router:
        route = router.post("https://vm.test:8443/dataservice/devices/abc/config").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        await dispatcher.call(
            "post_device_actions_config",
            {"deviceId": "abc", "name": "edge-1"},
        )

    body = route.calls.last.request.content.decode()
    assert "edge-1" in body
    # deviceId must be consumed as a path param, not echoed in the body
    assert '"deviceId"' not in body
