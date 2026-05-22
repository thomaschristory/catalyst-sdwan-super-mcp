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
| `config.py` | Load `config.yaml`, interpolate `${VAR}`, validate. |
| `loader.py` | Read OpenAPI spec files, group operations by tag (or section), filter by RO/RW, build a flat operationId → op index. |
| `auth.py` | Two login flows (JWT and legacy session), header injection, proactive refresh. |
| `dispatcher.py` | One `httpx.AsyncClient` per server. Routes params (path / query / body), executes, retries once on session expiry. |
| `tools.py` | Register one FastMCP tool per group, with `(action, params)` signature. |
| `diff.py` | Compare two spec versions — added / removed / changed operations. |
| `server.py` | CLI parsing, async pre-flight, lifecycle. |

## Why dynamic instead of generated?

Cisco ships a fresh spec every minor release. With 2,000+ operations, hand-generating Python wrappers means a lot of code churn and a lot of subtle drift. Loading the spec at startup means:

- A new vManage version is one folder + one config line.
- The MCP tool descriptions are *always* the spec the server is actually talking to.
- The diff utility tells you what changed at a glance.

Same model as [`netbox-super-cli`](https://github.com/thomaschristory/netbox-super-cli).
