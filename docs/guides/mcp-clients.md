# MCP clients

The server speaks the [Model Context Protocol](https://modelcontextprotocol.io). Any compliant client can attach to it.

## Setting the bearer token

When `transport.auth.type: bearer` is configured (see
[configuration reference](../reference/configuration.md)), every HTTP
request must include:

```
Authorization: Bearer <your-token>
```

How you set this depends on the client:

- **Claude Desktop** — add a `headers` block under the SSE/streamable-http
  server entry in `claude_desktop_config.json`:

  ```json
  {
    "mcpServers": {
      "sdwan": {
        "url": "http://your-host:8000/mcp",
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
      "http://your-host:8000/mcp",
      headers={"Authorization": f"Bearer {os.environ['SDWAN_MCP_TOKEN']}"},
  ) as client:
      ...
  ```

- **Cline / Continue / other MCP clients** — check the client's docs for
  custom HTTP headers. The header name is `Authorization`, the value is
  `Bearer <token>`.

## Claude Desktop (stdio)

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or the equivalent on Windows:

```json
{
  "mcpServers": {
    "sdwan": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/catalyst-sdwan-super-mcp", "run", "sdwan-mcp"],
      "env": {
        "VMANAGE_USERNAME": "devnetuser",
        "VMANAGE_PASSWORD": "RG!_Yw919_83"
      }
    }
  }
}
```

Restart Claude Desktop. You should see the `sdwan` server in the MCP indicator.

## Claude Code

Use the project-local config (`.mcp.json`) or the global one (`~/.claude/mcp.json`):

```json
{
  "mcpServers": {
    "sdwan": {
      "command": "uv",
      "args": ["--directory", ".", "run", "sdwan-mcp"]
    }
  }
}
```

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
uv run sdwan-mcp --transport sse --host 0.0.0.0 --port 8000
```

When exposing the server over the network, configure bearer token auth via
`transport.auth.type: bearer` in `config.yaml` and set the header on the client
as shown in the [Setting the bearer token](#setting-the-bearer-token) section above.
