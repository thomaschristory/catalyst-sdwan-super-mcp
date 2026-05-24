# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Configurable per-request timeout (`vmanage.timeout`, default 30s) and
  transient-failure retry policy (`vmanage.retries`) on the httpx client.
  Retries 502 / 503 / 504 and `httpx.RequestError` (timeouts, connection
  resets) with exponential backoff + equal jitter, capped. Mutating verbs
  (POST/PUT/DELETE/PATCH) are not retried by default. (#9)
- Response pagination for bulk endpoints. Auto-follows scroll and offset
  endpoint families up to `sdwan.pagination.max_pages` (default 5), then
  surfaces a resumable cursor under `pagination.next_cursor`. Per-call
  overrides via `_max_pages`, `_page_size`, `_pagination` params. (#8)

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
