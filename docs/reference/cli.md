# CLI reference

```
sdwan-mcp [-h] [--version-info]
          [--config PATH]
          [--transport {stdio,sse,streamable-http}]
          [--host HOST] [--port PORT]
          [--read-write]
          [--version VERSION]
          [--diff OLD NEW]
          [--max-actions-per-tool N]
          [--insecure-allow-public]
          [--debug] [--debug-all-calls] [--debug-no-redact]

sdwan-mcp fetch [--config PATH] (--version VERSION | --all-known)
                [--force] [--no-fragment-cache]

sdwan-mcp list-versions [--config PATH]
```

## Flags

| Flag | Default | Description |
|---|---|---|
| `--config PATH` | `./sdwan-mcp.yaml` | Alternate config file. |
| `--transport` | from config (`stdio`) | One of `stdio`, `sse`, `streamable-http`. |
| `--host` | from config (`127.0.0.1`) | Bind address for HTTP transports. |
| `--port` | from config (`8000`) | Bind port for HTTP transports. |
| `--read-write` | off | Enable POST/PUT/DELETE/PATCH. |
| `--version VERSION` | from config | Override the active spec version. |
| `--diff OLD NEW` | n/a | Print a diff between two spec versions, then exit. |
| `--max-actions-per-tool N` | from config (`150`) | Cap before the [adaptive splitter](../guides/tool-splitting.md) recurses. `0` disables splitting. |
| `--insecure-allow-public` | off | Allow binding to a non-loopback host with `transport.auth.type=none`. Without this flag, such a bind is auto-demoted to `127.0.0.1` with a stderr WARNING. Only use this when the server sits behind a trusted authenticating reverse proxy (mTLS, OIDC, a corporate auth gateway). The flag is intentionally verbose to discourage casual use. |
| `--debug` | off | Capture the upstream vManage request/response on failed calls and surface a redacted `debug` object in the result + stderr. Equivalent to `SDWAN_MCP_DEBUG=1`. See [debug mode](configuration.md#debug-capture-the-upstream-vmanage-exchange-72). |
| `--debug-all-calls` | off | With `--debug`: capture every call, not just failures (verbose). Equivalent to `SDWAN_MCP_DEBUG_CAPTURE=all`. |
| `--debug-no-redact` | off | With `--debug`: do **not** strip auth headers from captured output. Only safe on a trusted local terminal. Equivalent to `SDWAN_MCP_DEBUG_REDACT=0`. |
| `--version-info` | n/a | Print version and exit. |

## Examples

```bash
# Default — stdio, RO, version from config
sdwan-mcp

# RW, SSE, listening on all interfaces
sdwan-mcp --read-write --transport sse --host 0.0.0.0 --port 8000

# Try the new spec without changing config
sdwan-mcp --version 20.18

# Diff before upgrade
sdwan-mcp --diff 20.15 20.18

# Smaller, more numerous tools (lower cap → more aggressive splitting)
sdwan-mcp --max-actions-per-tool 50

# Diagnose a persistent REST0001 on the stats-DB tools — capture the exchange
sdwan-mcp --debug
```

## Subcommands

### `fetch`

Download the OpenAPI fragments for a vManage version from
`developer.cisco.com` and stitch them into `specs/<version>/vmanageapi_<flat>.yaml`.
Works without vManage credentials. See
[Spec versions](../guides/spec-versions.md) for how this fits into the
end-to-end flow.

| Flag | Description |
|---|---|
| `--version VERSION` | The spec version to fetch (e.g. `20.19`). |
| `--all-known` | Fetch every version in the curated KNOWN_VERSIONS list. |
| `--force` | Re-download even when the YAML already exists locally. |
| `--no-fragment-cache` | Skip the per-fragment JSON cache at `~/.cache/sdwan-mcp/fragments/`. |

### `list-versions`

Print known spec versions plus on-disk cache status. Useful before adding a
new version to `sdwan-mcp.yaml`.

```text
20.15  monolith  cached
20.16  split     cached
20.18  split     cached
20.19  split     not cached
21.1   split     not cached
```
