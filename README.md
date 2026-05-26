# catalyst-sdwan-super-mcp

[![lint](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/actions/workflows/lint.yml/badge.svg)](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/actions/workflows/lint.yml)
[![test](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/actions/workflows/test.yml/badge.svg)](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/actions/workflows/test.yml)
[![docs](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/actions/workflows/docs.yml/badge.svg)](https://thomaschristory.github.io/catalyst-sdwan-super-mcp/)
[![PyPI](https://img.shields.io/pypi/v/catalyst-sdwan-super-mcp.svg)](https://pypi.org/project/catalyst-sdwan-super-mcp/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

A [FastMCP](https://gofastmcp.com) server that exposes the **Cisco Catalyst SD-WAN Manager (vManage)** REST API as MCP tools, so any MCP-compatible LLM client (Claude Desktop, Claude Code, Cursor, …) can query and manage your SD-WAN overlay.

Tools are **generated dynamically from the official OpenAPI specs** — drop in a new spec, the tools rebuild themselves. No per-version Python.

**Documentation:** <https://thomaschristory.github.io/catalyst-sdwan-super-mcp/>

---

## Try it in 60 seconds against the Cisco DevNet sandbox

```bash
git clone https://github.com/thomaschristory/catalyst-sdwan-super-mcp.git
cd catalyst-sdwan-super-mcp
uv sync

# Credentials for Cisco's public always-on SD-WAN sandbox
cat > .env <<'EOF'
VMANAGE_USERNAME=devnetuser
VMANAGE_PASSWORD=RG!_Yw919_83
EOF

uv run sdwan-mcp        # stdio, read-only, adaptive tool splitting (default)
```

The shipped `sdwan-mcp.yaml` points at `sandbox-sdwan-2.cisco.com` and ships specs for vManage 20.15, 20.16, and 20.18 in `specs/`. 20.18 is the default. You don't need a vManage of your own to try it.

**Supported vManage versions: 20.15+.** Older releases are out of scope — see [issue #13](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/issues/13).

### Install from PyPI

```bash
uv tool install catalyst-sdwan-super-mcp
sdwan-mcp --help
```

The PyPI package ships the server only — no bundled specs. On first run the loader auto-fetches the spec for `sdwan.active_version` from `developer.cisco.com` and writes it under `sdwan.specs_dir` (set `sdwan.auto_fetch: false` to opt out, e.g. for air-gapped deployments). For predictable behaviour you can pre-warm with `sdwan-mcp fetch --version 20.18`. Full instructions: [docs/getting-started/install.md](docs/getting-started/install.md).

---

## What you get

- **Adaptive tool splitting.** A size-driven splitter (`max_actions_per_tool`, default 150) chops huge OpenAPI sections into right-sized tools — 360 tools on 20.18 RW out of the box, all under the cap. See [docs/guides/tool-splitting.md](docs/guides/tool-splitting.md).
- **Read-only by default.** `--read-write` registers POST/PUT/DELETE/PATCH explicitly.
- **Two auth modes to vManage:** JWT (vManage 20.18.1+) and JSESSIONID + XSRF (older).
- **Three transports:** stdio, SSE, streamable-HTTP. The HTTP transports ship with first-class **bearer-token auth** (`transport.auth.type: bearer`) and auto-demote non-loopback binds to `127.0.0.1` when no auth is configured. See [docs/guides/mcp-clients.md](docs/guides/mcp-clients.md).
- **Response pagination** for bulk endpoints. The dispatcher auto-follows scroll and offset endpoints up to a configurable cap and returns a stitched payload with a resumable cursor. See [docs/guides/pagination.md](docs/guides/pagination.md).
- **Configurable retry + timeout** on the httpx client. Transient `5xx` and connection errors retry with exponential backoff + jitter; mutating verbs are skipped by default. See [docs/reference/configuration.md](docs/reference/configuration.md).
- **Auto-fetch specs.** Bump `sdwan.active_version` and the loader pulls the matching spec from `developer.cisco.com` on startup. Pre-warm explicitly with `sdwan-mcp fetch --version <V>` or list known versions with `sdwan-mcp list-versions`. See [docs/guides/spec-versions.md](docs/guides/spec-versions.md).
- **Version diff:** `sdwan-mcp --diff 20.15 20.18` shows added/removed/changed operations before upgrade.
- **Docker:** multi-stage image, specs mounted as a volume so versions ship without rebuilding.

---

## Project layout

```
sdwan_mcp/          source package
  server.py         entrypoint, CLI, subcommands (fetch, list-versions)
  config.py         YAML + env interpolation
  loader.py         spec loading, adaptive splitting, indexing
  auth.py           JWT + session login to vManage
  transport_auth.py bearer-token middleware for SSE / streamable-HTTP
  dispatcher.py     httpx client, retry + timeout, param routing
  pagination.py     scroll + offset auto-follow
  fetcher/          live spec ingestion from developer.cisco.com (20.16+)
  tools.py          dynamic MCP tool registration
  diff.py           version diff utility
tests/              pytest suite (respx for HTTP)
docs/               mkdocs-material site, deployed to GitHub Pages
specs/{version}/    OpenAPI YAML/JSON, one folder per vManage version
.github/workflows/  lint, test, docker, docs, release
```

---

## Architecture quick look

See [docs/architecture/overview.md](docs/architecture/overview.md). At a glance:

```
LLM ──(MCP)──► FastMCP ──► tools.py ──► dispatcher.py ──► httpx ──► vManage
                  ▲                           │
                  │           auth.py ◄───────┘
              loader.py
                  ▲
              specs/{version}/*.{yaml,json}
```

---

## Status

Pre-1.0. Read-only is the safe default and the recommended starting posture; `--read-write` opt-in is exercised against the DevNet sandbox. Released versions are tagged on [PyPI](https://pypi.org/project/catalyst-sdwan-super-mcp/) and tracked in [CHANGELOG.md](CHANGELOG.md). Open work is on the [issue tracker](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/issues).

License: Apache 2.0.
