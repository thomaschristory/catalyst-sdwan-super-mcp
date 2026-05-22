# CLAUDE.md — catalyst-sdwan-super-mcp

## Project goal

A FastMCP server that exposes the Cisco Catalyst SD-WAN Manager (vManage) REST API
as MCP tools, with dynamic spec loading so it stays in sync as the API evolves
across versions. Same pattern as `netbox-super-cli`: no codegen — derive everything
from the upstream OpenAPI spec.

Public repo: <https://github.com/thomaschristory/catalyst-sdwan-super-mcp>.
Docs: <https://thomaschristory.github.io/catalyst-sdwan-super-mcp/>.

---

## Stack

- **Python** ≥ 3.11 (CI: 3.11, 3.12, 3.13 on Linux + macOS)
- **Packaging:** `pyproject.toml` (hatchling), managed with `uv`
- **MCP framework:** `fastmcp`
- **HTTP client:** `httpx` (async)
- **Config parsing:** `pyyaml` + `python-dotenv`
- **Tests:** `pytest`, `respx` (HTTP mocks), `pytest-asyncio`
- **Lint / format:** `ruff`
- **Docs:** `mkdocs-material`, deployed to GitHub Pages

Setup:

```bash
uv sync --group dev --group docs
uv run sdwan-mcp --help
```

---

## Architecture

### No code generation — dynamic loading

The server reads the OpenAPI spec at startup and registers MCP tools dynamically.
Upgrading to a new vManage version = drop a new spec folder + change one config line.

### Two grouping granularities

Cisco's OpenAPI tags look like `Monitoring - Device Details` and
`Configuration - Feature Profile (SDWAN)`. We support two grouping modes:

- **`section`** (default) — group by the first word, e.g. `Configuration`.
  On vManage 20.10 this collapses to **~38 tools**, which most LLM clients handle well.
- **`tag`** — group by the full tag, yielding ~300 tools on 20.10. Use when your
  client can ingest hundreds of tools and you want narrower per-tool descriptions.

Configurable via `sdwan.tag_granularity` in `config.yaml` or `--granularity` on the CLI.

### Tool shape

```
tool name:    group slug  (e.g. monitoring, configuration_device_actions)
description:  lists all actions with their params, built from the spec
args:
  action:     str — one of the operationIds in this group
  params:     dict — keys/values vary by action, documented in description
```

### Read-only by default

HTTP method filtering at startup:

- RO mode (default): registers GET endpoints only
- RW mode (`--read-write` flag): registers GET + POST + PUT + DELETE + PATCH

The LLM never sees write tools in RO mode — they are not registered, not in context.

---

## Authentication

vManage does NOT use standard Bearer API tokens. Auth is credential-based with two
modes:

### JWT (default, recommended for 20.18.1+)

```
POST /j_security_check  { j_username, j_password }
→ { token, xsrfToken, expiresIn }

Subsequent requests:
  Authorization: Bearer {token}
  X-XSRF-TOKEN: {xsrfToken}
```

Token is refreshed proactively when within 2 minutes of expiry.

### Session-based (legacy fallback for older vManage, e.g. 20.10)

```
POST /j_security_check  { j_username, j_password }
→ Set-Cookie: JSESSIONID=...  (success: empty body)
                              (failure: full login-form HTML in body)

GET /dataservice/client/token  (cookie auto-attached by httpx jar)
→ plain text xsrf token

Subsequent requests:
  X-XSRF-TOKEN: {xsrfToken}
  (cookie auto-attached by httpx client)
```

**Important:** `httpx.AsyncClient` keeps a cookie jar. We let it manage `JSESSIONID`
automatically and only set `X-XSRF-TOKEN` ourselves — passing a manual `Cookie` header
in addition produces duplicate cookies and vManage rejects the second copy. This bit
me during the first sandbox test.

Set `use_jwt: false` in config.yaml to force session mode. Logout is called on shutdown
to cleanly release the server-side session.

---

## Project structure

```
catalyst-sdwan-super-mcp/
  sdwan_mcp/                  source package
    __init__.py               version
    server.py                 entrypoint, CLI, async pre-flight
    config.py                 config.yaml loader + ${ENV} interpolation
    auth.py                   JWT + session login, refresh, logout
    loader.py                 spec loading, tag grouping, RO/RW filter, indexing
    dispatcher.py             httpx client, param routing, retry on session expiry
    tools.py                  dynamic MCP tool registration
    diff.py                   version diff utility
  tests/                      pytest suite (test_loader, test_dispatcher, test_diff, test_config)
    conftest.py               minimal OpenAPI spec fixture
  docs/                       mkdocs-material site
    index.md
    getting-started/{install,first-run,sandbox}.md
    guides/{mcp-clients,read-write,granularity,spec-versions,docker}.md
    reference/{cli,configuration,authentication}.md
    architecture/{overview,data-flow}.md
    contributing/{development,release-process}.md
  specs/                      OpenAPI documents, one folder per version
    20.10/vmanageapi_2010.json    ← bundled (matches DevNet sandbox)
  .github/
    workflows/{lint,test,docs,docker,release}.yml
    ISSUE_TEMPLATE/{bug,feature}.yml
    dependabot.yml
  scripts/                    helper scripts (currently empty placeholder)
  pyproject.toml              project + ruff + mypy + pytest config
  mkdocs.yml
  Dockerfile                  multi-stage, uv-based
  docker-compose.yml          SSE on :8000 by default
  config.yaml                 default config — points at DevNet sandbox
  .env.example
  CHANGELOG.md
  LICENSE                     Apache-2.0
  README.md
  CLAUDE.md                   this file
```

`specs/{version}/` accepts `*.yaml`, `*.yml`, and `*.json` files — they are merged
in name order.

---

## Config file

```yaml
# config.yaml
vmanage:
  host: sandbox-sdwan-2.cisco.com   # DevNet sandbox by default
  port: 443
  verify_ssl: false
  username: "${VMANAGE_USERNAME}"
  password: "${VMANAGE_PASSWORD}"
  use_jwt: false                    # false for 20.10; true for 20.18.1+

sdwan:
  specs_dir: ./specs
  active_version: "20.10"
  tag_granularity: section          # "section" (~30-40 tools) or "tag" (300+)

transport:
  mode: stdio                       # stdio | sse | streamable-http
  host: 127.0.0.1
  port: 8000
```

---

## CLI flags

```bash
sdwan-mcp                                          # stdio, RO, version from config
sdwan-mcp --transport sse --port 8000              # SSE transport
sdwan-mcp --transport streamable-http              # streamable HTTP
sdwan-mcp --read-write                             # enable mutations
sdwan-mcp --version 20.10                          # override spec version
sdwan-mcp --granularity tag                        # override granularity
sdwan-mcp --diff 20.10 20.18                       # diff two versions and exit
sdwan-mcp --config /path/to/config.yaml            # custom config file
```

The `catalyst-sdwan-super-mcp` script name is also registered if you prefer the long form.

---

## Docker

```bash
# Build
docker build -t catalyst-sdwan-super-mcp .

# Claude Desktop (stdio) — specs mounted from host
docker run -i --rm \
  -e VMANAGE_USERNAME=devnetuser \
  -e VMANAGE_PASSWORD='RG!_Yw919_83' \
  -v "$(pwd)/specs:/app/specs" \
  catalyst-sdwan-super-mcp

# SSE (network-accessible)
docker run -p 8000:8000 \
  -e VMANAGE_USERNAME=devnetuser \
  -e VMANAGE_PASSWORD='RG!_Yw919_83' \
  -v "$(pwd)/specs:/app/specs" \
  catalyst-sdwan-super-mcp --transport sse --host 0.0.0.0 --port 8000

# Via docker-compose (SSE by default)
docker compose up -d
```

Specs are always mounted as a volume — never baked into the image — so you can
upgrade vManage versions without rebuilding.

---

## Data flow

### Startup

```
server.py (async pre-flight)
  → config.py     reads config.yaml, interpolates env vars
  → loader.py     loads all *.{yaml,yml,json} from specs/{version}/
                  merges paths + schemas
                  groups operations by tag or section
                  filters by RO/RW flag
                  builds flat operationId index
  → auth.py       VManageAuth initialised with credentials
  → dispatcher.py httpx.AsyncClient created
  → dispatcher.connect()  → auth.login() → JWT or session flow
  → tools.py      registers one fastmcp tool per group
  → mcp.run()     starts selected transport
```

### Tool call

```
LLM calls tool "monitoring"
  → tools.py       receives { action: "getDeviceCounters", params: {} }
                   validates action against known operationIds
  → dispatcher.call("getDeviceCounters", {})
  → dispatcher     looks up op in spec index
                   resolves path template, splits query/body params
                   fires httpx request with auth headers
                   on 302/welcome.html or 401: re-auths, retries once
  → LLM            receives JSON response
```

### Shutdown

```
finally block in server.py
  → dispatcher.close()
    → auth.logout()
    → client.aclose()
```

---

## Loader logic (loader.py)

1. `_load_and_merge()` — glob `specs/{version}/*.{yaml,yml,json}`, merge into one dict
2. `_group_by_tag()` — iterate paths/methods, group by tag or section
3. `_filter_by_mode()` — drop non-GET if RO mode
4. `_build_index()` — flat dict keyed by operationId for O(1) dispatch lookup

RO filter:

```python
RO_METHODS = {"get"}
RW_METHODS = {"get", "post", "put", "delete", "patch"}
```

---

## Dispatcher logic (dispatcher.py)

Path param injection:

- spec path `/device/{deviceId}` + params `{"deviceId": "10.0.0.1"}` → `/device/10.0.0.1`
- remaining params routed to query string (GET) or JSON body (POST/PUT/PATCH)

Unknown params (not in spec): forwarded as query params with a warning log.

Session expiry detection: 302 redirect with `welcome.html` in the Location header, or 401.
On expiry → `auth.login()` again → retry once.

---

## Diff utility (diff.py)

```bash
uv run sdwan-mcp --diff 20.10 20.18
```

Output:

```
=== SD-WAN API Diff: 20.10 → 20.18 ===

REMOVED (breaking):
  - getVedgeList  [Monitoring - Device Details]  GET /device/vedge

ADDED:
  + getDeviceById  [Monitoring - Device Details]  GET /device/{deviceId}

CHANGED (parameter drift):
  ~ listAllDevices  [Monitoring - Device Details]
      added: 'includeTenantvSmart' — query, boolean, optional
```

---

## Key decisions log

| Decision | Choice | Reason |
|---|---|---|
| Language | Python ≥ 3.11 | Simpler local iteration, no build step |
| MCP framework | fastmcp | Minimal boilerplate |
| Packaging | hatchling + uv | Matches netbox-super-cli |
| Tool grouping | One per **section** by default | 20.10 has 304 tags — too many. Section yields ~38 tools. `tag` mode still available. |
| Params shape | `(action: str, params: dict)` | Scales with tag size; description documents per-action params |
| RO/RW | Flag at runtime | Safe default, explicit opt-in for mutations |
| Auth | Username/password → JWT or session | Matches actual vManage auth flow; API tokens require extra config |
| JWT vs session | JWT default, session fallback | JWT is simpler (one call); session needed for older deployments incl. the DevNet sandbox |
| Cookie handling | httpx jar auto-manages JSESSIONID | Sending a manual `Cookie:` header alongside the jar produces dupes and vManage rejects |
| Spec versioning | Drop folder + config line | No codegen, easy upgrade path |
| Spec formats | YAML, YML, **and JSON** | Cisco DevNet publishes the 20.10 spec as JSON |
| Transport | Flag at runtime | stdio for local, SSE/HTTP for remote/tunneled |
| Docker | Volume-mounted specs | Upgrade specs without rebuilding image |

---

## Not yet implemented / future

Tracked as GitHub issues — see <https://github.com/thomaschristory/catalyst-sdwan-super-mcp/issues>.

- Auto-fetch specs from DevNet (`fetch_specs.py` — see issue with recommended approach)
- Auth middleware for HTTP transports (protect exposed SSE/streamable-http endpoints)
- Response pagination handling for bulk endpoints
- Retry / timeout config on httpx client
- Per-action subtools (split very large groups like `configuration` into multiple tools)
- Live integration test workflow against the DevNet sandbox
