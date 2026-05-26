# Data flow

## Startup

```
1. server.py reads CLI flags + sdwan-mcp.yaml + .env
2. If specs/{active_version}/ is empty and sdwan.auto_fetch is true,
   fetcher/ pulls OpenAPI fragments from developer.cisco.com and writes
   specs/{version}/vmanageapi_<flat>.yaml (>= 20.16 only)
3. SpecLoader loads specs/{version}/*.{yaml,yml,json}, merges,
   drops mutations if RO, adaptively splits ops into ToolGroups
   (section -> sub-tag -> URL path, see guides/tool-splitting.md),
   derives a stable action_name per op, builds an action_name index
4. Bind-safety check: if transport is sse/streamable-http with auth=none
   and host is non-loopback, demote to 127.0.0.1 unless
   --insecure-allow-public was passed
5. VManageAuth.login() — JWT or session
6. Dispatcher attaches the index and a single httpx.AsyncClient
   (timeout + retry policy from vmanage.timeout / vmanage.retries)
7. tools.register_tools(...) registers one MCP tool per group
8. For HTTP transports: transport_auth middleware wraps the ASGI app
   if transport.auth.type == "bearer"
9. mcp.run() — transport listens, dispatcher answers tool calls
```

## Tool call

```
LLM picks tool name, e.g. "monitoring"
LLM emits      { "action": "get_device", "params": { "site-id": "500" } }

  ↓ FastMCP routes to the group's handler
  ↓ handler validates action ∈ derived action_names for this group
  ↓ dispatcher.call(action_name, params)
  ↓ dispatcher looks op up via SpecIndex.by_action_name[action_name]

dispatcher:
  ↓ ensure_fresh()           # JWT refresh if needed
  ↓ split params -> path / query / body
  ↓ substitute path template
  ↓ httpx.request(method, url, params=, json=, headers=)

  ↓ if 302 welcome.html or 401:
    ↓ auth.login() again
    ↓ retry once

  ↓ if op.pagination is set and pagination enabled (and _pagination != "off"):
    ↓ route to ScrollPaginator or OffsetPaginator
      (calls back into the single-page executor up to max_pages)
      stitch pages → wrap as {data, pagination, ...rest}
  ↓ else: return single-page JSON as before

  ↓ return JSON (or error dict if non-2xx)
```

## Shutdown

```
mcp.run() returns when the transport closes
finally: dispatcher.close()
  ↓ auth.logout()
  ↓ httpx.AsyncClient.aclose()
```
