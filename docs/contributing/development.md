# Development

## Setup

```bash
git clone https://github.com/thomaschristory/catalyst-sdwan-super-mcp.git
cd catalyst-sdwan-super-mcp
uv sync --group dev --group docs
```

## Day-to-day

```bash
uv run pytest -v                    # run the test suite
uv run ruff check sdwan_mcp tests   # lint
uv run ruff format sdwan_mcp tests  # format
uv run mkdocs serve                 # docs live preview at http://localhost:8000
```

## What CI enforces

- `ruff check` (lint) and `ruff format --check`
- `mypy --strict` on `sdwan_mcp/`
- `pytest` on Python 3.11, 3.12, 3.13, both Linux and macOS
- Docker build + `--help` smoke test
- `mkdocs build --strict` on every PR that touches docs

## Project layout

```
sdwan_mcp/          source package
  __init__.py       version
  server.py         entrypoint, CLI, subcommands (fetch, list-versions)
  config.py         YAML + env interpolation
  loader.py         spec loading, grouping, indexing
  fetcher/          live spec ingestion from developer.cisco.com (>= 20.16)
  auth.py           JWT + session login to vManage
  transport_auth.py bearer-token middleware for SSE / streamable-HTTP
  dispatcher.py     httpx client, retry + timeout, param routing
  pagination.py     scroll + offset auto-follow
  tools.py          dynamic MCP tool registration
  diff.py           version diff utility
tests/              pytest suite
docs/               mkdocs-material site
specs/{version}/    OpenAPI YAML/JSON, one folder per vManage version
.github/workflows/  CI: lint, test, docker, docs, release
```
