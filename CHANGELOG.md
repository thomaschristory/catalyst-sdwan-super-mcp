# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-05-25

This release completes the [v0.1.1 milestone](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/milestone/1) — four enhancements ship together. Bumped to **0.2.0** rather than 0.1.1 because the auto-fetch and bind-safety changes are minor-version behaviour changes, and the HTTP transport now ships with first-class auth.

### Added
- Live ingestion of split-spec vManage versions (>=20.16). If
  `specs/<active_version>/` is missing on startup, the loader fetches
  ~14k OpenAPI fragments from `developer.cisco.com`, stitches them into a
  single YAML, validates it, and writes
  `specs/<version>/vmanageapi_<flat>.yaml` before registering tools.
  Disable with `sdwan.auto_fetch: false`. Explicit `sdwan-mcp fetch
  --version <V>` and `sdwan-mcp list-versions` subcommands are also
  available; the explicit path caches fragments under
  `~/.cache/sdwan-mcp/fragments/`. (#31)
- HTTP transport auth: `transport.auth.{type,token}` config block. `type: bearer`
  requires `Authorization: Bearer <token>` on every request, compared in
  constant time, with an RFC 6750 `WWW-Authenticate` challenge on 401.
  Rejection logs are rate-limited (10 lines / 60s window) to resist log-flood
  attacks. Tokens shorter than 8 chars are rejected at startup, under 16 chars
  warn. (#7)
- New CLI flag `--insecure-allow-public` to acknowledge binding a non-loopback
  host without auth.
- Configurable per-request timeout (`vmanage.timeout`, default 30s) and
  transient-failure retry policy (`vmanage.retries`) on the httpx client.
  Retries 502 / 503 / 504 and `httpx.RequestError` (timeouts, connection
  resets) with exponential backoff + equal jitter, capped. Mutating verbs
  (POST/PUT/DELETE/PATCH) are not retried by default. (#9)
- Response pagination for bulk endpoints. Auto-follows scroll and offset
  endpoint families up to `sdwan.pagination.max_pages` (default 5), then
  surfaces a resumable cursor under `pagination.next_cursor`. Per-call
  overrides via `_max_pages`, `_page_size`, `_pagination` params. (#8)

### Changed (behavior — read this if you upgrade)
- `--host 0.0.0.0` (or any non-loopback bind) with `transport.auth.type=none`
  is now auto-demoted to `127.0.0.1` with a loud stderr warning. To restore
  the previous "open on the LAN" behavior, either:
    - set `transport.auth.type: bearer` and provide a token (recommended), OR
    - set `transport.auth.type: none` explicitly AND pass
      `--insecure-allow-public` to acknowledge the risk.

### Security
- The HTTP transports (SSE, streamable-http) now have first-class authn (#7).

## [0.1.0] - 2026-05-23

### Added
- Publish to PyPI from the release workflow via trusted publishing (OIDC, no API token). Installable with `uv tool install catalyst-sdwan-super-mcp`. ([#12](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/issues/12))
- Adaptive tool splitting (`max_actions_per_tool`, default 150) with section → sub-tag → URL-path recursion. ([#13](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/issues/13))
- Stable derived action names (independent of Cisco's churning `operationId`s).

## [0.0.1] - 2026-05-22

Initial alpha release.

### Added
- FastMCP server that exposes the Cisco Catalyst SD-WAN Manager (vManage) API as MCP tools.
- Dynamic OpenAPI spec loader — drop a `specs/{version}/*.yaml` folder, the tools rebuild themselves.
- One MCP tool per OpenAPI tag group with `(action, params)` shape (keeps the tool count LLM-friendly).
- Two auth modes: JWT (default, vManage 20.18.1+) and JSESSIONID + XSRF (legacy).
- Proactive JWT refresh and reactive re-login on session expiry.
- Read-only by default; `--read-write` enables POST/PUT/DELETE/PATCH.
- `--diff` utility to compare operationIds between two spec versions before upgrading.
- Three transports: stdio (Claude Desktop), SSE, streamable-HTTP.
- Multi-stage Dockerfile + docker-compose.
- mkdocs-material documentation site, deployed to GitHub Pages on tag.
- GitHub Actions: lint (ruff + mypy), test (pytest matrix on 3.11/3.12/3.13), docs deploy, docker build, release.

### Known limitations
- Specs must be downloaded manually from Cisco DevNet — see [#1](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/issues/1).
- No pagination handling for large list endpoints.
- HTTP transports have no auth middleware — do not expose to the public internet.
- No per-tool request timeout / retry config yet.
