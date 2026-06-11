# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] - 2026-06-11

Closes the [v0.4.0 milestone](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/milestone/6) (#55, #56, #57). Bumped to a minor (not the v0.3.2 patch tag) because TLS verification is now **on by default** — a behavior change for anyone relying on the old insecure default.

### Security
- **TLS certificate verification is now enabled by default** (`verify_ssl: true`).
  Previously it defaulted to `false`, and vManage credentials are POSTed to
  `/j_security_check` over that channel — an unverified connection let an
  on-path attacker capture the operator's username/password and the issued
  JWT/JSESSIONID. The self-signed Cisco DevNet sandbox is the documented opt-out:
  set `VMANAGE_VERIFY_SSL=false` (or `verify_ssl: false`); the shipped
  `sdwan-mcp.yaml` does this. The server now prints a loud stderr `WARNING`
  whenever verification is disabled. (#55, H1)
- All GitHub Actions are pinned to full commit SHAs (was mutable major-version
  tags, and a mutable `release/v1` **branch** on the OIDC-privileged PyPI publish
  step). (#55, M1)
- MCP-client-supplied path parameters are percent-encoded (`quote(safe='')`)
  before substitution into the vManage request URL, so a value containing `/`,
  `..`, `?`, or `#` can no longer reshape the request path to a sibling
  endpoint. (#55, L3)
- Dockerfile hardening: the `uv` builder image is pinned to an immutable
  version + digest (was `:latest`); the install is fail-closed (dropped the
  `|| uv sync --no-dev` fallback and `2>/dev/null`, and `COPY uv.lock`
  explicitly); the runtime container runs as a non-root user. (#55, L1/L2/L4/L5)
- `.env.example` ships credential placeholders instead of the literal public
  sandbox password, so secret scanners don't flag it. (#55, I2)

### Changed (behavior — read this if you upgrade)
- `verify_ssl` now defaults to **`true`** (see Security above). If you point at
  a vManage with a self-signed or otherwise unverifiable certificate, you must
  now explicitly set `VMANAGE_VERIFY_SSL=false` / `verify_ssl: false`, otherwise
  login will fail with a TLS error instead of silently connecting insecurely.
- The `fastmcp` dependency range is tightened to `>=3,<4` (was `>=2.0`). The
  server targets and is tested against fastmcp 3.x; this prevents a reinstall
  from silently landing on now-untested 2.x or an unreviewed future 4.x. (#57)

### Fixed
- The first tool call of a fresh session no longer intermittently fails with
  `Event loop is closed`. Async pre-flight (which creates the httpx client and
  logs in) ran in its own event loop, then the server opened a second loop to
  serve — leaving the client bound to the already-closed pre-flight loop. Both
  phases now run on a single loop via `mcp.run_async()`. (#56)
- Statistics-database queries that return an opaque HTTP 500 (`REST0001`) are
  now annotated with an actionable `hint` (the stats DB may be disabled/empty,
  or the query needs a bounded time window) instead of a bare error. The hint
  wording is tiered so a non-`REST0001` 500 on a `/statistics` path is hedged
  rather than over-claiming a DB outage. Real-time endpoints are unaffected. (#56)

## [0.3.1] - 2026-06-11

Closes the [v0.3.1 milestone](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/milestone/5). Patch release — the only change is a startup-crash fix, no behavior change.

### Fixed
- Tool registration no longer crashes at startup with
  `PydanticSchemaGenerationError: Unable to generate pydantic-core schema for
  <class 'sdwan_mcp.dispatcher.Dispatcher'>`. The handler factory used a
  default-arg value-capture pattern that leaked internal closures
  (`_dispatcher: Dispatcher`, `_valid`, `_name`) into the handler signature;
  **fastmcp 3.x** introspects the full signature to build each tool's input
  schema and could not generate a schema for the arbitrary `Dispatcher` type.
  Handlers are now built by a closure factory so the only parameters fastmcp
  sees are `action` and `params`. This surfaced after `uv tool install`
  resolved the unpinned `fastmcp>=2.0` dependency to 3.x. (#52)

## [0.3.0] - 2026-06-11

Closes the [v0.3.0 milestone](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/milestone/4). Bumped to a minor (not 0.2.3) because the config loader is now env-first and the YAML file is optional — a behavior change. Together with #44 (released in 0.2.2) this makes the server usable out of the box under `uv tool install` + an MCP client.

### Changed (behavior — read this if you upgrade)
- The YAML config file is now **optional**. Configuration is built from
  (highest priority first) CLI flags → environment variables → the YAML file →
  defaults, using `pydantic-settings`. Credentials and connection settings can
  be supplied entirely via env vars — `VMANAGE_USERNAME`, `VMANAGE_PASSWORD`,
  and optionally `VMANAGE_HOST`, `VMANAGE_PORT`, `VMANAGE_VERIFY_SSL`,
  `VMANAGE_USE_JWT`, `VMANAGE_TIMEOUT` — with no `sdwan-mcp.yaml` on disk. This
  fixes startup under `uv tool install` + an MCP client, where the working
  directory is not your project dir so no YAML is found. Env vars override the
  YAML; a `.env` is still honored. Passing `--config PATH` to a file that does
  not exist remains an error. Built-in defaults now target the Cisco DevNet
  sandbox (host `sandbox-sdwan-2.cisco.com`, port 443). (#49)

### Fixed
- vManage credentials are now validated immediately after config load, before
  the spec is loaded or auto-fetched. Previously the check lived in
  `auth.login()` at the end of startup, so a missing `VMANAGE_USERNAME` /
  `VMANAGE_PASSWORD` only surfaced after the loader had already done (and
  possibly network-fetched) all its work. (#47)

## [0.2.2] - 2026-06-11

Patch release closing the [v0.2.2 milestone](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/milestone/3) — two bug fixes, no behaviour changes. The remaining live-test work (#10) rolls forward to v0.2.3.

### Fixed
- `.env` is now discovered from the current working directory (and next to
  `--config`) instead of from the package's install location. python-dotenv's
  bare `load_dotenv()` searches upward from the calling module's directory,
  which is site-packages once installed via `uv tool install`/pipx — so a
  `.env` in the user's project dir was silently never loaded. Exported shell
  variables still take precedence. The "credentials not set" error now also
  explains that MCP clients don't inherit shell exports. (#44)
- `milestone-rollover.yml` now triggers on `push: tags` instead of
  `release: published`. The previous trigger was unreachable because
  `release.yml`'s `gh release create` step runs under the default
  `GITHUB_TOKEN`, and GitHub deliberately blocks workflow-token-created
  events from chaining further workflows. (#37)

## [0.2.1] - 2026-05-26

This release closes the [v0.2.1 milestone](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/milestone/2) — a config-file rename plus a docs sync. Shipping as a patch despite the `Changed (behavior)` entry because the rename is a trivial one-line user migration.

### Changed (behavior — read this if you upgrade)
- The default config filename is now `sdwan-mcp.yaml` (was `config.yaml`).
  `config.yaml` is too generic and collided with other tools in the same
  repo. Rename your config file or pass `--config config.yaml` explicitly.
  The Docker image and `docker-compose.yml` mounts use the new name too.
  (#35)

### Documentation
- Sync README and mkdocs site to the v0.2.0 feature surface (auto-fetch,
  HTTP-transport bearer auth, bind-safety demotion, pagination, retry/timeout,
  new modules). Clarify release-process: milestone auto-rollover lives in a
  companion workflow, not `release.yml`. (#33)

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
