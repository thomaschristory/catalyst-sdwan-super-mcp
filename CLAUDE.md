# CLAUDE.md — catalyst-sdwan-super-mcp

## Project goal

A FastMCP server that exposes the Cisco Catalyst SD-WAN Manager (vManage) REST API
as MCP tools, with dynamic spec loading so it stays in sync as the API evolves
across versions. No codegen — everything is derived from the upstream OpenAPI
spec.

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

### Supported vManage versions

**20.15+ only.** Pre-20.15 specs are not bundled and not supported — see
issue [#13](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/issues/13)
for the analysis. The repo ships 20.15, 20.16, and 20.18 specs; 20.18 is the default.

### Adaptive tool splitting

Cisco's spec has thousands of operations. A single tool per section would push
the description payload past most clients' per-tool budgets. The loader splits
adaptively based on a size cap:

- `sdwan.max_actions_per_tool: 150` (default; `0` disables splitting).
- Algorithm: **section → if over cap, split by sub-tag → if a sub-tag is still
  over cap, recurse on URL path segments at depth 3, 4, 5**.
- Sibling sub-tags with `<4` operations collapse into a single `<parent>_misc`
  tool to avoid a long tail of tiny tools.
- Buckets still over the cap at depth 5 (or oversized `_misc` umbrellas) emit a
  WARNING but are still registered.

On 20.18 RW with default settings: 360 tools, max tool ~110 ops, 0 warnings.
Full algorithm, worked example, and tool-count tables live in
[docs/guides/tool-splitting.md](docs/guides/tool-splitting.md).

### Tool shape

```
tool name:    group slug  (e.g. monitoring, configuration_feature_profile_sdwan_transport)
description:  lists all actions with their params, built from the spec
args:
  action:     str — a derived stable name like get_device_status; NOT Cisco's operationId
  params:     dict — keys/values vary by action, documented in description
```

Action names come from `(HTTP method, URL path, OpenAPI tag)`, deduped within a
tool. Cisco's `operationId` is preserved on `OperationSpec` as a back-reference
for the `--diff` utility but never reaches the user.

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

### Session-based (legacy fallback for deployments without the JWT endpoint)

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
    loader.py                 spec loading, adaptive splitting (section/sub-tag/path), RO/RW filter, action-name derivation, indexing
    dispatcher.py             httpx client, param routing, retry on session expiry
    tools.py                  dynamic MCP tool registration
    diff.py                   version diff utility
  tests/                      pytest suite (test_loader, test_dispatcher, test_diff, test_config)
    conftest.py               minimal OpenAPI spec fixture
  docs/                       mkdocs-material site
    index.md
    getting-started/{install,first-run,sandbox}.md
    guides/{mcp-clients,read-write,tool-splitting,spec-versions,docker}.md
    reference/{cli,configuration,authentication}.md
    architecture/{overview,data-flow}.md
    contributing/{development,release-process}.md
  specs/                      OpenAPI documents, one folder per version
    20.15/vmanageapi_2015.yaml    ← bundled
    20.16/vmanageapi_2016.yaml    ← bundled
    20.18/vmanageapi_2018.yaml    ← bundled (default; matches DevNet sandbox)
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
  use_jwt: true                     # 20.15+ supports JWT; set false to force session

sdwan:
  specs_dir: ./specs
  active_version: "20.18"
  max_actions_per_tool: 150         # default; 0 disables splitting (see docs/guides/tool-splitting.md)

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
sdwan-mcp --version 20.15                          # override spec version
sdwan-mcp --max-actions-per-tool 50                # smaller, more numerous tools
sdwan-mcp --diff 20.15 20.18                       # diff two versions and exit
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
                  filters by RO/RW flag
                  adaptively splits ops into ToolGroups
                    (section → sub-tag → URL path; see tool-splitting.md)
                  derives a stable action_name per op
                  builds flat action_name → op index (plus operation_id index for --diff)
  → auth.py       VManageAuth initialised with credentials
  → dispatcher.py httpx.AsyncClient created
  → dispatcher.connect()  → auth.login() → JWT or session flow
  → tools.py      registers one fastmcp tool per group
  → mcp.run()     starts selected transport
```

### Tool call

```
LLM calls tool "monitoring"
  → tools.py       receives { action: "get_device_counters", params: {} }
                   validates action against the group's derived action_names
  → dispatcher.call("get_device_counters", {})
  → dispatcher     looks up op via SpecIndex.by_action_name
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

1. `_load_and_merge()` — glob `specs/{version}/*.{yaml,yml,json}`, merge into one dict.
2. `_extract_operations()` — flatten paths/methods into `OperationSpec`s; each gets a
   stable `action_name` derived from `(method, path, tag)` via `_derive_action_name()`.
3. `_split_into_groups()` — RO/RW filter, then bucket ops by section. For each section,
   `_split_section()` decides whether to keep it as one tool or split by sub-tag.
   Over-cap sub-tags fall through to `_split_by_path()`, which recurses on URL path
   segments at depth 3, 4, 5. Sibling buckets with `<4` ops collapse to `<parent>_misc`.
4. `_dedupe_tool_names()` and `_dedupe_action_names()` ensure uniqueness within and
   across tools (appending `_2`, `_3`, … on collision).
5. `_build_index()` — two flat dicts: `by_action_name` (used by the dispatcher) and
   `by_operation_id` (used only by `--diff`).

RO/RW filter:

```python
RO_METHODS = {"get"}
RW_METHODS = {"get", "post", "put", "delete", "patch"}
```

---

## Dispatcher logic (dispatcher.py)

Lookup: `SpecIndex.by_action_name[action_name]` → `OperationSpec`. Cisco's
`operation_id` is not used here — it's only kept around for `--diff`.

Path param injection:

- spec path `/device/{deviceId}` + params `{"deviceId": "10.0.0.1"}` → `/device/10.0.0.1`
- remaining params routed to query string (GET) or JSON body (POST/PUT/PATCH)

Unknown params (not in spec): forwarded as query params with a warning log.

Session expiry detection: 302 redirect with `welcome.html` in the Location header, or 401.
On expiry → `auth.login()` again → retry once.

---

## Diff utility (diff.py)

```bash
uv run sdwan-mcp --diff 20.15 20.18
```

Output:

```
=== SD-WAN API Diff: 20.15 → 20.18 ===

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
| Packaging | hatchling + uv | Modern Python packaging, fast dependency resolution |
| Tool splitting | Size-driven adaptive splitter (`max_actions_per_tool`, default 150). Section → sub-tag → URL path (depth 3–5). | The earlier `section`/`tag` toggle was too coarse: `section` lumped 1,500+ ops into one tool on `Configuration`; `tag` produced 375 micro-tools on 20.18. A single size cap with recursive fallback adapts cleanly to the spec's actual shape without a mode switch. (#13) |
| Action names | Derived from `(method, path, tag)`, not Cisco's `operationId`. | Cisco renamed ~31 % of legacy operationIds in place between 20.16 and 20.18 (`editPolicyList_33` → `editPolicyList_ConfigurationPolicySiteListBuilder_3103`). Same URL, same behaviour, different identifier. Our user-facing action name stays stable across that rename. operationId remains on `OperationSpec` as a back-reference for `--diff`. (#13) |
| Supported versions | 20.15+ only | Pre-20.15 specs use numeric-suffix operationIds that churn between minor releases; not worth the special-case shims (#13). |
| Params shape | `(action: str, params: dict)` | Scales with tag size; description documents per-action params |
| RO/RW | Flag at runtime | Safe default, explicit opt-in for mutations |
| Auth | Username/password → JWT or session | Matches actual vManage auth flow; API tokens require extra config |
| JWT vs session | JWT default, session fallback | JWT is simpler (one call); session needed for older deployments incl. the DevNet sandbox |
| Cookie handling | httpx jar auto-manages JSESSIONID | Sending a manual `Cookie:` header alongside the jar produces dupes and vManage rejects |
| Spec versioning | Drop folder + config line | No codegen, easy upgrade path |
| Spec formats | YAML, YML, **and JSON** | Cisco publishes 20.15 as YAML-with-`.json`-extension, 20.16/20.18 as plain YAML; we accept all three extensions. |
| Transport | Flag at runtime | stdio for local, SSE/HTTP for remote/tunneled |
| Docker | Volume-mounted specs | Upgrade specs without rebuilding image |

---

## Not yet implemented / future

Tracked as GitHub issues — see <https://github.com/thomaschristory/catalyst-sdwan-super-mcp/issues>.

- Auto-fetch specs from DevNet (`fetch_specs.py` — see issue with recommended approach)
- Auth middleware for HTTP transports (protect exposed SSE/streamable-http endpoints)
- Response pagination handling for bulk endpoints
- Retry / timeout config on httpx client
- Live integration test workflow against the DevNet sandbox
