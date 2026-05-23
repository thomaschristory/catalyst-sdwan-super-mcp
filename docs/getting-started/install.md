# Install

**Supports vManage 20.15+.** Older releases are out of scope.

## From source (recommended for now — we're pre-PyPI)

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
  active_version: "20.18"     # must match a folder in specs/
  tag_granularity: section    # "section" or "tag"
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
