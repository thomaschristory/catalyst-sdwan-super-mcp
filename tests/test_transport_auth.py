"""Tests for transport_auth: decide_bind() and BearerAuthMiddleware."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from sdwan_mcp import server
from sdwan_mcp.transport_auth import BearerAuthMiddleware, decide_bind


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_decide_bind_loopback_never_demoted(host: str) -> None:
    effective, warnings = decide_bind(host=host, auth_type="none", insecure_ok=False)
    assert effective == host
    assert warnings == []


def test_decide_bind_public_with_bearer_passes() -> None:
    effective, warnings = decide_bind(host="0.0.0.0", auth_type="bearer", insecure_ok=False)
    assert effective == "0.0.0.0"
    assert warnings == []


def test_decide_bind_public_with_none_demotes_to_loopback() -> None:
    effective, warnings = decide_bind(host="0.0.0.0", auth_type="none", insecure_ok=False)
    assert effective == "127.0.0.1"
    assert any("Demoting bind to 127.0.0.1" in w for w in warnings)
    assert any("--insecure-allow-public" in w for w in warnings)


def test_decide_bind_public_with_none_and_override_passes() -> None:
    effective, warnings = decide_bind(host="0.0.0.0", auth_type="none", insecure_ok=True)
    assert effective == "0.0.0.0"
    assert warnings == []


def test_decide_bind_arbitrary_public_host_demoted() -> None:
    # Any non-loopback host without bearer + without override gets demoted.
    effective, warnings = decide_bind(host="10.0.0.5", auth_type="none", insecure_ok=False)
    assert effective == "127.0.0.1"
    assert len(warnings) >= 1


def _make_app(expected_token: str) -> Starlette:
    async def ok(_: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    return Starlette(
        routes=[Route("/x", ok)],
        middleware=[Middleware(BearerAuthMiddleware, expected_token=expected_token)],
    )


def test_bearer_middleware_accepts_correct_token() -> None:
    client = TestClient(_make_app("good-token"))
    resp = client.get("/x", headers={"Authorization": "Bearer good-token"})
    assert resp.status_code == 200
    assert resp.text == "ok"


def test_bearer_middleware_rejects_missing_header() -> None:
    client = TestClient(_make_app("good-token"))
    resp = client.get("/x")
    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "Bearer"
    body = json.loads(resp.text)
    assert "missing or malformed" in body["error"].lower()


def test_bearer_middleware_rejects_wrong_scheme() -> None:
    client = TestClient(_make_app("good-token"))
    resp = client.get("/x", headers={"Authorization": "Basic Zm9vOmJhcg=="})
    assert resp.status_code == 401
    body = json.loads(resp.text)
    assert "missing or malformed" in body["error"].lower()


def test_bearer_middleware_rejects_bearer_without_token() -> None:
    client = TestClient(_make_app("good-token"))
    resp = client.get("/x", headers={"Authorization": "Bearer "})
    assert resp.status_code == 401
    body = json.loads(resp.text)
    assert "missing or malformed" in body["error"].lower()


def test_bearer_middleware_rejects_wrong_token() -> None:
    client = TestClient(_make_app("good-token"))
    resp = client.get("/x", headers={"Authorization": "Bearer wrong-token"})
    assert resp.status_code == 401
    body = json.loads(resp.text)
    assert "invalid token" in body["error"].lower()


def test_bearer_middleware_uses_constant_time_compare() -> None:
    """compare_digest must be called with (supplied_token, expected_token)."""
    with patch("sdwan_mcp.transport_auth.hmac.compare_digest", return_value=True) as mock_cd:
        client = TestClient(_make_app("good-token"))
        resp = client.get("/x", headers={"Authorization": "Bearer anything"})
        assert resp.status_code == 200
        mock_cd.assert_called_once_with("anything", "good-token")


# ---------------------------------------------------------------------------
# Server-level smoke tests
# ---------------------------------------------------------------------------


def _write_min_config(
    tmp_path: Path,
    *,
    transport_mode: str,
    host: str,
    auth_type: str,
    token: str = "",
) -> Path:
    token_line = f'    token: "{token}"\n' if token else ""
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        f"""\
vmanage:
  host: vm.test
  username: u
  password: p
sdwan:
  specs_dir: ./specs
  active_version: '20.18'
transport:
  mode: {transport_mode}
  host: {host}
  port: 8000
  auth:
    type: {auth_type}
{token_line}"""
    )
    return cfg


def _make_args(config_path: Path, *, insecure: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        config=str(config_path),
        transport=None,
        host=None,
        port=None,
        read_write=False,
        version=None,
        diff=None,
        max_actions_per_tool=None,
        insecure_allow_public=insecure,
    )


@pytest.mark.asyncio
async def test_server_stdio_does_not_install_auth_middleware(tmp_path: Path) -> None:
    config_path = _write_min_config(
        tmp_path, transport_mode="stdio", host="127.0.0.1", auth_type="none"
    )

    with (
        patch("sdwan_mcp.server.SpecLoader") as loader_cls,
        patch("sdwan_mcp.server.VManageAuth"),
        patch("sdwan_mcp.server.Dispatcher") as disp_cls,
        patch("sdwan_mcp.server.register_tools", return_value=0),
    ):
        loader_cls.return_value.load.return_value = MagicMock()
        disp_cls.return_value.connect = AsyncMock()

        _, _, transport_mode, host, _port, middleware = await server._connect_and_register(
            _make_args(config_path)
        )

    assert transport_mode == "stdio"
    assert host == "127.0.0.1"
    assert middleware == []


@pytest.mark.asyncio
async def test_server_http_with_bearer_installs_middleware(
    tmp_path: Path,
) -> None:
    config_path = _write_min_config(
        tmp_path,
        transport_mode="streamable-http",
        host="0.0.0.0",
        auth_type="bearer",
        token="real-token",
    )

    with (
        patch("sdwan_mcp.server.SpecLoader") as loader_cls,
        patch("sdwan_mcp.server.VManageAuth"),
        patch("sdwan_mcp.server.Dispatcher") as disp_cls,
        patch("sdwan_mcp.server.register_tools", return_value=0),
    ):
        loader_cls.return_value.load.return_value = MagicMock()
        disp_cls.return_value.connect = AsyncMock()

        _, _, _, host, _, middleware = await server._connect_and_register(_make_args(config_path))

    assert host == "0.0.0.0"  # not demoted; bearer is configured
    assert len(middleware) == 1
    assert middleware[0].cls is BearerAuthMiddleware


@pytest.mark.asyncio
async def test_server_http_public_no_auth_demotes_to_loopback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = _write_min_config(
        tmp_path, transport_mode="streamable-http", host="0.0.0.0", auth_type="none"
    )

    with (
        patch("sdwan_mcp.server.SpecLoader") as loader_cls,
        patch("sdwan_mcp.server.VManageAuth"),
        patch("sdwan_mcp.server.Dispatcher") as disp_cls,
        patch("sdwan_mcp.server.register_tools", return_value=0),
    ):
        loader_cls.return_value.load.return_value = MagicMock()
        disp_cls.return_value.connect = AsyncMock()

        _, _, _, host, _, middleware = await server._connect_and_register(
            _make_args(config_path, insecure=False)
        )

    assert host == "127.0.0.1"  # demoted
    assert middleware == []
    captured = capsys.readouterr()
    assert "Demoting bind to 127.0.0.1" in captured.err
    assert "--insecure-allow-public" in captured.err


@pytest.mark.asyncio
async def test_server_http_public_no_auth_with_override_keeps_bind(
    tmp_path: Path,
) -> None:
    config_path = _write_min_config(
        tmp_path, transport_mode="streamable-http", host="0.0.0.0", auth_type="none"
    )

    with (
        patch("sdwan_mcp.server.SpecLoader") as loader_cls,
        patch("sdwan_mcp.server.VManageAuth"),
        patch("sdwan_mcp.server.Dispatcher") as disp_cls,
        patch("sdwan_mcp.server.register_tools", return_value=0),
    ):
        loader_cls.return_value.load.return_value = MagicMock()
        disp_cls.return_value.connect = AsyncMock()

        _, _, _, host, _, middleware = await server._connect_and_register(
            _make_args(config_path, insecure=True)
        )

    assert host == "0.0.0.0"
    assert middleware == []
