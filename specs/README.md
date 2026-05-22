# specs/

OpenAPI documents for the Cisco Catalyst SD-WAN Manager (vManage) API, one folder
per release.

## Layout

```
specs/
  20.10/
    vmanageapi_2010.json    # single monolithic document — works fine
  20.15/
    monitoring.yaml         # or split into multiple files — also fine
    configuration.yaml
    ...
```

Inside a version folder the loader globs `*.yaml`, `*.yml`, and `*.json`, then
merges them. Filenames are arbitrary.

## Bundled versions

| Version | Source | Notes |
|---|---|---|
| `20.10/vmanageapi_2010.json` | [Cisco DevNet — SD-WAN 20.10](https://developer.cisco.com/docs/sdwan/20-10/) | Matches the always-on DevNet sandbox. 2,230 paths, 2,983 operations, 304 tags. |

## Adding a new version

1. Download the OpenAPI document(s) for the version you want.

   - For 20.10 we pull `https://pubhub.devnetcloud.com/media/sd-wan-api-docs-20-10/docs/openapi/vmanageapi_2010.json`.
   - The DevNet URL pattern for other versions tends to follow `sd-wan-api-docs-{XX-YY}/docs/openapi/vmanageapi_{XXYY}.json`, but **verify this first** — Cisco occasionally changes the layout.

2. Drop the file(s) into `specs/{version}/`.
3. Diff against the previous version:

   ```bash
   uv run sdwan-mcp --diff 20.10 {NEW_VERSION}
   ```

4. Update `config.yaml`:

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
