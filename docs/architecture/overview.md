# Architecture overview

```
┌────────────────────────────────────────────────────────────────────────┐
│                            MCP client (LLM)                             │
└──────────────────────────┬──────────────────────────────────────────────┘
                           │  MCP JSON-RPC over stdio / SSE / HTTP
┌──────────────────────────▼──────────────────────────────────────────────┐
│                            FastMCP server                               │
│                                                                          │
│  ┌────────────┐   ┌──────────┐   ┌──────────┐   ┌─────────────────┐   │
│  │ config.py  │   │ loader.py│   │ tools.py │   │  dispatcher.py  │   │
│  │            │   │          │   │          │   │                 │   │
│  │ YAML + env │ ─►│  Read    │ ─►│ Register │ ─►│  httpx + auth   │   │
│  │ interpol.  │   │  spec,   │   │ one tool │   │  + retry        │   │
│  └────────────┘   │  group,  │   │ per tag  │   └────────┬────────┘   │
│                   │  filter, │   │ group    │            │            │
│                   │  index   │   └──────────┘   ┌────────▼────────┐   │
│                   └──────────┘                  │     auth.py     │   │
│                                                 │ JWT or session  │   │
│                                                 └────────┬────────┘   │
└──────────────────────────────────────────────────────────┬───────────┘
                                                            │  HTTPS
                                                            ▼
                                                ┌───────────────────────┐
                                                │   Cisco vManage       │
                                                └───────────────────────┘
```

## Modules

| Module | Responsibility |
|---|---|
| `config.py` | Load `sdwan-mcp.yaml`, interpolate `${VAR}`, validate. |
| `loader.py` | Read OpenAPI spec files, [adaptively split](../guides/tool-splitting.md) operations into tools (section → sub-tag → URL path), filter by RO/RW, derive stable `action_name` per op, build a flat `action_name → op` index. |
| `fetcher/` | Live spec ingestion for vManage 20.16+. Pulls per-operation and per-schema JSON fragments from `developer.cisco.com`, stitches into a single OpenAPI 3.1 document, validates, and writes `specs/<version>/vmanageapi_<flat>.yaml`. Drives both the startup auto-fetch and the `sdwan-mcp fetch` subcommand. |
| `auth.py` | Two login flows to vManage (JWT and legacy session), header injection, proactive refresh. |
| `transport_auth.py` | Bearer-token middleware for the SSE / streamable-HTTP transports — constant-time comparison, rate-limited rejection logs, RFC 6750 `WWW-Authenticate` on 401. |
| `dispatcher.py` | One `httpx.AsyncClient` per server. Looks ops up by `action_name`, routes params (path / query / body), executes, applies the [retry + timeout policy](../reference/configuration.md#retry-behavior), and retries once on session expiry. |
| `pagination.py` | Scroll- and offset-style auto-follow for bulk endpoints. Stitches up to `max_pages` pages and surfaces a resumable cursor under `pagination.next_cursor`. |
| `tools.py` | Register one FastMCP tool per group, with `(action, params)` signature. |
| `diff.py` | Compare two spec versions — added / removed / changed operations. Uses Cisco's `operationId` (preserved on `OperationSpec` as a back-reference) so the diff matches Cisco's own identifiers. |
| `server.py` | CLI parsing, subcommand dispatch (`fetch`, `list-versions`), async pre-flight, lifecycle. |

## Why dynamic instead of generated?

Cisco ships a fresh spec every minor release. With 2,000+ operations, hand-generating Python wrappers means a lot of code churn and a lot of subtle drift. Loading the spec at startup means:

- A new vManage version is one folder + one config line.
- The MCP tool descriptions are *always* the spec the server is actually talking to.
- The diff utility tells you what changed at a glance.
