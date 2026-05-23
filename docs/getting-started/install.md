# Install

**Supports vManage 20.15+.** Older releases are out of scope.

## From PyPI (recommended)

```bash
uv tool install catalyst-sdwan-super-mcp
sdwan-mcp --help
```

Or with pip / pipx:

```bash
pipx install catalyst-sdwan-super-mcp
# or
pip install catalyst-sdwan-super-mcp
```

The PyPI install ships the package only — the OpenAPI specs live in this repo under `specs/`. Either clone the repo for the bundled specs, or point `sdwan.specs_dir` in `config.yaml` at your own copy.

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

Edit `config.yaml`:

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

## Get the OpenAPI specs

Cisco publishes vManage OpenAPI specs on DevNet. Three versions are bundled with this repo:

```bash
ls specs/
# 20.15  20.16  20.18  README.md
```

`20.18` is the default and matches the public DevNet sandbox. See `specs/README.md` for the source URLs and how to add another version.

Tracking and auto-downloading newer versions is [issue #1](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/issues/1).

## Verify

```bash
uv run sdwan-mcp --help
uv run sdwan-mcp --diff 20.15 20.18    # see what changed between bundled versions
```
