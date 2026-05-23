# CLI reference

```
sdwan-mcp [-h] [--version-info]
          [--config PATH]
          [--transport {stdio,sse,streamable-http}]
          [--host HOST] [--port PORT]
          [--read-write]
          [--version VERSION]
          [--diff OLD NEW]
          [--granularity {section,tag}]
```

## Flags

| Flag | Default | Description |
|---|---|---|
| `--config PATH` | `./config.yaml` | Alternate config file. |
| `--transport` | from config (`stdio`) | One of `stdio`, `sse`, `streamable-http`. |
| `--host` | from config (`127.0.0.1`) | Bind address for HTTP transports. |
| `--port` | from config (`8000`) | Bind port for HTTP transports. |
| `--read-write` | off | Enable POST/PUT/DELETE/PATCH. |
| `--version VERSION` | from config | Override the active spec version. |
| `--diff OLD NEW` | n/a | Print a diff between two spec versions, then exit. |
| `--granularity` | from config (`section`) | `section` (~65 tools) or `tag` (~375). |
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

# Crank tools up to 11
sdwan-mcp --granularity tag
```
