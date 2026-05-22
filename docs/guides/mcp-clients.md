# MCP clients

The server speaks the [Model Context Protocol](https://modelcontextprotocol.io). Any compliant client can attach to it.

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

⚠ **No auth middleware yet** — don't expose the SSE port to anything untrusted. Track [issue](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/issues) labeled `security`.
