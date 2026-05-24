# Configuration

The server reads `./config.yaml` by default (override with `--config`). Environment variables are interpolated at load time using `${VAR_NAME}` syntax.

## Full schema

```yaml
vmanage:
  host: sandbox-sdwan-2.cisco.com   # required
  port: 443                         # default: 8443
  verify_ssl: false                 # default: false
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
  active_version: "20.18"           # required — must match a folder in specs_dir (20.15, 20.16, 20.18 bundled)
  max_actions_per_tool: 150         # default: 150. Cap before splitting; 0 disables splitting. See guides/tool-splitting.md

transport:
  mode: stdio                       # default: stdio. Options: stdio | sse | streamable-http
  host: 127.0.0.1                   # default. Bind address for HTTP transports.
  port: 8000                        # default. Bind port for HTTP transports.
```

## Environment variables

| Variable | Used by |
|---|---|
| `VMANAGE_USERNAME` | `${VMANAGE_USERNAME}` in `config.yaml` |
| `VMANAGE_PASSWORD` | `${VMANAGE_PASSWORD}` in `config.yaml` |

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

CLI flags override `config.yaml`. Anything missing from both falls back to the dataclass defaults shown above.
