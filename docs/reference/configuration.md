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

sdwan:
  specs_dir: ./specs                # default: ./specs
  active_version: "20.18"           # required — must match a folder in specs_dir (20.15, 20.16, 20.18 bundled)
  tag_granularity: section          # default: "section". Options: "section" | "tag"

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

## Precedence

CLI flags override `config.yaml`. Anything missing from both falls back to the dataclass defaults shown above.
