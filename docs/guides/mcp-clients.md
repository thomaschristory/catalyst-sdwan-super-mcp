# MCP clients

The server speaks the [Model Context Protocol](https://modelcontextprotocol.io). Any compliant client can attach to it.

## Setting the bearer token

When `transport.auth.type: bearer` is configured (see
[configuration reference](../reference/configuration.md)), every HTTP
request must include:

```
Authorization: Bearer <your-token>
```

!!! danger "Always run bearer mode over HTTPS"
    Bearer tokens travel in plaintext inside the `Authorization` header.
    Serving them over `http://` exposes the token to anyone on the network
    path (Wi-Fi neighbours, ISP, transparent proxies) — equivalent to no
    auth on a shared network. **Always front a bearer-mode endpoint with
    HTTPS** via a reverse proxy (Caddy auto-issues a cert with one line of
    config; nginx, Traefik, or a corporate gateway work equally well). The
    URLs in the examples below use `http://` only for local-loopback
    illustration.

How you set the header depends on the client:

- **Claude Desktop** — add a `headers` block under the SSE/streamable-http
  server entry in `claude_desktop_config.json`:

  ```json
  {
    "mcpServers": {
      "sdwan": {
        "url": "https://your-host/mcp",
        "headers": {
          "Authorization": "Bearer ${SDWAN_MCP_TOKEN}"
        }
      }
    }
  }
  ```

- **fastmcp Python client** — pass `headers=` when constructing the client:

  ```python
  from fastmcp import Client

  async with Client(
      "https://your-host/mcp",
      headers={"Authorization": f"Bearer {os.environ['SDWAN_MCP_TOKEN']}"},
  ) as client:
      ...
  ```

- **Cline / Continue / other MCP clients** — check the client's docs for
  custom HTTP headers. The header name is `Authorization`, the value is
  `Bearer <token>`.

### Generating a token

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

The server rejects tokens shorter than 8 characters outright and warns on
anything under 16. Aim for ≥32 characters of URL-safe base64.

### Rate-limiting and brute force

The middleware uses constant-time comparison so individual rejections leak
no timing information, and rejection logs are throttled (at most 10
`WARNING` lines per minute followed by a "suppressed N more" rollup) so a
brute-force flood cannot fill the disk. There is **no built-in rate limit
or lockout** on rejection rate itself — front the endpoint with a reverse
proxy that does (nginx `limit_req`, Caddy `rate_limit`, or fail2ban) if
the network surface is hostile.

## Recommended: launch via `uvx`

The blocks below use [`uvx`](https://docs.astral.sh/uv/), which fetches and runs the
published package on demand — no source checkout and no absolute paths to maintain.
If you installed the package instead (`uv tool install` / `pipx`), replace
`"command": "uvx", "args": ["catalyst-sdwan-super-mcp"]` with
`"command": "sdwan-mcp", "args": []`. To enable mutations, append `"--read-write"`
to `args`.

## Claude Code

One command:

```bash
claude mcp add sdwan \
  -e VMANAGE_USERNAME=devnetuser \
  -e VMANAGE_PASSWORD='RG!_Yw919_83' \
  -e VMANAGE_VERIFY_SSL=false \
  -- uvx catalyst-sdwan-super-mcp
```

(`VMANAGE_VERIFY_SSL=false` is only for the self-signed DevNet sandbox — omit it for a production vManage with a valid certificate.)

…or commit a project-local `.mcp.json` (global config: `~/.claude/mcp.json`):

```json
{
  "mcpServers": {
    "sdwan": {
      "command": "uvx",
      "args": ["catalyst-sdwan-super-mcp"],
      "env": {
        "VMANAGE_USERNAME": "devnetuser",
        "VMANAGE_PASSWORD": "RG!_Yw919_83",
        "VMANAGE_VERIFY_SSL": "false"
      }
    }
  }
}
```

## Claude Desktop (stdio)

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "sdwan": {
      "command": "uvx",
      "args": ["catalyst-sdwan-super-mcp"],
      "env": {
        "VMANAGE_USERNAME": "devnetuser",
        "VMANAGE_PASSWORD": "RG!_Yw919_83",
        "VMANAGE_VERIFY_SSL": "false"
      }
    }
  }
}
```

Restart Claude Desktop. You should see the `sdwan` server in the MCP indicator.

## Cursor

Add to `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (per project):

```json
{
  "mcpServers": {
    "sdwan": {
      "command": "uvx",
      "args": ["catalyst-sdwan-super-mcp"],
      "env": {
        "VMANAGE_USERNAME": "devnetuser",
        "VMANAGE_PASSWORD": "RG!_Yw919_83",
        "VMANAGE_VERIFY_SSL": "false"
      }
    }
  }
}
```

Other stdio clients (Cline, Continue, Windsurf, Zed, …) use the same shape: the
`uvx catalyst-sdwan-super-mcp` command with `VMANAGE_*` in the environment.

## Docker (stdio)

```json
{
  "mcpServers": {
    "sdwan": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-e", "VMANAGE_USERNAME",
        "-e", "VMANAGE_PASSWORD",
        "-v", "/absolute/path/to/specs:/app/specs",
        "catalyst-sdwan-super-mcp"
      ]
    }
  }
}
```

## SSE / streamable-HTTP

For clients that connect over the network rather than spawning a subprocess:

```bash
sdwan-mcp --transport sse --host 0.0.0.0 --port 8000
```

When exposing the server over the network, configure bearer token auth via
`transport.auth.type: bearer` in `sdwan-mcp.yaml` and set the header on the client
as shown in the [Setting the bearer token](#setting-the-bearer-token) section above.
