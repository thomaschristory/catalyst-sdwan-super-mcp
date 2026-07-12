# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Docs now build with [Zensical](https://zensical.org/) instead of MkDocs +
  Material for MkDocs (#97).** Zensical is the successor to both, from the same
  team. It reads the existing `mkdocs.yml` natively, so the nav, palette,
  markdown extensions, and every published URL/anchor carry over untouched — the
  migration is essentially a dependency and command swap (`mkdocs build --strict`
  → `zensical build --strict`, `mkdocs serve` → `zensical serve`). Full site
  build is now ~0.3s. Contributor-facing only; the shipped package is untouched.
- **Docs adopt Zensical's `modern` theme with the warm "paper" palette (#97),**
  matching the sister project `panorama-super-cli`. The modern theme paints the
  top bar from `--md-default-bg-color--light`, so a new `docs/stylesheets/extra.css`
  sets that variable (plus body/fg/code surfaces) to soften the default
  black-on-white and white-on-black extremes. The indigo primary/accent from
  `mkdocs.yml` is left intact.

### Removed

- **`docs/superpowers/` internal planning artifacts (#97).** Four agent-generated
  implementation plans and design specs from May 2026 lived under `docs/`. They
  were absent from the `mkdocs.yml` nav, but the site generator builds every
  `.md` under `docs_dir`, so they were being published to GitHub Pages as
  unlinked orphan pages. Deleted and added to `.gitignore`; they remain in git
  history. This also removed the last stale `mkdocs build --strict` references
  in the repo.

### Fixed

- **Broken anchor in the CLI reference (#97).** The `--debug` row linked to
  `configuration.md#debug--capture-the-upstream-vmanage-exchange-72` (doubled
  hyphen), which matched no heading. MkDocs' `--strict` does not validate anchor
  fragments, so this shipped to the live site; Zensical's stricter validator
  flagged it on the first build.

## [0.6.4] - 2026-07-03

Closes the [v0.6.4 milestone](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/milestone/16) (#93).

### Fixed

- **Auto-recover from legacy session timeout without a restart (#93).** In
  session mode (`use_jwt: false`, the DevNet-sandbox/20.15 default), an expired
  `JSESSIONID` does not produce a clean `302`/`401` for API calls — vManage
  answers with an HTTP 200 whose body is the login page. `is_session_expired()`
  only recognised the `302→welcome.html` and `401` signals, so that login HTML
  slipped through as "data", the existing re-auth retry never fired, and the
  session stayed dead until the server was manually restarted (confusing LLM
  agents mid-session). Detection now also treats a 2xx carrying the vManage
  login markers (`welcome.html` / `j_security_check`) as expiry, so the existing
  retry-once re-login path recovers transparently. Fail-safe: JSON/text API
  successes are rejected by content-type before the body is inspected, and the
  login markers are anchored (the `j_security_check` form action, or a
  `url=`/`href=` redirect to `welcome.html`) so the HTML device-config endpoint
  (`GET /device/config/html`) can't be misread as a login page and discarded. If
  re-authentication succeeds but the retry is still a login page (e.g. a
  concurrent-session limit on shared credentials), a real error is returned
  rather than the internal sentinel. JWT mode is unaffected (it already gets a
  401 on expiry).

## [0.6.3] - 2026-06-18

Closes the [v0.6.3 milestone](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/milestone/15) (#86).

### Docs

- **Made the sandbox host explicit in every example (#86).** The DevNet-sandbox
  quick-start and MCP-client blocks set credentials but relied on
  `sandbox-sdwan-2.cisco.com` being the built-in config default rather than
  naming it, so a reader couldn't tell which vManage the example targeted. Added
  `VMANAGE_HOST=sandbox-sdwan-2.cisco.com` to the README headline command,
  `claude mcp add`, and every `.mcp.json` `env` block, plus the `docker run`
  examples and the Docker passthrough block in the docs. The
  credentials-only configuration-reference snippet stays host-less on purpose —
  it demonstrates that host/version have built-in defaults.

## [0.6.2] - 2026-06-18

Closes the [v0.6.2 milestone](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/milestone/14) (#83).

### Docs

- **Documented how to point the server at your own vManage (#83).** After the
  v0.6.1 onboarding rewrite, every quick-start and MCP-client block used the
  DevNet sandbox defaults, so a user targeting their own controller had no
  concrete example. Added a "Point at your own vManage" section to the README
  (env-var command + `.mcp.json` `env` block with `VMANAGE_HOST` / `VMANAGE_PORT`,
  SSL verification left on for valid certs) and an env-var form to the docs
  "Configure your vManage" section, cross-linking the configuration reference.

## [0.6.1] - 2026-06-18

Closes the [v0.6.1 milestone](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/milestone/13) (#80).

### Docs

- **Modernized the README and docs onboarding (#80).** The quick start now leads
  with `uvx catalyst-sdwan-super-mcp` (zero-install) and `pipx` / `uv tool install`
  for a persistent CLI, instead of `git clone` + `uv sync`; the source checkout is
  demoted to a "Develop / hack on it" note. Added an "Add it to your MCP client"
  section with copy-paste config for Claude Code (`claude mcp add` + `.mcp.json`),
  Claude Desktop, Cursor, and other stdio clients — all launching via the published
  CLI. Sandbox examples now pass `VMANAGE_VERIFY_SSL=false` (the sandbox uses a
  self-signed cert and a `uvx` run has no `sdwan-mcp.yaml` to set it). Rewrote the
  stale `Status` section to reflect actual maturity. The docs site
  (`install`, `first-run`, `sandbox`, `mcp-clients`) was updated to match.

## [0.6.0] - 2026-06-17

Closes the [v0.6.0 milestone](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/milestone/12) (#78).

### Changed (behavior)
- **POST/PUT/PATCH tools no longer mislead callers into wrapping the body (#78).**
  Every write action used to declare its request body as a single param literally
  named `body: object`, while the dispatcher forwarded `params` verbatim as the
  HTTP body — so the schema-faithful call `params={"body": {...}}` double-wrapped
  and vManage rejected it (`400 STATS_VALIDATION0001`, "Unrecognized field
  'body'"); only passing the fields at the top level of `params` worked. Schema
  and behavior now agree:
  - The tool description names the **real top-level body fields** (resolved from
    the spec's `requestBody` schema, following `$ref` and merging `allOf`), e.g.
    `body fields (top-level): query?: object, aggregation?: object, …`. ~37% of
    POST bodies gain concrete field lists.
  - For the **statistics-DB query family** — which Cisco's spec declares as a bare
    `{"type": "object"}` with no fields — the known query-DSL fields (`size,
    aggregation, plot_data, fields, category, query, sort`) are baked into the
    description so the canonical case is one-shot correct.
  - A single per-tool note states the convention once: request-body fields go at
    the top level of `params`, never nested under a `body` key. The note is
    omitted from tools with no body-bearing action (most tools in the default
    read-only mode), so it costs nothing where it doesn't apply.
  - The dispatcher **defensively unwraps** a lone `{"body": …}` wrapper (any value
    type) so a caller that followed the old schema still succeeds.
- **New error hint for `400 STATS_VALIDATION0001` (#78).** A statistics query that
  clears auth/RBAC but is rejected by the stats engine now gets an actionable hint
  naming the top-level convention and accepted fields, and distinguishing this
  post-auth query-validation failure from the pre-query `500 REST0001` RBAC error.

### Added
- `OperationSpec.body_fields` and `loader._parse_request_body()` — best-effort
  extraction of a request body's top-level fields (names, types, required flags)
  from the OpenAPI spec, degrading to an empty list (never raising) on bare-object
  or malformed body schemas.

## [0.5.1] - 2026-06-17

Closes the [v0.5.1 milestone](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/milestone/11) (#75).

### Documentation
- **Full review of all documentation and inline docstrings (#75).** Audited every
  prose doc and every module/function docstring against the actual code as of
  v0.5.0, adversarially re-verifying each claim against the source before editing
  and landing only high-confidence corrections. Prose fixes: `vmanage.port`
  default is `443` (was documented as `8443`); the RO tool counts in the
  tool-splitting table are `214` (20.16) and `230` (20.18); the `first-run`
  startup sample now matches current output; the docs deploy is path-filtered
  rather than running on every push to `main`; token-length validation is
  performed by the server, not the loader. Docstring fixes: `auth.py` (session
  mode sets only `X-XSRF-TOKEN`; `401` treated as expired in both modes),
  `config.py` (`init_settings` unused; `redact` masks body/query credentials too),
  `server.py` (`--debug-no-redact` scope), `dispatcher.py` (resolves derived
  action names, not operationIds), `loader.py` (leaf-tool naming;
  `SpecLoader.load()` docstring), `pagination.py` (`stitch` exclusions),
  `diff.py` (added docstrings), and `fetcher/fetch.py` (`Retry-After` parsing).
  No code behaviour changes.

## [0.5.0] - 2026-06-16

Closes the [v0.5.0 milestone](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/milestone/10) (#72).

### Added
- **Debug mode for capturing the upstream vManage request/response (#72).** A new
  off-by-default toggle (`debug.enabled` / `SDWAN_MCP_DEBUG=1` / `--debug`) makes
  the dispatcher attach a structured, redacted `debug` object to a tool result and
  log the same record to stderr, so opaque upstream errors — most notably the
  persistent `REST0001` 500s on the statistics-database query tools (#56, #62) —
  can be diagnosed from the *facts of the exchange* rather than inferred from a
  hint. The record carries the resolved method/path, the **exact serialized
  request body actually sent** (where the `params`-becomes-body shape gotcha shows
  up), and the full upstream status/`error_code`/headers/body, plus timing and the
  tool/action name. Redaction is on by default and masks both the auth headers
  (`Authorization` / `X-XSRF-TOKEN` / `Cookie` / `Set-Cookie`) **and
  credential-shaped body/query values** (keys matching `token` / `secret` /
  `password` / `xsrf` / `cookie` / `apiKey` / `sessionId` / …), so a capture of a
  token-returning endpoint such as `GET /client/token` is safe to share
  (`debug.redact: false` / `--debug-no-redact` to disable, with a startup
  warning). Oversized bodies are truncated to keep records bounded. Capture
  defaults to failed calls only; `debug.capture: all` /
  `--debug-all-calls` captures every call. Purely observational — no new tool, no
  mutating surface, safe in read-only mode. Default (debug off) behaviour is byte
  unchanged.

## [0.4.2] - 2026-06-16

Closes the [v0.4.2 milestone](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/milestone/8) (#65, #64).

### Fixed
- **Operations sharing a derived action name across tools are no longer dropped
  or misrouted (#65).** Action names are unique only *within* a tool, but the
  dispatch index was a single flat `action_name -> op` dict. When the splitter
  produced sibling tools that derived the same name from different URL paths
  (e.g. `get_feature_profile_sd_routing_bgp`), the flat index kept the first
  occurrence and silently dropped the rest — on 20.15 read-write that was 292 of
  3815 operations. Worse, calling the dropped action on its own tool resolved to
  the *other* tool's endpoint (a misroute). Dispatch is now **tool-scoped**: the
  loader builds a `by_tool` index and the dispatcher resolves an action within
  the calling tool's namespace. All operations are reachable and route correctly.
  The flood of `duplicate action_name … keeping first occurrence` WARNINGs at
  startup is replaced by a single summary line.

### Documentation
- Added a `streamable-http` Docker run example to the `Dockerfile` usage block
  and the Docker guide, alongside the existing stdio and SSE examples (#64).

## [0.4.1] - 2026-06-12

Closes the [v0.4.1 milestone](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/milestone/7) (#62).

### Fixed
- **Read-only mode can now reach the statistics-database query endpoints (#62).**
  vManage models its `/statistics/**` queries as **POST** (the query DSL rides in
  the request body); the `GET ?query=…` twin is rejected with `REST0001` on
  current builds. Read-only mode previously filtered to GET-only, so it exposed
  only the broken GET form and the entire historical-stats surface 500'd. The
  loader now admits **non-mutating** POST statistics-query endpoints in read-only
  mode and drops the superseded broken-GET twin, so `monitoring_bfd`,
  `monitoring_system_status_stats`, and the rest of the `statistics/*` query
  surface work without `--read-write`. Genuinely mutating POSTs under
  `/statistics` (e.g. `createQueueEntry`, `setDynamicCollection`) remain excluded
  from read-only mode, guarded by an operationId write-verb deny-list.

### Changed (behavior)
- In read-only mode, statistics-query actions are now the working POST form
  (e.g. `post_bfd`, `post_bfd_doccount`) instead of the broken GET form
  (`get_bfd`, …), which is no longer registered. Read-write mode is unchanged
  (both twins still load). The `#56` stats-DB 500 hint now points a failing GET
  raw-query form at its POST variant.

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
