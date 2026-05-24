# Pagination

Many vManage endpoints (alarms, events, bulk statistics, large inventory
listings) return one page of a larger result set. The server detects two
pagination shapes from the OpenAPI spec and auto-follows them, returning a
single stitched response.

## Shapes

- **scroll** — Elasticsearch-style. Request param `scrollId`; response carries
  `pageInfo.scrollId` and `pageInfo.hasMoreData`. Used by alarms, events,
  bulk stats.
- **offset** — Classic `page` / `pageSize` (or `count` / `limit`). Used by
  most configuration and inventory listings.

## Behaviour

When pagination is engaged the response is wrapped:

```json
{
  "data": [ ...concatenated items from every page... ],
  "pagination": {
    "style": "scroll",
    "pages_fetched": 5,
    "truncated": true,
    "next_cursor": {"scrollId": "DXF1Z..."}
  }
}
```

- `truncated: true` ⇔ `next_cursor` is non-null. To get the next batch, call
  the same action again with `next_cursor`'s fields merged into `params` —
  e.g. `params["scrollId"] = "DXF1Z..."` (scroll) or `params["page"] = 6`
  (offset).
- Non-paginated actions and explicit opt-outs return the raw response shape
  unchanged.

## Configuration

```yaml
sdwan:
  pagination:
    enabled: true     # master switch
    max_pages: 5      # default cap on auto-follow
    page_size: null   # null → use the endpoint's natural page size
```

## Per-call overrides

The dispatcher recognises three reserved underscore keys in `params`. They
are stripped before the request is sent to vManage:

| Param | Type | Effect |
|---|---|---|
| `_max_pages` | int | Override `max_pages` for this call. |
| `_page_size` | int | Override `page_size` for this call (offset only). |
| `_pagination` | `"off"` | Skip pagination; return the raw first page. The literal `"off"` is the only recognised disabling value. |

## Examples

Fetch up to 10 pages of alarms:

```json
{ "action": "get_alarms", "params": { "_max_pages": 10 } }
```

Disable auto-follow for one call:

```json
{ "action": "get_alarms", "params": { "_pagination": "off" } }
```

Resume from a returned cursor:

```json
{ "action": "get_alarms", "params": { "scrollId": "DXF1Z..." } }
```
