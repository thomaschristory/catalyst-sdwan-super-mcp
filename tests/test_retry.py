"""Tests for the dispatcher's configurable retry / timeout policy (#9)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from sdwan_mcp.auth import VManageAuth
from sdwan_mcp.config import RetryConfig
from sdwan_mcp.dispatcher import Dispatcher
from sdwan_mcp.loader import SpecLoader


def _make_dispatcher(
    specs_dir: Path,
    *,
    retry: RetryConfig | None = None,
    timeout: float = 30.0,
) -> Dispatcher:
    index = SpecLoader(str(specs_dir), "20.99", read_write=True).load()
    auth = VManageAuth(
        host="vm.test",
        port=8443,
        username="admin",
        password="pwd",
        verify_ssl=False,
        use_jwt=True,
    )
    auth._jwt_token = "fake-jwt"
    auth._xsrf_token = "fake-xsrf"
    auth._token_expires_at = 1e18

    d = Dispatcher(
        base_url="https://vm.test:8443/dataservice",
        auth=auth,
        verify_ssl=False,
        timeout=timeout,
        retry=retry,
    )
    d.set_index(index)
    return d


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip real sleeps in backoff so tests run fast."""
    import asyncio

    async def _instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _instant)


@pytest.mark.asyncio
async def test_retry_recovers_after_one_503(specs_dir: Path) -> None:
    retry = RetryConfig(max_attempts=3, statuses=(503,), backoff_base=0.0, backoff_cap=0.0)
    dispatcher = _make_dispatcher(specs_dir, retry=retry)

    with respx.mock(assert_all_called=True) as router:
        route = router.get("https://vm.test:8443/dataservice/devices/count").mock(
            side_effect=[
                httpx.Response(503),
                httpx.Response(200, json={"count": 42}),
            ]
        )
        result = await dispatcher.call("get_device_details_count", {})

    assert route.call_count == 2
    assert result == {"count": 42}


@pytest.mark.asyncio
async def test_retry_exhausted_returns_error(specs_dir: Path) -> None:
    retry = RetryConfig(max_attempts=3, statuses=(503,), backoff_base=0.0, backoff_cap=0.0)
    dispatcher = _make_dispatcher(specs_dir, retry=retry)

    with respx.mock(assert_all_called=True) as router:
        route = router.get("https://vm.test:8443/dataservice/devices/count").mock(
            return_value=httpx.Response(503)
        )
        result = await dispatcher.call("get_device_details_count", {})

    assert route.call_count == 3
    assert isinstance(result, dict)
    assert result.get("error") is True
    assert result.get("status_code") == 503


@pytest.mark.asyncio
async def test_retry_skips_mutating_verbs_by_default(specs_dir: Path) -> None:
    retry = RetryConfig(
        max_attempts=3,
        statuses=(503,),
        backoff_base=0.0,
        backoff_cap=0.0,
        retry_mutating=False,
    )
    dispatcher = _make_dispatcher(specs_dir, retry=retry)

    with respx.mock(assert_all_called=True) as router:
        route = router.post("https://vm.test:8443/dataservice/devices/abc/config").mock(
            return_value=httpx.Response(503)
        )
        result = await dispatcher.call(
            "post_device_actions_config", {"deviceId": "abc", "name": "edge-1"}
        )

    assert route.call_count == 1
    assert isinstance(result, dict)
    assert result.get("status_code") == 503


@pytest.mark.asyncio
async def test_retry_mutating_when_enabled(specs_dir: Path) -> None:
    retry = RetryConfig(
        max_attempts=2,
        statuses=(503,),
        backoff_base=0.0,
        backoff_cap=0.0,
        retry_mutating=True,
    )
    dispatcher = _make_dispatcher(specs_dir, retry=retry)

    with respx.mock(assert_all_called=True) as router:
        route = router.post("https://vm.test:8443/dataservice/devices/abc/config").mock(
            side_effect=[
                httpx.Response(503),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        result = await dispatcher.call(
            "post_device_actions_config", {"deviceId": "abc", "name": "edge-1"}
        )

    assert route.call_count == 2
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_retry_on_timeout(specs_dir: Path) -> None:
    retry = RetryConfig(max_attempts=3, statuses=(), backoff_base=0.0, backoff_cap=0.0)
    dispatcher = _make_dispatcher(specs_dir, retry=retry)

    with respx.mock(assert_all_called=True) as router:
        route = router.get("https://vm.test:8443/dataservice/devices/count").mock(
            side_effect=[
                httpx.TimeoutException("timed out"),
                httpx.Response(200, json={"count": 1}),
            ]
        )
        result = await dispatcher.call("get_device_details_count", {})

    assert route.call_count == 2
    assert result == {"count": 1}


@pytest.mark.asyncio
async def test_timeout_exhausted_returns_error(specs_dir: Path) -> None:
    retry = RetryConfig(max_attempts=2, statuses=(), backoff_base=0.0, backoff_cap=0.0)
    dispatcher = _make_dispatcher(specs_dir, retry=retry)

    with respx.mock(assert_all_called=True) as router:
        route = router.get("https://vm.test:8443/dataservice/devices/count").mock(
            side_effect=httpx.TimeoutException("timed out")
        )
        result = await dispatcher.call("get_device_details_count", {})

    assert route.call_count == 2
    assert isinstance(result, dict)
    assert result.get("error") is True
    assert "timed out" in result.get("message", "").lower() or "Request failed" in result.get(
        "message", ""
    )


@pytest.mark.asyncio
async def test_no_retry_on_non_retryable_status(specs_dir: Path) -> None:
    retry = RetryConfig(max_attempts=3, statuses=(503,), backoff_base=0.0, backoff_cap=0.0)
    dispatcher = _make_dispatcher(specs_dir, retry=retry)

    with respx.mock(assert_all_called=True) as router:
        route = router.get("https://vm.test:8443/dataservice/devices/count").mock(
            return_value=httpx.Response(404, json={"error": "not found"})
        )
        result = await dispatcher.call("get_device_details_count", {})

    assert route.call_count == 1
    assert isinstance(result, dict)
    assert result.get("status_code") == 404


@pytest.mark.asyncio
async def test_disabled_retry_max_attempts_one(specs_dir: Path) -> None:
    retry = RetryConfig(max_attempts=1, statuses=(503,))
    dispatcher = _make_dispatcher(specs_dir, retry=retry)

    with respx.mock(assert_all_called=True) as router:
        route = router.get("https://vm.test:8443/dataservice/devices/count").mock(
            return_value=httpx.Response(503)
        )
        result = await dispatcher.call("get_device_details_count", {})

    assert route.call_count == 1
    assert isinstance(result, dict)
    assert result.get("status_code") == 503


@pytest.mark.asyncio
async def test_timeout_passes_through_to_httpx_client(specs_dir: Path) -> None:
    dispatcher = _make_dispatcher(specs_dir, timeout=7.5)
    # Internal: verify httpx client picked up the configured timeout
    assert dispatcher._client.timeout.connect == 7.5
    assert dispatcher._client.timeout.read == 7.5


def test_retry_config_defaults() -> None:
    cfg = RetryConfig()
    assert cfg.max_attempts == 3
    assert 502 in cfg.statuses
    assert 503 in cfg.statuses
    assert 504 in cfg.statuses
    assert cfg.retry_mutating is False
    assert cfg.backoff_base > 0
    assert cfg.backoff_cap >= cfg.backoff_base
