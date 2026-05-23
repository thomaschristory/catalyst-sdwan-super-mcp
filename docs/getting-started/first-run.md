# First run

## Run as stdio (Claude Desktop)

Default transport — no network ports, the MCP client spawns the server as a subprocess.

```bash
uv run sdwan-mcp
```

You should see something like:

```
[server] SD-WAN Super MCP
[server] Spec version : 20.18
[server] Mode         : READ-ONLY
[server] Transport    : stdio
[server] Auth         : JWT
[loader] Loading vmanageapi_2018.yaml
[loader] Loaded 1 spec file(s), 2802 total paths
[loader] Granularity=section -> 66 tool group(s)
[loader] Mode=RO: kept 2227 operations, filtered out 1875 write operations
[auth] JWT login successful
[server] 62 tools registered — starting stdio transport
```

## Run as SSE (network-accessible)

```bash
uv run sdwan-mcp --transport sse --host 0.0.0.0 --port 8000
```

## Enable writes

Off by default. Pass `--read-write` to register POST/PUT/DELETE/PATCH:

```bash
uv run sdwan-mcp --read-write
```

⚠ Writes are real: they mutate your vManage. Don't aim this at production until you've practiced on the [DevNet sandbox](sandbox.md).

## Diff two spec versions

```bash
uv run sdwan-mcp --diff 20.15 20.18
```

Outputs added/removed operations and parameter drift.
