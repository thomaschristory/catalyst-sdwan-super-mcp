# catalyst-sdwan-super-mcp

[![lint](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/actions/workflows/lint.yml/badge.svg)](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/actions/workflows/lint.yml)
[![test](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/actions/workflows/test.yml/badge.svg)](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/actions/workflows/test.yml)
[![docs](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/actions/workflows/docs.yml/badge.svg)](https://thomaschristory.github.io/catalyst-sdwan-super-mcp/)
[![PyPI](https://img.shields.io/pypi/v/catalyst-sdwan-super-mcp.svg)](https://pypi.org/project/catalyst-sdwan-super-mcp/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

A [FastMCP](https://gofastmcp.com) server that exposes the **Cisco Catalyst SD-WAN Manager (vManage)** REST API as MCP tools, so any MCP-compatible LLM client (Claude Code, Claude Desktop, Cursor, …) can query and manage your SD-WAN overlay in natural language.

Tools are **generated dynamically from the official OpenAPI specs** — drop in a new spec and the tools rebuild themselves. No per-version Python, no codegen.

📖 **Documentation:** <https://thomaschristory.github.io/catalyst-sdwan-super-mcp/>

---

## Quick start

Run it with [`uvx`](https://docs.astral.sh/uv/) — no clone, no install. The credentials below are Cisco's public, always-on **DevNet sandbox**, so this is a true end-to-end trial without a vManage of your own:

```bash
VMANAGE_HOST=sandbox-sdwan-2.cisco.com \
VMANAGE_USERNAME=devnetuser VMANAGE_PASSWORD='RG!_Yw919_83' \
  VMANAGE_VERIFY_SSL=false uvx catalyst-sdwan-super-mcp
```

That boots the server in **stdio, read-only** mode against `sandbox-sdwan-2.cisco.com` with adaptive tool splitting on. On first run it auto-fetches the vManage **20.18** OpenAPI spec from Cisco DevNet (the default version). `VMANAGE_VERIFY_SSL=false` is needed only because the sandbox uses a self-signed certificate — drop it for a production vManage with a valid cert.

Prefer a persistent install?

```bash
pipx install catalyst-sdwan-super-mcp      # or: uv tool install catalyst-sdwan-super-mcp
sdwan-mcp --help
```

> **Supported vManage versions: 20.15+.** Older releases are out of scope — see [issue #13](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/issues/13).

---

## Add it to your MCP client

Every block below uses the published CLI — no source checkout, no absolute paths to wrangle. The examples target the DevNet sandbox; to use **your own vManage**, swap in `VMANAGE_HOST` (and friends) as shown in [Point at your own vManage](#point-at-your-own-vmanage) below.

### Claude Code

One command:

```bash
claude mcp add sdwan \
  -e VMANAGE_HOST=sandbox-sdwan-2.cisco.com \
  -e VMANAGE_USERNAME=devnetuser \
  -e VMANAGE_PASSWORD='RG!_Yw919_83' \
  -e VMANAGE_VERIFY_SSL=false \
  -- uvx catalyst-sdwan-super-mcp
```

…or commit a project-local `.mcp.json`:

```json
{
  "mcpServers": {
    "sdwan": {
      "command": "uvx",
      "args": ["catalyst-sdwan-super-mcp"],
      "env": {
        "VMANAGE_HOST": "sandbox-sdwan-2.cisco.com",
        "VMANAGE_USERNAME": "devnetuser",
        "VMANAGE_PASSWORD": "RG!_Yw919_83",
        "VMANAGE_VERIFY_SSL": "false"
      }
    }
  }
}
```

### Claude Desktop

Edit `claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/`, Windows: `%APPDATA%\Claude\`), then restart Claude Desktop — `sdwan` shows up in the MCP indicator:

```json
{
  "mcpServers": {
    "sdwan": {
      "command": "uvx",
      "args": ["catalyst-sdwan-super-mcp"],
      "env": {
        "VMANAGE_HOST": "sandbox-sdwan-2.cisco.com",
        "VMANAGE_USERNAME": "devnetuser",
        "VMANAGE_PASSWORD": "RG!_Yw919_83",
        "VMANAGE_VERIFY_SSL": "false"
      }
    }
  }
}
```

### Cursor

Add to `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (per project):

```json
{
  "mcpServers": {
    "sdwan": {
      "command": "uvx",
      "args": ["catalyst-sdwan-super-mcp"],
      "env": {
        "VMANAGE_HOST": "sandbox-sdwan-2.cisco.com",
        "VMANAGE_USERNAME": "devnetuser",
        "VMANAGE_PASSWORD": "RG!_Yw919_83",
        "VMANAGE_VERIFY_SSL": "false"
      }
    }
  }
}
```

### Any other stdio client (Cline, Continue, Windsurf, Zed, …)

Same shape everywhere: point the client at the `uvx catalyst-sdwan-super-mcp` command with `VMANAGE_*` in the environment.

> **Tips**
> - **`VMANAGE_VERIFY_SSL=false`** is included because the DevNet sandbox uses a self-signed cert. Drop it (or set `true`) when pointing at a production vManage with a valid certificate.
> - **Installed the package?** Replace `"command": "uvx", "args": ["catalyst-sdwan-super-mcp"]` with `"command": "sdwan-mcp", "args": []`.
> - **Need writes?** Add `"--read-write"` to `args` (off by default — see below).
> - **Network transport / bearer auth?** SSE and streamable-HTTP setups live in [docs/guides/mcp-clients.md](https://thomaschristory.github.io/catalyst-sdwan-super-mcp/guides/mcp-clients/).

### Point at your own vManage

The host defaults to the DevNet sandbox. To target your own controller, set
`VMANAGE_HOST` (and `VMANAGE_PORT` if it isn't `443`). With a valid TLS
certificate, leave SSL verification on — i.e. drop `VMANAGE_VERIFY_SSL` entirely
or set it to `true`:

```bash
VMANAGE_HOST=vmanage.example.com \
VMANAGE_PORT=443 \
VMANAGE_USERNAME=your-user \
VMANAGE_PASSWORD='your-pass' \
  uvx catalyst-sdwan-super-mcp
```

The same keys go in any client's `env` block — e.g. for `.mcp.json`:

```json
{
  "mcpServers": {
    "sdwan": {
      "command": "uvx",
      "args": ["catalyst-sdwan-super-mcp"],
      "env": {
        "VMANAGE_HOST": "vmanage.example.com",
        "VMANAGE_PORT": "443",
        "VMANAGE_USERNAME": "your-user",
        "VMANAGE_PASSWORD": "your-pass"
      }
    }
  }
}
```

`VMANAGE_USE_JWT` (default `true`; set `false` for the legacy session login) and
`VMANAGE_TIMEOUT` round out the connection settings. Full reference, including the
optional `sdwan-mcp.yaml` equivalents and `${ENV}` interpolation, in the
[configuration docs](https://thomaschristory.github.io/catalyst-sdwan-super-mcp/reference/configuration/).

---

## What you get

- **Adaptive tool splitting.** A size-driven splitter (`max_actions_per_tool`, default 150) chops huge OpenAPI sections into right-sized tools — 360 tools on 20.18 RW out of the box, all under the cap. See [docs/guides/tool-splitting.md](docs/guides/tool-splitting.md).
- **Read-only by default.** `--read-write` registers POST/PUT/DELETE/PATCH explicitly. Write tools are never even put in the LLM's context in RO mode.
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

## Develop / hack on it

The PyPI package ships the server only; clone the repo if you want the bundled specs (20.15 / 20.16 / 20.18), the test suite, or the docs site:

```bash
git clone https://github.com/thomaschristory/catalyst-sdwan-super-mcp.git
cd catalyst-sdwan-super-mcp
uv sync --group dev --group docs
uv run sdwan-mcp --help
uv run pytest
```

Contribution guidelines, the release process, and the security posture for fork PRs live in [docs/contributing/](docs/contributing/development.md).

---

## Status

Actively maintained and published on [PyPI](https://pypi.org/project/catalyst-sdwan-super-mcp/). Pre-1.0 — under [semver](https://semver.org/) that means minor releases may change behavior, so pin a version in production. What's solid today:

- **Read-only by default** — mutations require an explicit `--read-write`, the recommended starting posture.
- Dynamic tool generation from the official vManage OpenAPI specs (20.15 / 20.16 / 20.18), with on-demand auto-fetch for newer versions.
- JWT **and** session auth to vManage; stdio, SSE, and streamable-HTTP transports, with bearer-token auth on the HTTP ones.
- Response pagination, configurable retry/timeout, and an opt-in [debug capture](docs/reference/configuration.md) of the upstream exchange for diagnosing opaque vManage errors.

Releases are tagged on PyPI and recorded in [CHANGELOG.md](CHANGELOG.md); open work is on the [issue tracker](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/issues). Contributions welcome.

License: Apache 2.0.
