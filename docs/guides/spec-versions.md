# Spec versions and diffing

## Layout

```
specs/
  20.15/
    vmanageapi_2015.yaml     # one file or many — loader globs *.yaml *.yml *.json
  20.16/
    vmanageapi_2016.yaml
  20.18/
    vmanageapi_2018.yaml
```

The folder name is the version key. `config.yaml`'s `sdwan.active_version` picks which one to load.

## Adding a new version (auto-fetch)

Starting in 0.1.1, just bump `active_version` and start the server. If
`specs/<version>/` is empty or missing, the loader fetches and stitches the
spec from [developer.cisco.com](https://developer.cisco.com/docs/sdwan/) before
registering tools. The next start reuses the cached YAML.

```yaml
sdwan:
  active_version: "20.19"
  auto_fetch: true            # default; set false for air-gapped deployments
```

For predictable behaviour (CI, Docker image bake, air-gapped pre-warm), run an
explicit fetch first:

```bash
uv run sdwan-mcp fetch --version 20.19           # writes specs/20.19/vmanageapi_2019.yaml
uv run sdwan-mcp fetch --version 20.19 --force   # re-fetch even if cached
uv run sdwan-mcp fetch --all-known               # pre-warm every known version
uv run sdwan-mcp list-versions                   # show what's known + cached locally
```

The explicit `fetch` subcommand additionally caches each downloaded fragment
JSON under `~/.cache/sdwan-mcp/fragments/<version>/`, so a `--force` re-fetch
reuses unchanged fragments instead of re-pulling thousands of files. Pass
`--no-fragment-cache` to disable that cache.

## Manual download (legacy / pre-20.16)

For versions older than 20.16 (where Cisco still publishes a monolithic
OpenAPI document), download manually:

1. Get the OpenAPI document from
   [Cisco DevNet](https://developer.cisco.com/docs/search/?products=Catalyst%20SD-WAN).
2. Drop the file(s) into `specs/{version}/`.
3. Run a diff so you know what changed:

    ```bash
    uv run sdwan-mcp --diff 20.15 20.18
    ```

4. Update `config.yaml`:

    ```yaml
    sdwan:
      active_version: "20.18"
    ```

5. Restart the server. Done.

## Diff output

```text
=== SD-WAN API Diff: 20.15 → 20.18 ===

REMOVED (3 operations — potentially breaking):
  - getVedgeList  [Monitoring - Device Details]  GET /device/vedge
  ...

ADDED (217 new operations):
  + getDeviceById  [Monitoring - Device Details]  GET /device/{deviceId}
  ...

CHANGED (42 operations with parameter drift):
  ~ listAllDevices  [Monitoring - Device Details]
      added: 'includeTenantvSmart' — query, boolean, optional
```

## How auto-fetch works (>=20.16)

For 20.16 and newer, Cisco publishes specs as thousands of per-operation and
per-schema JSON fragments under UUID directories on
`pubhub.devnetcloud.com`. The fetcher:

1. Pulls the DevNet landing page for the version and extracts every
   `content:"./<uuid>/(apis|models)/<rest>"` reference from the inline
   `webJson` literal.
2. Downloads each fragment concurrently (bounded at 10 in-flight) with
   exponential backoff on transient HTTP failures.
3. Stitches the fragments into a single OpenAPI 3.1 document — paths come
   from each operation's own `spec.path`/`spec.method` fields, schemas keyed
   by fragment filename. `$ref` strings are already in
   `#/components/schemas/<name>` form, so no rewriting is needed.
4. Validates the result (>=100 paths, non-empty `components.schemas`, no
   unresolved schema `$ref`s, >=1 MB) and atomically writes
   `specs/<version>/vmanageapi_<flat>.yaml`.

Unresolved `#/components/examples/...` refs are tolerated as warnings —
Cisco does not publish example fragments.

See [the loader docs](tool-splitting.md) for what happens next.
