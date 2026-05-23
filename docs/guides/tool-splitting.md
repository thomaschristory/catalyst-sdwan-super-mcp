# Tool splitting

The vManage OpenAPI spec exposes thousands of operations. Registering all of them on a single MCP tool would produce a description payload of hundreds of kilobytes — too large for most clients to ingest comfortably. The loader instead splits operations into smaller tools using a size-driven adaptive algorithm.

## The knob: `max_actions_per_tool`

```yaml
sdwan:
  max_actions_per_tool: 150   # default
```

Or on the CLI:

```bash
uv run sdwan-mcp --max-actions-per-tool 150
```

A tool may hold at most this many actions before the loader tries to split it further. Set to `0` to disable splitting entirely (one tool per section, regardless of size).

The default of **150** keeps every tool well under the typical client per-tool description budget on 20.15, 20.16, and 20.18 — see the [counts table](#actual-tool-counts) below.

## The algorithm

Three steps, applied per section (`Configuration`, `Monitoring`, …) in order:

1. **Section.** If the section has `≤ max_actions_per_tool` operations, emit it as a single tool. Done.
2. **Sub-tag.** Otherwise, split by the second component of the OpenAPI tag (`Configuration - Feature Profile (SDWAN)` → `feature_profile_sdwan`). Sibling sub-tags with fewer than 4 operations collapse into a single `<parent>_misc` umbrella (see [below](#misc-umbrella)). For each resulting sub-tag tool, if it still has `> max_actions_per_tool` operations, recurse.
3. **URL path.** Recurse on URL path segments starting at depth 3, then 4, then 5. The discriminating segment that produces the most even split becomes the tool name suffix. If a bucket is still over the threshold at depth 5, the loader gives up, emits the oversized tool, and logs a [WARNING](#max-depth-warning).

### Worked example: NFVirtual

`Configuration - Feature Profile (NFVirtual)` has 72 operations on 20.18. With the default `max_actions_per_tool: 150` it fits in a single tool. Lower the cap to 50 and the algorithm kicks in: the sub-tag is over threshold, so it splits on URL path. All operations share the prefix `/v1/feature-profile/nfvirtual/`, and the discriminator lives at depth 4:

```
configuration_feature_profile_nfvirtual_networks  34
configuration_feature_profile_nfvirtual_system    29
configuration_feature_profile_nfvirtual_cli        9
```

Three tools, no bucket over the cap, names that map cleanly to NFVirtual's conceptual surface.

## `_misc` umbrella { #misc-umbrella }

At each splitting stage (sub-tag *and* URL-path), sibling buckets with fewer than 4 operations collapse into a single tool named `<parent>_misc`. This keeps the tool list from being polluted with a long tail of two- or three-operation tools.

A `_misc` bucket can itself exceed `max_actions_per_tool` if a section has a very long tail of tiny sub-tags. When that happens the loader logs a WARNING but still emits the tool — the alternative would be to keep splitting on something that has no clear hierarchical key, and the result would be more confusing than helpful.

## Max-depth warning { #max-depth-warning }

If a bucket is still over the threshold at path depth 5 — or if a `_misc` bucket overflows — you'll see one of:

```
[loader] WARNING: tool 'configuration_feature_profile_sdwan' has 187 actions
  (threshold=150) — path splitting tried depths 3-5 (max 5) and could not subdivide further.

[loader] WARNING: tool 'configuration_feature_profile_sdwan_transport' has 187 actions
  (threshold=150) — hit PATH_SPLIT_MAX_DEPTH=5 at depth 5 without further splitting.

[loader] WARNING: misc tool 'configuration_misc' has 187 actions
  (threshold=150) — many small sibling sub-tags collapsed past the cap.
```

Two ways to react:

- **Raise the threshold.** Set `max_actions_per_tool` higher in `config.yaml` (or on the CLI). Reasonable if your MCP client can handle larger descriptions and you'd rather have one big tool than an oddly-shaped split.
- **Accept the oversized tool.** It still works — the warning is informational. The dispatcher doesn't care about the size.

On the bundled specs with the default 150 threshold, **no tools trigger this warning**.

## Stable action names

Each operation gets an `action_name` derived from `(HTTP method, URL path, OpenAPI tag)`, not Cisco's `operationId`. Why?

Cisco renamed **1,211 of the 3,850 legacy operationIds (≈31 %)** in place between 20.16 and 20.18 — same URL, same method, new ID. A concrete example, `PUT /template/policy/list/site/{id}`:

| Version | Cisco `operationId` | Our `action_name` |
|---|---|---|
| 20.16 | `editPolicyList_33` | `put_policy_site_list_builder_site` |
| 20.18 | `editPolicyList_ConfigurationPolicySiteListBuilder_3103` | `put_policy_site_list_builder_site` |

The user-facing dispatch key is immune to that rename event. Cisco's `operationId` is still preserved on every `OperationSpec` so the `--diff` utility can show you which Cisco-side identifier changed under the hood — see [Spec versions and diffing](spec-versions.md).

## Actual tool counts { #actual-tool-counts }

Loader output on the bundled specs with default settings (`max_actions_per_tool: 150`):

| Version | RO tools | RO max tool | RW tools | RW max tool | Over cap |
|---|---:|---:|---:|---:|---:|
| 20.15 | 206 | 131 | 292 | 136 | 0 |
| 20.16 | 213 | 108 | 330 | 123 | 0 |
| 20.18 | 229 | 111 | 360 | 111 | 0 |

Reproduce locally:

```bash
uv run python -c "
from sdwan_mcp.loader import SpecLoader
for v in ['20.15', '20.16', '20.18']:
    for rw in (False, True):
        idx = SpecLoader('specs', v, read_write=rw).load()
        print(v, 'RW' if rw else 'RO', len(idx.groups))
"
```
