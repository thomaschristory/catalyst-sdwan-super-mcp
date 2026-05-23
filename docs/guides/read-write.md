# Read-only vs read-write

## The default is read-only

When the server starts without `--read-write`, **only `GET` operations are registered**. The LLM literally cannot see write tools: they are not in its context.

This is deliberate. A misfired `POST /dataservice/device/action/install` could push a firmware upgrade across your fleet.

## Enabling writes

```bash
uv run sdwan-mcp --read-write
```

This registers the rest of the HTTP verbs (`POST`, `PUT`, `DELETE`, `PATCH`).

The server's startup log tells you what it loaded:

```
[loader] Mode=RW, max_actions_per_tool=150 -> 360 tool(s), 4102 operations
```

## Recommended workflow

1. Always start in RO and let the LLM map the topology / answer questions.
2. When you want to actually change something, restart the server with `--read-write`.
3. For routine read-only telemetry, run a separate process locked to RO; for occasional changes, run a second process on a different port with RW enabled.

`docker-compose.yml` ships with both patterns — the RW service is commented out.
