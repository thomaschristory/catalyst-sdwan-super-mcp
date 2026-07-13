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
uv run zensical serve               # docs live preview at http://localhost:8000
```

## What CI enforces

- `ruff check` (lint) and `ruff format --check`
- `mypy --strict` on `sdwan_mcp/`
- `pytest` on Python 3.11, 3.12, 3.13, both Linux and macOS
- Docker build + `--help` smoke test
- `zensical build --strict` on every PR that touches docs (validates links **and** anchors)

## Writing tests that make HTTP calls

**Do not reach for `respx`** — it is not a dependency of this project, and it cannot be
one. respx works by patching `httpx`'s transport internals, and since #99 the client is
[`httpx2`](https://github.com/pydantic/httpx2), which respx cannot see
([respx #317](https://github.com/lundberg/respx/issues/317) is still open).

HTTP is mocked instead with `tests/mocking.py`, a small router over
`httpx2.MockTransport`. It deliberately mirrors the slice of respx's API the suite used,
so existing tests read the same:

```python
async def test_something(dispatcher: Dispatcher, mock_router: MockRouter) -> None:
    with mock_router.scope() as router:                     # assert_all_called=True by default
        route = router.get("https://vm.test:8443/dataservice/devices").mock(
            return_value=httpx2.Response(200, json={"data": []})
        )
        await dispatcher.call("get_device_details_devices", {"site-id": "500"})

    assert route.call_count == 1
    assert route.calls.last.request.url.params["site-id"] == "500"
```

Supported on a route: `.mock(return_value=…)`, `.mock(side_effect=[…])` (consumed in
order; entries may be exceptions such as `httpx2.TimeoutException`), `.respond(status,
json=…, text=…)`, `.side_effect = [...]`, `.called`, `.call_count`, and `.calls` (with
`.last` and indexing, each exposing `.request`).

The one thing that differs from respx, and the thing to remember:

> **The transport is injected, not patched.** respx patched globally, so the client could
> be built before the mock existed. `MockTransport` must be handed to the client at
> construction — which is why `Dispatcher(...)` and `fetcher.make_client(...)` take an
> optional `transport` argument (`None` in production). If your test builds a client, pass
> `transport=mock_router.transport`, or the request will escape to the real network.

The router fails loudly in both directions: an unmatched request raises rather than
hitting the network, and on exit `scope()` asserts every registered route was actually
called (pass `assert_all_called=False` to opt out).

## A note on dependencies

Both `httpx2` **and** `httpx` are installed. That is expected: we use `httpx2`, while
`mcp` (under `fastmcp`) still depends on `httpx`. Application code should always import
`httpx2`.

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
  dispatcher.py     httpx2 client, retry + timeout, param routing
  pagination.py     scroll + offset auto-follow
  tools.py          dynamic MCP tool registration
  diff.py           version diff utility
tests/              pytest suite
  mocking.py        HTTP mock router over httpx2.MockTransport (replaces respx)
docs/               Zensical site (Material theme, mkdocs.yml config)
specs/{version}/    OpenAPI YAML/JSON, one folder per vManage version
.github/workflows/  CI: lint, test, docker, docs, release
```
