# Install

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
  use_jwt: true               # set to false for vManage < 20.18.1

sdwan:
  specs_dir: ./specs
  active_version: "20.18"     # must match a folder in specs/
  tag_granularity: section    # "section" or "tag"
```

## Get the OpenAPI specs

Cisco publishes vManage OpenAPI specs on DevNet. The 20.10 spec is shipped with this repo (`specs/20.10/`) because it matches the public sandbox. For other versions:

```bash
# 20.10 is already bundled
ls specs/20.10/

# For other versions, see docs/guides/spec-versions.md
```

Tracking and auto-downloading newer versions is [issue #1](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/issues/1).

## Verify

```bash
uv run sdwan-mcp --help
uv run sdwan-mcp --diff 20.10 20.10    # sanity check — no diff with itself
```
