# catalyst-sdwan-super-mcp

A [FastMCP](https://gofastmcp.com) server that exposes the **Cisco Catalyst SD-WAN Manager (vManage)** REST API as MCP tools, so any MCP-compatible LLM client (Claude Desktop, Claude Code, Cursor, …) can query and manage your SD-WAN overlay.

Tools are generated dynamically from the official OpenAPI specs — drop in a new spec, the tools rebuild themselves. No code changes per vManage version.

---

## At a glance

- **Dynamic** — no codegen step, no per-version Python file.
- **One tool per Cisco section** (default) — ~65 tools, LLM-friendly. Optional `tag` granularity gives ~375 tools for fine-grained control.
- **Read-only by default** — explicit `--read-write` flag for POST/PUT/DELETE/PATCH.
- **Two auth modes** — modern JWT (vManage 20.18.1+) and legacy session (older).
- **Three transports** — stdio (Claude Desktop), SSE, streamable-HTTP.
- **Version diff** — `sdwan-mcp --diff 20.15 20.18` shows added/removed/changed operations before upgrading.

---

## What's the “super” for?

The same pattern as [`netbox-super-cli`](https://github.com/thomaschristory/netbox-super-cli): instead of hand-writing a tool per endpoint (the API has 2,000+), we **derive everything from the upstream spec**. Cisco evolves vManage; you drop the new spec in `specs/{version}/` and the MCP tools rebuild themselves.

---

## Continue reading

- [Install](getting-started/install.md)
- [First run](getting-started/first-run.md)
- [DevNet sandbox](getting-started/sandbox.md) — the easiest way to try it without a vManage of your own
- [Architecture overview](architecture/overview.md)
