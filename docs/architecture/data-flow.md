# Data flow

## Startup

```
1. server.py reads CLI flags + config.yaml + .env
2. SpecLoader loads specs/{version}/*.{yaml,yml,json}, merges,
   groups by tag (or section), drops mutations if RO,
   builds operationId index
3. VManageAuth.login() — JWT or session
4. Dispatcher attaches the index and a single httpx.AsyncClient
5. tools.register_tools(...) registers one MCP tool per group
6. mcp.run() — transport listens, dispatcher answers tool calls
```

## Tool call

```
LLM picks tool name, e.g. "monitoring"
LLM emits      { "action": "listAllDevices", "params": { "site-id": "500" } }

  ↓ FastMCP routes to the group's handler
  ↓ handler validates action ∈ valid operationIds
  ↓ dispatcher.call(operationId, params)

dispatcher:
  ↓ ensure_fresh()           # JWT refresh if needed
  ↓ split params -> path / query / body
  ↓ substitute path template
  ↓ httpx.request(method, url, params=, json=, headers=)

  ↓ if 302 welcome.html or 401:
    ↓ auth.login() again
    ↓ retry once

  ↓ return JSON (or error dict if non-2xx)
```

## Shutdown

```
mcp.run() returns when the transport closes
finally: dispatcher.close()
  ↓ auth.logout()
  ↓ httpx.AsyncClient.aclose()
```
