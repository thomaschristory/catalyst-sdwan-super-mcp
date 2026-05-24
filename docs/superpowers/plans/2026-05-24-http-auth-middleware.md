# HTTP Transport Auth Middleware Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a shared-token bearer auth layer to the SSE / streamable-http transports, with a startup-time bind-demotion safety net so a non-loopback bind without auth is impossible without an explicit opt-out flag.

**Architecture:** New `TransportAuthConfig` block in `config.py` (discriminated by `type`: `none|bearer`). A standalone `sdwan_mcp/transport_auth.py` module holds a pure `decide_bind()` function and a Starlette `BearerAuthMiddleware`. `server.py` wires `--insecure-allow-public`, calls `decide_bind()` to potentially demote the host to loopback, and passes the middleware to `mcp.run()` via FastMCP's `middleware=[Middleware(...)]` kwarg (FastMCP 3.3.1 forwards this to the underlying Starlette app).

**Tech Stack:** Python 3.11+, FastMCP 3.3.1, Starlette middleware, `hmac.compare_digest`, pytest + `starlette.testclient.TestClient`.

**Spec:** `docs/superpowers/specs/2026-05-24-http-auth-middleware-design.md`

---

## File Structure

**New:**
- `sdwan_mcp/transport_auth.py` — `decide_bind()` pure function + `BearerAuthMiddleware` class.
- `tests/test_transport_auth.py` — unit tests for `decide_bind` and the middleware.

**Modified:**
- `sdwan_mcp/config.py` — add `TransportAuthConfig` dataclass, attach to `TransportConfig`, parse + validate `transport.auth` block.
- `sdwan_mcp/server.py` — add `--insecure-allow-public` flag; call `decide_bind()` and emit demotion warnings to stderr; pass middleware to `mcp.run()` when bearer auth is active.
- `tests/test_config.py` — add tests for the new auth config parsing and validation errors.
- `config.yaml` — add commented `transport.auth` block.
- `.env.example` — add `SDWAN_MCP_TOKEN=`.
- `docker-compose.yml` — switch the example to demonstrate token-based auth.
- `CHANGELOG.md` — call out the bind-demotion behavior change.
- `docs/reference/configuration.md` — document `transport.auth`.
- `docs/reference/cli.md` — document `--insecure-allow-public`.
- `docs/guides/mcp-clients.md` — show clients how to send the `Authorization: Bearer` header.

---

## Task 1: TransportAuthConfig dataclass and validation

**Files:**
- Modify: `sdwan_mcp/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing tests for new auth config parsing and validation**

Append to `tests/test_config.py`:

```python
def test_transport_auth_defaults_to_none(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "vmanage:\n  host: vm.test\nsdwan:\n  active_version: '20.18'\n"
    )
    config = load_config(str(cfg))
    assert config.transport.auth.type == "none"
    assert config.transport.auth.token == ""


def test_transport_auth_bearer_with_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SDWAN_MCP_TOKEN", "s3cret-token")
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        """\
vmanage:
  host: vm.test
sdwan:
  active_version: '20.18'
transport:
  mode: streamable-http
  host: 0.0.0.0
  port: 8000
  auth:
    type: bearer
    token: "${SDWAN_MCP_TOKEN}"
"""
    )
    config = load_config(str(cfg))
    assert config.transport.auth.type == "bearer"
    assert config.transport.auth.token == "s3cret-token"


def test_transport_auth_bearer_missing_token_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        """\
vmanage:
  host: vm.test
sdwan:
  active_version: '20.18'
transport:
  mode: streamable-http
  auth:
    type: bearer
"""
    )
    with pytest.raises(ValueError, match="transport.auth.type=bearer requires"):
        load_config(str(cfg))


def test_transport_auth_none_with_token_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        """\
vmanage:
  host: vm.test
sdwan:
  active_version: '20.18'
transport:
  mode: streamable-http
  auth:
    type: none
    token: leftover-paste
"""
    )
    with pytest.raises(ValueError, match="token configured but transport.auth.type=none"):
        load_config(str(cfg))


def test_transport_auth_unknown_type_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        """\
vmanage:
  host: vm.test
sdwan:
  active_version: '20.18'
transport:
  auth:
    type: oidc
"""
    )
    with pytest.raises(ValueError, match="unknown transport.auth.type"):
        load_config(str(cfg))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -k transport_auth -v`
Expected: 5 FAIL with `AttributeError: 'TransportConfig' object has no attribute 'auth'` (or similar).

- [ ] **Step 3: Add the dataclass and parsing in `sdwan_mcp/config.py`**

Insert this dataclass directly **above** the existing `@dataclass class TransportConfig`:

```python
_VALID_AUTH_TYPES: frozenset[str] = frozenset({"none", "bearer"})


@dataclass
class TransportAuthConfig:
    """Authentication for the HTTP transports (SSE, streamable-http).

    type='none' means no auth — only safe on loopback or behind a trusted
    authenticating reverse proxy (see --insecure-allow-public in server.py).
    type='bearer' enforces an `Authorization: Bearer <token>` header on
    every request, compared in constant time.
    """

    type: str = "none"
    token: str = ""
```

Then modify the existing `TransportConfig` to include the new field:

```python
@dataclass
class TransportConfig:
    mode: str = "stdio"  # stdio | sse | streamable-http
    host: str = "127.0.0.1"
    port: int = 8000
    auth: TransportAuthConfig = field(default_factory=TransportAuthConfig)
```

Finally, replace the existing `transport = TransportConfig(...)` construction at the end of `load_config()` with:

```python
    auth_raw = transport_raw.get("auth", {}) or {}
    auth_type = str(auth_raw.get("type", "none"))
    auth_token = str(auth_raw.get("token", ""))

    if auth_type not in _VALID_AUTH_TYPES:
        raise ValueError(
            f"unknown transport.auth.type: {auth_type!r}. "
            f"Choose one of {sorted(_VALID_AUTH_TYPES)}."
        )
    if auth_type == "bearer" and not auth_token:
        raise ValueError(
            "transport.auth.type=bearer requires a non-empty transport.auth.token "
            "(use ${ENV_VAR} interpolation)."
        )
    if auth_type == "none" and auth_token:
        raise ValueError(
            "token configured but transport.auth.type=none — "
            "set type: bearer to enable it, or remove the token."
        )

    transport = TransportConfig(
        mode=transport_raw.get("mode", "stdio"),
        host=transport_raw.get("host", "127.0.0.1"),
        port=int(transport_raw.get("port", 8000)),
        auth=TransportAuthConfig(type=auth_type, token=auth_token),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: all PASS (including the 5 new ones).

- [ ] **Step 5: Commit**

```bash
git add sdwan_mcp/config.py tests/test_config.py
git commit -m "feat(config): add transport.auth block (none|bearer) with validation"
```

---

## Task 2: `decide_bind()` pure function

**Files:**
- Create: `sdwan_mcp/transport_auth.py`
- Test: `tests/test_transport_auth.py`

- [ ] **Step 1: Write failing tests for `decide_bind`**

Create `tests/test_transport_auth.py`:

```python
"""Tests for transport_auth: decide_bind() and BearerAuthMiddleware."""

from __future__ import annotations

import pytest

from sdwan_mcp.transport_auth import decide_bind


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_transport_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sdwan_mcp.transport_auth'`.

- [ ] **Step 3: Create `sdwan_mcp/transport_auth.py` with `decide_bind`**

```python
"""transport_auth.py — HTTP transport auth (bearer token) and bind-safety logic.

Two responsibilities:
1. decide_bind(): a pure function that decides whether to honor the requested
   bind host or demote it to loopback, given the configured auth type and
   the --insecure-allow-public override flag. Easy to unit test in isolation.
2. BearerAuthMiddleware: a Starlette middleware enforcing
   `Authorization: Bearer <token>` on every HTTP request.
"""

from __future__ import annotations

_LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})


def decide_bind(
    host: str,
    auth_type: str,
    insecure_ok: bool,
) -> tuple[str, list[str]]:
    """Decide the effective bind host given the configured auth and override.

    Rules:
      - Loopback hosts (127.0.0.1, ::1, localhost) are never demoted.
      - Non-loopback + auth_type == "bearer" → bind as requested.
      - Non-loopback + auth_type == "none" + insecure_ok=False → demote
        to 127.0.0.1 with warnings explaining how to opt in.
      - Non-loopback + auth_type == "none" + insecure_ok=True → bind as
        requested (operator has acknowledged the risk).

    Returns:
      (effective_host, warning_lines)
    """
    if host in _LOOPBACK_HOSTS:
        return host, []
    if auth_type == "bearer":
        return host, []
    if insecure_ok:
        return host, []

    warnings = [
        f"refusing to bind {host} with transport.auth.type=none.",
        "Demoting bind to 127.0.0.1. To expose externally, set "
        "transport.auth.type=bearer",
        "and transport.auth.token, OR set transport.auth.type=none "
        "explicitly AND pass",
        "--insecure-allow-public to acknowledge the risk.",
    ]
    return "127.0.0.1", warnings
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_transport_auth.py -v`
Expected: all 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add sdwan_mcp/transport_auth.py tests/test_transport_auth.py
git commit -m "feat(transport-auth): add decide_bind() pure function for bind safety"
```

---

## Task 3: `BearerAuthMiddleware`

**Files:**
- Modify: `sdwan_mcp/transport_auth.py`
- Modify: `tests/test_transport_auth.py`

- [ ] **Step 1: Write failing tests for the middleware**

Append to `tests/test_transport_auth.py`:

```python
import json
from typing import Any
from unittest.mock import patch

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from sdwan_mcp.transport_auth import BearerAuthMiddleware


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
    """compare_digest must be called for the actual token comparison."""
    with patch("sdwan_mcp.transport_auth.hmac.compare_digest", return_value=True) as mock_cd:
        client = TestClient(_make_app("good-token"))
        resp = client.get("/x", headers={"Authorization": "Bearer anything"})
        assert resp.status_code == 200
        assert mock_cd.called
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_transport_auth.py -v`
Expected: existing `decide_bind` tests PASS; the 6 new middleware tests FAIL with `ImportError: cannot import name 'BearerAuthMiddleware'`.

- [ ] **Step 3: Add `BearerAuthMiddleware` to `sdwan_mcp/transport_auth.py`**

Append to `sdwan_mcp/transport_auth.py`:

```python
import hmac
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Enforce `Authorization: Bearer <token>` using constant-time comparison.

    On failure, returns a 401 JSON body with `WWW-Authenticate: Bearer`.
    Never logs the supplied token (not even a prefix — leaks rotation state).
    """

    def __init__(self, app: ASGIApp, expected_token: str) -> None:
        super().__init__(app)
        if not expected_token:
            raise ValueError("BearerAuthMiddleware requires a non-empty expected_token")
        self._expected_token = expected_token

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return self._unauthorized(request, "missing or malformed Authorization header")

        if not hmac.compare_digest(token, self._expected_token):
            return self._unauthorized(request, "invalid token")

        return await call_next(request)

    def _unauthorized(self, request: Request, reason: str) -> Response:
        client_host = request.client.host if request.client else "unknown"
        logger.warning(
            "auth rejected: remote=%s path=%s reason=%s",
            client_host,
            request.url.path,
            reason,
        )
        return JSONResponse(
            {"error": reason},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_transport_auth.py -v`
Expected: all 11 PASS (5 `decide_bind` + 6 middleware).

- [ ] **Step 5: Commit**

```bash
git add sdwan_mcp/transport_auth.py tests/test_transport_auth.py
git commit -m "feat(transport-auth): add BearerAuthMiddleware (constant-time bearer check)"
```

---

## Task 4: Wire auth into `server.py`

**Files:**
- Modify: `sdwan_mcp/server.py`

This task does not have its own unit tests — the wiring is integration-style and is covered indirectly by the smoke tests added in Task 5. We do verify the help text manually.

- [ ] **Step 1: Add the `--insecure-allow-public` CLI flag**

In `sdwan_mcp/server.py`, inside `parse_args()`, just before the `return parser.parse_args(argv)` line, add:

```python
    parser.add_argument(
        "--insecure-allow-public",
        action="store_true",
        default=False,
        help=(
            "Allow binding to a non-loopback host with transport.auth.type=none. "
            "Without this flag, such a bind is auto-demoted to 127.0.0.1."
        ),
    )
```

- [ ] **Step 2: Update imports and `_connect_and_register` to apply bind decision and middleware**

At the top of `sdwan_mcp/server.py`, replace the existing imports block (line ~14-30) to add the new imports. Add these alongside the existing ones (don't replace the whole block — just add the new lines):

```python
from starlette.middleware import Middleware

from .transport_auth import BearerAuthMiddleware, decide_bind
```

Then locate the existing block in `_connect_and_register()` that computes `host`, `port`, and `transport_mode` (around line 130-135). After `read_write = args.read_write`, insert the bind decision (and capture `middleware_list` for later):

```python
    insecure_ok: bool = getattr(args, "insecure_allow_public", False)

    middleware_list: list[Middleware] = []
    if transport_mode != "stdio":
        effective_host, bind_warnings = decide_bind(
            host=host,
            auth_type=config.transport.auth.type,
            insecure_ok=insecure_ok,
        )
        for line in bind_warnings:
            print(f"[server] WARNING: {line}", file=sys.stderr)
        host = effective_host

        if config.transport.auth.type == "bearer":
            middleware_list.append(
                Middleware(
                    BearerAuthMiddleware,
                    expected_token=config.transport.auth.token,
                )
            )
```

Then update the print line that displays auth mode. Find:

```python
    print(f"[server] Auth         : {'JWT' if config.vmanage.use_jwt else 'Session'}")
```

Replace with:

```python
    print(f"[server] vManage Auth : {'JWT' if config.vmanage.use_jwt else 'Session'}")
    if transport_mode != "stdio":
        print(f"[server] HTTP Auth    : {config.transport.auth.type}")
```

Finally, update `_connect_and_register`'s return signature and `build_and_run` to pass the middleware list. Change the return-type line:

```python
) -> tuple[FastMCP, Dispatcher, TransportMode, str, int]:
```

to:

```python
) -> tuple[FastMCP, Dispatcher, TransportMode, str, int, list[Middleware]]:
```

Change the final `return` line of `_connect_and_register` from:

```python
    return mcp, dispatcher, transport_mode, host, port
```

to:

```python
    return mcp, dispatcher, transport_mode, host, port, middleware_list
```

In `build_and_run`, change:

```python
def build_and_run(args: argparse.Namespace) -> None:
    """FastMCP.run() owns its own event loop, so async pre-flight runs first."""
    mcp, dispatcher, transport, host, port = asyncio.run(_connect_and_register(args))

    try:
        if transport == "stdio":
            mcp.run()
        else:
            mcp.run(transport=transport, host=host, port=port)
```

to:

```python
def build_and_run(args: argparse.Namespace) -> None:
    """FastMCP.run() owns its own event loop, so async pre-flight runs first."""
    mcp, dispatcher, transport, host, port, middleware = asyncio.run(
        _connect_and_register(args)
    )

    try:
        if transport == "stdio":
            mcp.run()
        else:
            mcp.run(
                transport=transport,
                host=host,
                port=port,
                middleware=middleware or None,
            )
```

- [ ] **Step 3: Verify help text and existing tests still pass**

Run: `uv run sdwan-mcp --help 2>&1 | grep -A1 insecure`
Expected: shows `--insecure-allow-public` and its help text.

Run: `uv run pytest -v`
Expected: all existing tests PASS.

- [ ] **Step 4: Commit**

```bash
git add sdwan_mcp/server.py
git commit -m "feat(server): wire BearerAuthMiddleware + bind demotion into startup"
```

---

## Task 5: Server-level smoke tests

**Files:**
- Modify: `tests/test_transport_auth.py`

These tests verify the wiring done in Task 4 without booting uvicorn.

- [ ] **Step 1: Write the failing smoke tests**

Append to `tests/test_transport_auth.py`:

```python
import argparse
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from sdwan_mcp import server


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

        _, _, transport_mode, host, port, middleware = await server._connect_and_register(
            _make_args(config_path)
        )

    assert transport_mode == "stdio"
    assert host == "127.0.0.1"
    assert middleware == []


@pytest.mark.asyncio
async def test_server_http_with_bearer_installs_middleware(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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

        _, _, _, host, _, middleware = await server._connect_and_register(
            _make_args(config_path)
        )

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
```

- [ ] **Step 2: Run smoke tests to verify they pass**

Run: `uv run pytest tests/test_transport_auth.py -v`
Expected: all tests PASS (11 from earlier + 4 smoke = 15).

- [ ] **Step 3: Run the full test suite to confirm nothing regressed**

Run: `uv run pytest -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_transport_auth.py
git commit -m "test(server): smoke tests for transport auth wiring (stdio + bearer + demotion)"
```

---

## Task 6: Update example configs and `.env.example`

**Files:**
- Modify: `config.yaml`
- Modify: `.env.example`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add the `transport.auth` block to `config.yaml`**

Locate the existing `transport:` block in `config.yaml`. Replace the entire block with:

```yaml
transport:
  mode: stdio                       # stdio | sse | streamable-http
  host: 127.0.0.1
  port: 8000
  # HTTP transports (sse, streamable-http) only:
  #   type: none    → no auth. Auto-demoted to 127.0.0.1 if host is non-loopback,
  #                   unless you also pass --insecure-allow-public.
  #   type: bearer  → require `Authorization: Bearer <token>` on every request.
  #                   token must be non-empty (use ${ENV_VAR} interpolation).
  auth:
    type: none
    # token: "${SDWAN_MCP_TOKEN}"
```

- [ ] **Step 2: Add the new env var to `.env.example`**

Append to `.env.example` (preserving any existing content):

```
# Shared bearer token for HTTP-transport auth (sse, streamable-http).
# Only required when transport.auth.type=bearer in config.yaml.
SDWAN_MCP_TOKEN=
```

- [ ] **Step 3: Update `docker-compose.yml` to demonstrate token auth**

Update the `environment:` section to include `SDWAN_MCP_TOKEN`:

```yaml
    environment:
      - VMANAGE_USERNAME=${VMANAGE_USERNAME}
      - VMANAGE_PASSWORD=${VMANAGE_PASSWORD}
      - SDWAN_MCP_TOKEN=${SDWAN_MCP_TOKEN}
```

If `docker-compose.yml` mounts a `config.yaml`, it picks up the `transport.auth` block automatically. If it instead passes CLI flags, add a `command:` override that ensures `--transport streamable-http --host 0.0.0.0` is paired with a config that sets `transport.auth.type: bearer` (i.e. the updated `config.yaml` from Step 1). Verify by reading the existing `docker-compose.yml` first.

- [ ] **Step 4: Commit**

```bash
git add config.yaml .env.example docker-compose.yml
git commit -m "docs(examples): show transport.auth.bearer in config.yaml + docker-compose"
```

---

## Task 7: CHANGELOG and docs

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/reference/configuration.md`
- Modify: `docs/reference/cli.md`
- Modify: `docs/guides/mcp-clients.md`

- [ ] **Step 1: Add a CHANGELOG entry calling out the behavior change**

Add under the `[Unreleased]` (or v0.1.1) heading in `CHANGELOG.md`. If no Unreleased section exists, create one at the top:

```markdown
## [Unreleased]

### Added
- HTTP transport auth: `transport.auth.{type,token}` config block. `type: bearer`
  requires `Authorization: Bearer <token>` on every request (#7).
- New CLI flag `--insecure-allow-public` to acknowledge binding a non-loopback
  host without auth.

### Changed (behavior — read this if you upgrade)
- `--host 0.0.0.0` (or any non-loopback bind) with `transport.auth.type=none`
  is now auto-demoted to `127.0.0.1` with a loud stderr warning. To restore
  the previous "open on the LAN" behavior, either:
    - set `transport.auth.type: bearer` and provide a token (recommended), OR
    - set `transport.auth.type: none` explicitly AND pass
      `--insecure-allow-public` to acknowledge the risk.

### Security
- The HTTP transports (SSE, streamable-http) now have first-class authn (#7).
```

- [ ] **Step 2: Add a `transport.auth` section to `docs/reference/configuration.md`**

Append a new section to `docs/reference/configuration.md`:

```markdown
## `transport.auth` — HTTP transport authentication

Applies to the `sse` and `streamable-http` transports only. The `stdio`
transport ignores this block.

| Key     | Type   | Default | Description                                                  |
|---------|--------|---------|--------------------------------------------------------------|
| `type`  | string | `none`  | `none` (no auth) or `bearer` (shared bearer token).          |
| `token` | string | `""`    | Required when `type: bearer`. Use `${ENV_VAR}` interpolation.|

Validation (raised at config load):

- `type: bearer` with an empty `token` → error.
- `type: none` with a non-empty `token` → error (catches the common "I pasted
  a token but forgot to flip the type" mistake).
- Any other `type` value → error.

### Bind-safety: auto-demotion to loopback

If `transport.host` is non-loopback (e.g. `0.0.0.0`) **and**
`transport.auth.type` is `none`, the server prints a stderr WARNING and
demotes the bind to `127.0.0.1`. To bind outward without auth (only safe
behind a trusted authenticating reverse proxy), pass `--insecure-allow-public`
on the command line.

### Example: bearer token via env var

```yaml
transport:
  mode: streamable-http
  host: 0.0.0.0
  port: 8000
  auth:
    type: bearer
    token: "${SDWAN_MCP_TOKEN}"
```

Then in `.env`:

```
SDWAN_MCP_TOKEN=replace-me-with-a-long-random-string
```

Clients must send `Authorization: Bearer replace-me-with-a-long-random-string`
on every request.
```

- [ ] **Step 3: Document `--insecure-allow-public` in `docs/reference/cli.md`**

Add to the CLI flag table (or as a new entry — match the existing style):

```markdown
### `--insecure-allow-public`

Allow binding to a non-loopback host with `transport.auth.type=none`. Without
this flag, such a bind is auto-demoted to `127.0.0.1` with a stderr WARNING.
Only use this when the server sits behind a trusted authenticating reverse
proxy (mTLS, OIDC, a corporate auth gateway). The flag is intentionally
verbose to discourage casual use.
```

- [ ] **Step 4: Update `docs/guides/mcp-clients.md` with the Authorization header**

Add a new subsection near the top of `docs/guides/mcp-clients.md` (before the
client-by-client examples):

```markdown
## Setting the bearer token

When `transport.auth.type: bearer` is configured (see
[configuration reference](../reference/configuration.md)), every HTTP
request must include:

```
Authorization: Bearer <your-token>
```

How you set this depends on the client:

- **Claude Desktop** — add a `headers` block under the SSE/streamable-http
  server entry in `claude_desktop_config.json`:

  ```json
  {
    "mcpServers": {
      "sdwan": {
        "url": "http://your-host:8000/mcp",
        "headers": {
          "Authorization": "Bearer ${SDWAN_MCP_TOKEN}"
        }
      }
    }
  }
  ```

- **fastmcp Python client** — pass `headers=` when constructing the client:

  ```python
  from fastmcp import Client

  async with Client(
      "http://your-host:8000/mcp",
      headers={"Authorization": f"Bearer {os.environ['SDWAN_MCP_TOKEN']}"},
  ) as client:
      ...
  ```

- **Cline / Continue / other MCP clients** — check the client's docs for
  custom HTTP headers. The header name is `Authorization`, the value is
  `Bearer <token>`.
```

- [ ] **Step 5: Build docs locally to verify nothing broke**

Run: `uv run --group docs mkdocs build --strict`
Expected: build succeeds with no warnings/errors.

- [ ] **Step 6: Commit**

```bash
git add CHANGELOG.md docs/reference/configuration.md docs/reference/cli.md docs/guides/mcp-clients.md
git commit -m "docs: document transport.auth, --insecure-allow-public, and client header setup"
```

---

## Task 8: Final verification

- [ ] **Step 1: Run full test suite, lint, and type check**

```bash
uv run pytest -v
uv run ruff check .
uv run ruff format --check .
uv run mypy sdwan_mcp/
```

Expected: all PASS / clean.

- [ ] **Step 2: Manual smoke — stdio still works**

```bash
uv run sdwan-mcp --help
```

Expected: help text shows `--insecure-allow-public`; no errors.

- [ ] **Step 3: Manual smoke — auto-demotion warning fires**

Set up a minimal config with `transport.mode: streamable-http`, `host: 0.0.0.0`, `auth.type: none`, then start the server briefly (Ctrl-C after seeing the banner):

```bash
uv run sdwan-mcp --transport streamable-http --host 0.0.0.0 2>&1 | head -20
```

Expected: stderr lines containing `Demoting bind to 127.0.0.1` and `--insecure-allow-public`.

- [ ] **Step 4: Push branch and open PR**

```bash
git push -u origin feat/7-http-auth-middleware
gh pr create --title "HTTP transport auth middleware (#7)" --body "$(cat <<'EOF'
## Summary
- Adds `transport.auth.{type,token}` config block (none|bearer); bearer mode enforces `Authorization: Bearer <token>` via constant-time compare.
- Adds `--insecure-allow-public` CLI flag. Non-loopback bind with `auth.type=none` is auto-demoted to `127.0.0.1` unless this flag is set.
- Spec: `docs/superpowers/specs/2026-05-24-http-auth-middleware-design.md`. Closes #7.

## Test plan
- [ ] `uv run pytest -v` (including new `tests/test_transport_auth.py`)
- [ ] `uv run sdwan-mcp --transport streamable-http --host 0.0.0.0` shows demotion warning
- [ ] `uv run --group docs mkdocs build --strict` succeeds

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review

**Spec coverage (each spec section → task):**

- Config schema (bearer/none, env interpolation, all 3 validation errors) → Task 1.
- Wire-level contract (Bearer-only, 401 shape, `WWW-Authenticate: Bearer`, constant-time) → Task 3.
- Startup checks / demotion table → Task 2 (`decide_bind`) + Task 4 (wiring) + Task 5 (smoke).
- `--insecure-allow-public` flag → Task 4.
- Logging policy (never log token; remote+path on failure) → Task 3 implementation + no test checks the *absence* of the token in logs (trust the code: only `client.host` and `request.url.path` are passed to `logger.warning`).
- Code layout (`transport_auth.py`, edits to `config.py`/`server.py`, test files) → Tasks 1–5.
- Docs (configuration.md, cli.md, mcp-clients.md) → Task 7.
- Open implementation question re: FastMCP middleware API → resolved during plan-writing: FastMCP 3.3.1 `mcp.run(middleware=[Middleware(...)])` plumbs through to `http_app()`. Implemented in Task 4.
- Risk: behavior change for `0.0.0.0` users → CHANGELOG entry in Task 7.

**Placeholder scan:** None — every step has the exact code, command, or expected output.

**Type consistency:**
- `decide_bind(host, auth_type, insecure_ok) -> tuple[str, list[str]]` — same signature in tests (Task 2) and in `server.py` call site (Task 4). ✓
- `BearerAuthMiddleware(app, expected_token)` — same kwarg name in tests (Task 3) and `Middleware(..., expected_token=...)` in Task 4. ✓
- `_connect_and_register` return tuple changed from 5-tuple to 6-tuple — both the return statement and the `build_and_run` unpack updated in the same task (Task 4). ✓
- Config: `TransportAuthConfig.type` / `.token` accessed identically in tests (Task 1) and server (Task 4). ✓

No gaps found.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-24-http-auth-middleware.md`.
