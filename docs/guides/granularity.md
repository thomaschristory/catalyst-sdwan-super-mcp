# Tool granularity

The vManage API is huge — 20.18 has **4,102 operations** spread over **375 OpenAPI tags**. Naively mapping one MCP tool per tag gives an LLM 300+ tools, which most clients (and most LLMs) cannot ingest cleanly.

So we offer two granularities:

| Granularity | What it groups | Tools (20.18, RW) | Use when |
|---|---|---|---|
| `section` (default) | First word before " - " | **~65** | You want the LLM to find anything across the API. |
| `tag` | Full Cisco tag | **~375** | Your client can handle hundreds of tools and you want narrower descriptions. |

> **Note:** this two-mode toggle is being replaced by an adaptive splitter — see issue [#13](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/issues/13).

## How grouping works

A Cisco tag like `Configuration - Feature Profile (SDWAN)` is parsed as:

- **section** = `Configuration`
- **tag** = `Configuration - Feature Profile (SDWAN)`

In `section` mode all 1,500+ Configuration operations roll up into a single `configuration` tool. The tool's description still lists every operation by name, so the LLM still knows exactly what's available — it just picks `action="getFeatureProfile"` instead of having a `configuration_feature_profile_sdwan` tool to choose from.

## Tradeoff

- `section`: fewer tools, but each tool's description is huge (megabytes for `configuration`). Some LLM clients reject oversized tool descriptions. If you hit this, switch to `tag`.
- `tag`: many tools but each one is small. Better for clients that gracefully handle hundreds of tools.

## Switching

`config.yaml`:

```yaml
sdwan:
  tag_granularity: section   # or "tag"
```

Or CLI override:

```bash
uv run sdwan-mcp --granularity tag
```
