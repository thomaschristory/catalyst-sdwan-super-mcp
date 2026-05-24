# HTTP transport auth middleware — design

**Status:** Approved, ready for implementation plan
**Date:** 2026-05-24
**Issue:** [#7](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/issues/7)
**Milestone:** v0.1.1

## Problem

`sdwan-mcp --transport sse --host 0.0.0.0 --port 8000` (and the
`streamable-http` equivalent) exposes the MCP endpoint with no authentication
of its own. Anyone reachable on the port can invoke tools, and therefore the
underlying Cisco vManage. Fine on `127.0.0.1` or behind a reverse proxy that
does authn; not fine on a public interface.

## Goal

Add a shared-token bearer auth layer to the HTTP transports (SSE,
streamable-http). Make it impossible to bind a non-loopback interface with no
auth unless the operator explicitly acknowledges the risk.

Structure the config so stronger modes (OIDC, mTLS) can land later without a
breaking change, but do **not** build any provider abstraction in this round
— the seam is the config discriminator, not a Python interface.

## Non-goals (v0.1.1)

- OIDC, mTLS, or JWT-signature validation.
- Per-tool RBAC.
- Rate limiting on failed auth (belongs in a reverse proxy).
- Audit log of caller → tool.
- Token rotation primitives (multiple accepted tokens).
- Auth for stdio (no HTTP layer).

## Config schema

New `transport.auth` block:

```yaml
transport:
  mode: streamable-http
  host: 127.0.0.1
  port: 8000
  auth:
    type: bearer              # bearer | none
    token: "${SDWAN_MCP_TOKEN}"
```

Rules:

- `type: bearer` with empty or missing `token` → **config-load error**
  (fail fast at startup, not at first request).
- `type: none` with a non-empty `token` set → **config-load error**
  ("token configured but auth.type is none; set type: bearer to enable it").
  Catches the footgun where an operator pastes a token but forgets the type.
- `type: none` is explicit "I know this is open" — required to bind
  outward without auth (see startup checks).
- Default when block omitted: `type: none` (preserves existing stdio /
  loopback users; demotion logic prevents accidental public exposure).
- `${VAR}` interpolation reuses existing `_interpolate_dict()` — no new code.

No `--auth-token` CLI flag: tokens belong in env/config, not shell history.

## Wire-level contract

- Client sends `Authorization: Bearer <token>` on every HTTP request.
- Server compares with `hmac.compare_digest` (constant-time).
- Failure responses:
  - `401` + `WWW-Authenticate: Bearer` + JSON body `{"error": "missing or malformed Authorization header"}`
  - `401` + same shape, `"invalid token"` on mismatch.
- No alternative header (`X-API-Key` etc.) accepted — Bearer only.
- Single accepted token (no list) — keeps room to widen to a richer per-token
  shape later if/when needed.

## Startup checks (bind-time decision)

Run in `_connect_and_register()` after config load, before the server binds.
Only applies when `transport != stdio`.

| host          | auth.type            | Action                                                |
| ------------- | -------------------- | ----------------------------------------------------- |
| 127.0.0.1 / ::1 | any                | bind as requested                                     |
| non-loopback  | bearer + valid token | bind as requested                                     |
| non-loopback  | none                 | **demote bind to 127.0.0.1, loud WARNING (stderr)**   |
| non-loopback  | bearer + empty token | config-load error (never reaches table)               |

Demotion is overridden only by `--insecure-allow-public` (CLI flag,
intentionally ugly). With the flag and `type: none`, the server binds outward
as requested — for users genuinely behind a trusted authenticating proxy.

Demotion warning template (printed to **stderr**, multi-line, with the exact
remediation):

```
[server] WARNING: refusing to bind 0.0.0.0 with transport.auth.type=none.
[server] WARNING: Demoting bind to 127.0.0.1. To expose externally, set transport.auth.type=bearer
[server] WARNING: and transport.auth.token, OR set transport.auth.type=none explicitly AND pass
[server] WARNING: --insecure-allow-public to acknowledge the risk.
```

## Logging policy

- Failed auth log line: remote address + request path. **Never** the supplied
  token (not even a prefix — leaks rotation state).
- No per-request success logging beyond what FastMCP/uvicorn already emit.
- Successful token comparison is not logged.

## Code layout

### New files

- `sdwan_mcp/transport_auth.py` (~80 LOC)
  - `class BearerAuthMiddleware(BaseHTTPMiddleware)` — Starlette middleware
    that enforces the wire-level contract above.
  - `def decide_bind(host: str, auth_type: str, insecure_ok: bool) -> tuple[str, list[str]]`
    — pure function returning `(effective_host, warning_lines)`. Easy to unit
    test in isolation from FastMCP/uvicorn.

- `tests/test_transport_auth.py`
  - `decide_bind` matrix: all five rows of the table above.
  - Middleware:
    - 200 with valid token.
    - 401 with no `Authorization` header.
    - 401 with wrong scheme (e.g. `Basic ...`).
    - 401 with malformed Bearer (no token after `Bearer`).
    - 401 with wrong token value.
    - Assert `hmac.compare_digest` is called (mock/spy).
  - Config: `transport.auth.type: bearer` with empty `token` raises at
    `load_config()`.
  - Config: `transport.auth.type: none` with non-empty `token` raises at
    `load_config()`.
  - Server smoke: stdio transport does **not** wrap the FastMCP app with the
    middleware (mocked).

### Modified files

- `sdwan_mcp/config.py`
  - Add `TransportAuthConfig(type: str = "none", token: str = "")`.
  - Extend `TransportConfig` with `auth: TransportAuthConfig`.
  - Parse `transport.auth` block; validate `bearer` ⇒ non-empty `token`.

- `sdwan_mcp/server.py`
  - Add `--insecure-allow-public` argparse flag.
  - After config load: call `decide_bind()`, print warnings to stderr, use
    the returned `effective_host`.
  - When `transport != stdio` and `auth.type == bearer`: wrap the FastMCP
    ASGI app with `BearerAuthMiddleware` before handing to uvicorn.

- `config.yaml`
  - Add commented `transport.auth` block showing both modes.

- `.env.example`
  - Add `SDWAN_MCP_TOKEN=` line.

- `docker-compose.yml`
  - Update example to demonstrate token-based auth (not `type: none`) — more
    realistic for a network-exposed deployment.

### Docs

- `docs/reference/configuration.md` — new `transport.auth` section.
- `docs/guides/mcp-clients.md` — show how to plumb the `Authorization: Bearer`
  header in Claude Desktop, Cline, and the FastMCP HTTP client examples.
- `docs/reference/cli.md` — document `--insecure-allow-public`.

## Open implementation question

FastMCP currently exposes the ASGI app for SSE and streamable-http transports
(`mcp.sse_app()`, `mcp.streamable_http_app()` or equivalent). The
implementation will wrap that app with `BearerAuthMiddleware` before passing
to uvicorn. If the current FastMCP version doesn't cleanly expose the app for
both transports, fall back to FastMCP's own middleware hook. Confirm the
exact API at implementation time; design does not depend on which we pick.

## Risks & mitigations

- **FastMCP middleware API stability.** Keep `BearerAuthMiddleware` small and
  standalone so future migration to a first-class FastMCP auth feature is
  cheap.
- **Token in config file on disk.** Documented path is env-var interpolation;
  inline literals are technically allowed but not shown in any example.
  Cannot prevent operator misuse.
- **Behavior change for existing users on `0.0.0.0`.** Upgrade silently
  demotes their bind to loopback unless they take action. This is intentional
  (the issue is labeled `security`) but **must be called out in CHANGELOG**
  as a breaking runtime behavior change with the exact remediation snippet.

## Acceptance criteria

From the issue, mapped to this design:

- New config key `transport.auth_token` (env-var interpolated) — delivered
  as `transport.auth.token` under a discriminated block.
- Server refuses with `401` if missing/wrong.
- Docs updated (`docs/guides/mcp-clients.md`) showing how to plumb the token
  through the client.
- Startup warning when running with `--host 0.0.0.0` and no auth configured
  — strengthened to "demote to loopback unless `--insecure-allow-public`."
