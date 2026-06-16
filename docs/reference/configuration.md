# Configuration

The YAML config file is **optional**. Configuration is resolved from these sources, highest priority first:

1. **CLI flags** (e.g. `--version`, `--transport`, `--read-write`)
2. **Environment variables** — `VMANAGE_USERNAME`, `VMANAGE_PASSWORD`, and optionally `VMANAGE_HOST`, `VMANAGE_PORT`, `VMANAGE_VERIFY_SSL`, `VMANAGE_USE_JWT`, `VMANAGE_TIMEOUT`
3. **The YAML file** — `./sdwan-mcp.yaml` by default (override with `--config`); values still support `${VAR_NAME}` interpolation
4. **Built-in defaults** — the Cisco DevNet sandbox

You can run the server with **no config file at all** — just export the credentials (or put them in a `.env`):

```bash
export VMANAGE_USERNAME=devnetuser
export VMANAGE_PASSWORD='...'
sdwan-mcp
```

This is what makes the server work when installed via `uv tool install` and launched by an MCP client (whose working directory is not your project dir, so no YAML is on disk). Environment variables override matching YAML values. Passing `--config PATH` to a file that does not exist is still an error.

## Full schema

```yaml
vmanage:
  host: sandbox-sdwan-2.cisco.com   # required
  port: 443                         # default: 8443
  verify_ssl: false                 # default: true — keep true in production; the self-signed sandbox opts out with false
  username: "${VMANAGE_USERNAME}"   # required (use env var)
  password: "${VMANAGE_PASSWORD}"   # required (use env var)
  use_jwt: true                     # default: true. Set to false to force JSESSIONID + XSRF fallback.
  timeout: 30.0                     # default: 30.0. Per-request httpx timeout in seconds.
  retries:                          # transient-failure retry policy
    max_attempts: 3                 # default: 3. Total attempts incl. first try; 1 disables retries.
    statuses: [502, 503, 504]       # default. HTTP status codes to retry.
    backoff_base: 0.5               # default: 0.5. Seconds; first backoff is base * 2**0 with jitter.
    backoff_cap: 8.0                # default: 8.0. Upper bound on a single backoff.
    retry_mutating: false           # default: false. Retry POST/PUT/DELETE/PATCH too. Off by default for safety.

sdwan:
  specs_dir: ./specs                # default: ./specs
  active_version: "20.18"           # required — names a folder in specs_dir. Auto-fetched if missing (see auto_fetch).
  max_actions_per_tool: 150         # default: 150. Cap before splitting; 0 disables splitting. See guides/tool-splitting.md
  auto_fetch: true                  # default: true. If specs/<active_version>/ is missing, fetch from developer.cisco.com on startup.

debug:
  enabled: false                    # default: false. Capture upstream request/response on failure.
  redact: true                      # default: true. Strip auth headers from captured output.
  capture: errors                   # default: errors. Options: errors | all.

transport:
  mode: stdio                       # default: stdio. Options: stdio | sse | streamable-http
  host: 127.0.0.1                   # default. Bind address for HTTP transports.
  port: 8000                        # default. Bind port for HTTP transports.
  auth:
    type: none                      # default: none. Options: none | bearer
    token: ""                       # required when type: bearer. Supports ${ENV_VAR}.
```

## `debug` — capture the upstream vManage exchange (#72)

Debug mode is **off by default and changes nothing when off**. Turning it on
makes the dispatcher attach a structured `debug` object to tool results and
log the same record to stderr, so you can see exactly what was sent to and
returned by vManage. It is the way to diagnose opaque upstream errors — most
notably the `REST0001` 500s the statistics-database query tools return — by
capturing the *facts of the exchange* instead of inferring from a hint.

It is purely observational: it adds no new tool and no mutating surface, so it
is safe to enable in read-only mode.

| Key       | Type   | Default    | Description                                                                 |
|-----------|--------|------------|-----------------------------------------------------------------------------|
| `enabled` | bool   | `false`    | Master switch. Also `SDWAN_MCP_DEBUG=1` or `--debug`.                        |
| `redact`  | bool   | `true`     | Strip `Authorization` / `X-XSRF-TOKEN` / `Cookie` / `Set-Cookie` headers. `SDWAN_MCP_DEBUG_REDACT=0` / `--debug-no-redact`. |
| `capture` | string | `errors`   | `errors` = failed calls only; `all` = every call. `SDWAN_MCP_DEBUG_CAPTURE` / `--debug-all-calls`. |

### What gets captured

For a failed call (and for every call under `capture: all`):

```jsonc
{
  "tool": "monitoring_bfd",
  "action": "post_bfd_doccount",
  "operation_id": "...",          // Cisco's operationId, for cross-referencing the spec
  "timing_ms": 142.7,
  "request": {
    "method": "POST",
    "path": "/statistics/bfd/doccount",
    "url": "https://vmanage.example:443/dataservice/statistics/bfd/doccount",
    "query_params": {},
    "body": { "query": { "condition": "AND", "rules": [ ... ] } },  // the serialized body actually sent
    "headers": { "Authorization": "<redacted>", "X-XSRF-TOKEN": "<redacted>", "Content-Type": "application/json" }
  },
  "response": {
    "status_code": 500,
    "error_code": "REST0001",
    "headers": { ... },
    "body": { "error": { "message": "Server error", "code": "REST0001", "details": "..." } }
  }
}
```

The `request.body` is the exact payload sent upstream — this is where the
request-shape gotcha shows up: tool `params` are forwarded straight through as
the HTTP body, so a `query` payload must sit at the **top level**, not nested
under `body`.

### Redaction

With `redact: true` (the default), captured output is scrubbed in both the
result object and the stderr log so it is safe to paste into a GitHub issue:

- **Auth headers** — `Authorization`, `X-XSRF-TOKEN`, `Cookie`, `Set-Cookie`
  (and `Proxy-Authorization`) — are replaced with `<redacted>`.
- **Credential-shaped body/query values** — the value of any key whose name
  matches `token` / `secret` / `password` / `xsrf` / `cookie` / `apiKey` /
  `sessionId` / `credential` / `privateKey` (case-insensitive substring) is
  replaced with `<redacted>`. This matters because a few reachable GETs return
  a live token *in the body* — e.g. `GET /client/token` (`getCsrfToken`) yields
  `{"token": "<live XSRF token>"}` even in read-only mode. The query DSL,
  vManage error codes, and ordinary data fields pass through untouched.

Set `redact: false` only on a trusted local terminal when you need to confirm
the literal token on the wire; the server prints a warning when redaction is
off.

Captured bodies larger than ~20 KB (serialized) are truncated to a
`{"_truncated": true, "_original_chars": N, "preview": "..."}` marker so an
opt-in debug session can't silently double or overflow a tool result.

### Capture scope and response shape

- `capture: errors` (default) attaches `debug` only to failed upstream calls.
- `capture: all` also attaches `debug` to successful calls **when the response
  is a JSON object**. A successful list- or string-shaped response is returned
  verbatim (no silent wrapping) and its debug record is written to stderr only.
- Either way, every captured record is logged to stderr as a single
  `[dispatcher][debug] {...}` JSON line.

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

## Environment variables

| Variable | Used by |
|---|---|
| `VMANAGE_USERNAME` | `${VMANAGE_USERNAME}` in `sdwan-mcp.yaml` |
| `VMANAGE_PASSWORD` | `${VMANAGE_PASSWORD}` in `sdwan-mcp.yaml` |
| `SDWAN_MCP_DEBUG` | `debug.enabled` — `1`/`true` turns debug capture on |
| `SDWAN_MCP_DEBUG_REDACT` | `debug.redact` — `0`/`false` disables header redaction |
| `SDWAN_MCP_DEBUG_CAPTURE` | `debug.capture` — `errors` or `all` |

`.env` is auto-loaded if present (via `python-dotenv`).

## Retry behavior

Transient failures from the load balancer in front of vManage are common
(502 / 503 / 504, connection resets, timeouts). The dispatcher retries them
with exponential backoff and equal jitter — `delay = (raw/2) + uniform(0, raw/2)`,
where `raw = min(backoff_cap, backoff_base * 2**attempt)`.

What is retried:

- HTTP responses whose status is in `vmanage.retries.statuses`.
- `httpx.TimeoutException` and other `httpx.RequestError` subclasses
  (connection resets, DNS failures).

What is **not** retried by default:

- POST / PUT / DELETE / PATCH — they may not be idempotent on vManage.
  Flip `retry_mutating: true` only if you know your operations are safe to
  replay.
- 4xx responses other than those explicitly listed in `statuses`.

The session-expiry re-login (302 to `welcome.html` or 401) is a separate
layer that re-authenticates once on top of any transport-level retries.

## Precedence

CLI flags override `sdwan-mcp.yaml`. Anything missing from both falls back to the dataclass defaults shown above.
