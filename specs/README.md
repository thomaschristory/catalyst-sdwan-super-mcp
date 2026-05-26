# specs/

OpenAPI documents for the Cisco Catalyst SD-WAN Manager (vManage) API, one folder
per release.

## Layout

```
specs/
  20.15/
    vmanageapi_2015.yaml    # single monolithic document — works fine
  20.16/
    vmanageapi_2016.yaml
  20.18/
    vmanageapi_2018.yaml
```

Inside a version folder the loader globs `*.yaml`, `*.yml`, and `*.json`, then
merges them. Filenames are arbitrary; the loader accepts both YAML and JSON.

## Bundled versions

| Version | Source | Format | Pulled | Notes |
|---|---|---|---|---|
| `20.15/vmanageapi_2015.yaml` | [`pubhub.devnetcloud.com/.../vmanageapi_2015.json`](https://pubhub.devnetcloud.com/media/sd-wan-api-docs-20-15/docs/openapi/vmanageapi_2015.json) | YAML (Cisco serves a `.json` URL whose body is actually YAML — saved with the correct `.yaml` extension here) | 2026-05-23 | 3,815 RW ops, 347 tags. |
| `20.16/vmanageapi_2016.yaml` | [`pubhub.devnetcloud.com/.../vmanageapi_2016.yaml`](https://pubhub.devnetcloud.com/media/sd-wan-api-20-16/docs/openapi/vmanageapi_2016.yaml) | YAML | 2026-05-23 | 3,923 RW ops, 353 tags. |
| `20.18/vmanageapi_2018.yaml` | [`pubhub.devnetcloud.com/.../vmanageapi_2018.yaml`](https://pubhub.devnetcloud.com/media/cisco-catalyst-sd-wan-api-guide-20-18/docs/openapi/vmanageapi_2018.yaml) | YAML | 2026-05-23 | 4,102 RW ops, 375 tags. Default `active_version`. |

Pre-20.15 specs are no longer supported — the API surface below that point is
unstable enough that the loader's stable-action-name derivation cannot be
guaranteed. See issue [#13](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/issues/13)
for the analysis.

## Adding a new version

1. Download the OpenAPI document(s) for the version you want.

   - DevNet URL pattern: `sd-wan-api-{XX-YY}/docs/openapi/vmanageapi_{XXYY}.yaml`
     (occasionally `.json`). **Verify before downloading** — Cisco changes the
     layout from time to time.

2. Drop the file(s) into `specs/{version}/`. YAML, YML, and JSON are all
   accepted. If the file's contents don't match its extension (as on 20.15),
   rename it to match — the loader picks a parser by extension.
3. Diff against the previous version:

   ```bash
   uv run sdwan-mcp --diff 20.15 {NEW_VERSION}
   ```

4. Update `sdwan-mcp.yaml`:

   ```yaml
   sdwan:
     active_version: "{NEW_VERSION}"
   ```

## Auto-fetching

Tracked as issue [#1](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/issues/1).
The recommended approach is documented in that issue.

## License

The OpenAPI documents are © Cisco Systems. They are bundled here for convenience
under the standard fair-use terms of the Cisco DevNet platform — the canonical
copy lives at <https://developer.cisco.com/docs/search/?products=Catalyst%20SD-WAN>.
