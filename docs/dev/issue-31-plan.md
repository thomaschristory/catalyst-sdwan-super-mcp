# Issue #31 — Spec fetcher plan (working notes)

Status: implementation in flight. This file is a developer-facing plan, not user docs.

## Verified facts (live-checked against DevNet 20.18 on 2026-05-24)

- DevNet landing page: `https://developer.cisco.com/docs/sdwan/{slug}/` where `slug = version.replace('.', '-')` (e.g. `20-18`).
- The page is an SPA. All fragment URLs live in an inline JS object literal assigned to `webJson`. Object keys are unquoted (`webJson:{config:{...},items:[...]}`), so it is JavaScript, not JSON.
- We do **not** need to parse the JS literal in full. Every fragment leaf appears as a `content:"./<uuid>/(apis|models)/<rest>"` string, and a simple regex over the raw HTML yields the full set deterministically.
- Pubhub base URL is `https://pubhub.devnetcloud.com/media/cisco-catalyst-sd-wan-api-guide-{slug}/docs/<uuid>/<rest>`. The issue's draft used `sd-wan-api-docs-{slug}`, which 404s. The correct prefix is the one used by the page's own image refs: see `grep pubhub` in the page HTML.
- Fragment shape (verified):
  ```json
  {
    "type": "api" | "model",
    "title": "...",
    "meta": {
      "id": "...",
      "info": { "title": "...", "description": "...", "version": "20.18 - 2025-08-15" },
      "openapi": "3.1.0",
      "servers": [{"url": "/dataservice"}]
    },
    "spec": { ... }
  }
  ```
- For `type=api`: `spec` contains the OpenAPI operation object PLUS `method` and `path` keys (e.g. `method: get`, `path: /v1/feature-profile/sdwan/service/{serviceId}/appqoe`). We strip those two before placing the rest at `paths[path][method]`.
- For `type=model`: `spec` is the JSON-schema object. The schema name is the file basename (no extension), e.g. `CommonCommonDefs_booleanDef.json` → `components.schemas["CommonCommonDefs_booleanDef"]`.
- `$ref` strings inside fragments are already in standard `#/components/schemas/<Name>` form. **No ref rewriting required**, so the issue's "ref rewrite" step collapses to a no-op.
- Some operations reference `#/components/examples/<name>` — there are no example fragments published. Validation must treat unresolved `examples` refs as a warning, not an error.
- Counts on 20.18: 4103 ops, 9654 models, 13 sections (UUIDs).

## Module layout

```
sdwan_mcp/fetcher/
  __init__.py     # public: fetch_version(version, *, force=False, use_cache=True) -> Path
                  #         KNOWN_VERSIONS constant
                  #         list_known_versions() -> list[VersionInfo]
  discover.py     # parse_discovery_html(html, slug) -> Discovery
                  #   - Discovery.api_fragments: list[FragmentRef]
                  #   - Discovery.model_fragments: list[FragmentRef]
                  #   - FragmentRef = (url, uuid, rel_path)
  stitch.py       # stitch(version, op_fragments, model_fragments) -> dict (OpenAPI doc)
  validate.py     # validate(doc) -> None  (raises FetcherValidationError on failure)
                  # Rules: >=100 paths, schemas non-empty, no unresolved $ref into
                  # components.schemas, top-level `openapi` key, total YAML >= 1 MB.
                  # Unresolved refs into components.examples = warning only.
  fetch.py        # internals: HTTP client, bounded-concurrency fetch, retry, cache
```

## Public entrypoint

```python
async def fetch_version(
    version: str,
    *,
    specs_dir: Path | None = None,   # default: ./specs from config
    force: bool = False,             # ignore on-disk YAML; rebuild
    use_cache: bool = True,          # fragment disk cache (~/.cache/sdwan-mcp/fragments/{V}/)
    concurrency: int = 10,
    timeout: float = 60.0,
    verify_ssl: bool = True,
) -> Path
```

- Implicit path (auto-fetch on missing spec): `use_cache=False`, `force=False`.
- Explicit `fetch` subcommand: `use_cache=True`.

## CLI surface

We keep the existing flat argparse and add a thin sub-command router. Approach: if `sys.argv[1]` is one of `{fetch, list-versions}`, route to a dedicated parser; otherwise fall through to the existing flat parser. This preserves backward compatibility with every documented flag (`--version`, `--diff`, `--read-write`, ...) without re-homing them under a `serve` sub-command.

```bash
sdwan-mcp fetch --version 20.19
sdwan-mcp fetch --version 20.19 --force
sdwan-mcp fetch --all-known
sdwan-mcp list-versions
```

## Auto-fetch hook

In `server._connect_and_register()`, wrap `SpecLoader(...)` in a try/except for `FileNotFoundError`. If `config.sdwan.auto_fetch` is true (default), `await fetch_version(...)` then retry `SpecLoader(...)`. If false, re-raise with a hint pointing at the explicit `fetch` subcommand.

## Config additions

```yaml
sdwan:
  auto_fetch: true                  # default; set false to require explicit fetch
```

`SDWANConfig.auto_fetch: bool = True`.

## Tests

- `tests/fetcher_fixtures/devnet_minimal.html` — a hand-crafted HTML containing a stripped `webJson` literal with 4 op leaves + 3 model leaves across 2 UUIDs. Used by discover tests.
- `tests/fetcher_fixtures/op_fragment.json`, `model_fragment.json` — verbatim copies of real `spec=...` objects (one op, one model).
- `tests/test_fetcher_discover.py` — parses the fixture, asserts the exact fragment URL set, plus rejects garbage.
- `tests/test_fetcher_stitch.py` — hand-built list of fragments → assert merged doc shape (`paths`, `components.schemas`, tags). Verifies stripping of `method`/`path` from `spec`.
- `tests/test_fetcher_validate.py` — passing case and one failing case per rule.
- `tests/test_fetcher_fetch.py` — respx-mocked end-to-end: discovery HTML → ~5 fragments → stitched YAML written to a tmp specs dir. Confirms SpecLoader can load the result.
- `tests/test_fetcher_integration.py` — gated by `RUN_LIVE_FETCH=1`; pulls real 20.18 end to end, compares path count to the bundled spec. Skipped in normal CI.

## Risks (and current mitigation)

- DevNet SPA shape changes → discover.py logs every parsed leaf at DEBUG and raises a clear error if zero `apis/` leaves are found. validate.py refuses to write garbage.
- The pubhub URL prefix moves again → discover.py extracts the prefix from the page (already present in image refs), so a hard-coded prefix is a fallback only.
- Dangling `examples` refs → validate.py warns rather than failing. If Cisco ever publishes example fragments, we will pick them up by extending the discovery regex to `(apis|models|examples)`.
- Implicit fetch slowness → 300s overall wall-clock deadline (`fetch_version_safe(overall_timeout=300.0)`); clear `FetchError` on timeout points at the explicit `sdwan-mcp fetch` subcommand.

## Out of scope (per issue Non-goals)

- No drift detection / cron / GitHub Action.
- No automation for <=20.15.
- No hot-reload at runtime.
