# Spec versions and diffing

## Layout

```
specs/
  20.10/
    vmanageapi_2010.json     # one file or many — loader globs *.yaml *.yml *.json
  20.15/
    ...
  20.18/
    ...
```

The folder name is the version key. `config.yaml`'s `sdwan.active_version` picks which one to load.

## Adding a new version

1. Download the OpenAPI document(s) from [Cisco DevNet](https://developer.cisco.com/docs/search/?products=Catalyst%20SD-WAN).
2. Drop the file(s) into `specs/{version}/`.
3. Run a diff so you know what changed:

    ```bash
    uv run sdwan-mcp --diff 20.10 20.18
    ```

4. Update `config.yaml`:

    ```yaml
    sdwan:
      active_version: "20.18"
    ```

5. Restart the server. Done.

## Diff output

```text
=== SD-WAN API Diff: 20.10 → 20.18 ===

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

## Auto-fetching specs

Manually downloading is a chore. Tracked as [issue #1](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/issues/1). Recommended approach is documented there.
