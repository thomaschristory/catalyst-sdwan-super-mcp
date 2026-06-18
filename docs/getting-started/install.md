# Install

**Supports vManage 20.15+.** Older releases are out of scope.

## Run it without installing (recommended)

The fastest path is [`uvx`](https://docs.astral.sh/uv/), which fetches and runs the
published package in one step — nothing to install or clean up:

```bash
uvx catalyst-sdwan-super-mcp --help
```

This is also the form used in the [MCP client config](../guides/mcp-clients.md)
blocks, so your editor launches the server the same way.

## Install persistently

For a long-lived CLI on your `PATH`:

```bash
uv tool install catalyst-sdwan-super-mcp
# or
pipx install catalyst-sdwan-super-mcp
# or
pip install catalyst-sdwan-super-mcp

sdwan-mcp --help
```

Whichever you pick, the PyPI distribution ships the package only. No specs are bundled — on first startup the loader auto-fetches the spec for `sdwan.active_version` (>= 20.16) from `developer.cisco.com` into `sdwan.specs_dir`. Override this behaviour with `sdwan.auto_fetch: false` (air-gapped) or pre-warm with `sdwan-mcp fetch --version <V>`. See [Spec versions](../guides/spec-versions.md) for details.

## From source (for development or to get the bundled specs)

```bash
git clone https://github.com/thomaschristory/catalyst-sdwan-super-mcp.git
cd catalyst-sdwan-super-mcp

# Using uv (fastest)
uv sync
uv run sdwan-mcp --help

# Or plain pip
pip install -e .
sdwan-mcp --help
```

## Configure credentials

```bash
cp .env.example .env
$EDITOR .env       # set VMANAGE_USERNAME and VMANAGE_PASSWORD
```

The `.env` file is loaded automatically at startup. Never commit it.

## Configure your vManage

The host defaults to the DevNet sandbox (`sandbox-sdwan-2.cisco.com`). To point at
your own controller, set the connection settings — either as environment variables
(the natural choice for a `uvx` / installed-CLI launch with no config file) or in
`sdwan-mcp.yaml`.

Environment variables:

```bash
export VMANAGE_HOST=vmanage.example.com   # your vManage hostname
export VMANAGE_PORT=443                    # or 8443, etc.
export VMANAGE_USERNAME=your-user
export VMANAGE_PASSWORD='your-pass'
# VMANAGE_VERIFY_SSL=true is the default (valid cert) — set false only for self-signed
# VMANAGE_USE_JWT=true is the default — set false to force the legacy JSESSIONID + XSRF login
# VMANAGE_TIMEOUT overrides the request timeout (seconds)
```

Or `sdwan-mcp.yaml`:

```yaml
vmanage:
  host: vmanage.example.com   # your vManage hostname
  port: 8443                  # or 443 in front of a load balancer
  verify_ssl: true            # set to false for self-signed
  use_jwt: true               # set to false to force JSESSIONID + XSRF fallback

sdwan:
  specs_dir: ./specs
  active_version: "20.18"        # must match a folder in specs/
  max_actions_per_tool: 150      # default; 0 disables splitting (see guides/tool-splitting.md)
```

CLI flags > env vars > YAML > defaults. See the [configuration reference](../reference/configuration.md) for the full table.

## Get the OpenAPI specs

Cisco publishes vManage OpenAPI specs on DevNet. Three versions are bundled with this repo:

```bash
ls specs/
# 20.15  20.16  20.18  README.md
```

`20.18` is the default and matches the public DevNet sandbox.

For other versions (>= 20.16) the loader can fetch on demand — bump `sdwan.active_version` and run the server, or pre-warm explicitly:

```bash
sdwan-mcp list-versions                  # what's known and what's cached locally
sdwan-mcp fetch --version 20.19          # download + stitch into specs/20.19/
```

See [Spec versions](../guides/spec-versions.md) for the full flow, and `specs/README.md` for the source URLs of the bundled versions.

## Verify

```bash
uv run sdwan-mcp --help
uv run sdwan-mcp --diff 20.15 20.18    # see what changed between bundled versions
```
