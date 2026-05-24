# Pagination design — bulk vManage endpoints

**Issue:** [#8 — Response pagination for bulk endpoints](https://github.com/thomaschristory/catalyst-sdwan-super-mcp/issues/8)
**Status:** Approved (2026-05-24)
**Scope:** dispatcher + loader + config + docs + tests. No new dependencies.

---

## Problem

vManage returns many bulk endpoints (alarms, events, statistics) as one page of a
larger result set. Today the dispatcher returns the first page only — `pageInfo`
metadata is exposed to the LLM but follow-through is left to the model, which
either gives up or burns tokens chaining calls manually.

## Goal

Make paginated calls "just work" up to a safe cap, while remaining honest about
truncation and letting the LLM resume when it actually needs more.

---

## Pagination shapes in 20.18

Surveyed from `specs/20.18/*.yaml`. Two shapes cover all observed cases:

### `scroll` (Elasticsearch-style)

- **Request:** `scrollId` query parameter (optional on first call, required on subsequent).
- **Response:** `pageInfo.scrollId`, `pageInfo.hasMoreData`, `pageInfo.count`, `pageInfo.totalCount`.
- **Used by:** alarms (`/alarms`, `getActiveAlarms`, `postPage_MonitoringAlarmsDetails_*`),
  bulk stats (`getPostStatBulkRawPropertyData`, etc.), event correlation endpoints.
- Both GET and POST. For POST the cursor still goes in the query string, never the body.

### `offset` (page/pageSize)

- **Request:** `page` (1-indexed) plus a size param (`pageSize`, `count`, or `limit`).
- **Response:** plain `data: [...]`. No explicit "more" flag — stop when the page is
  short of `pageSize`, or when `data` is empty.
- **Used by:** ~70+ endpoints across configuration, inventory, and audit-log family.

Anything else (single-shot endpoints, ad-hoc `start`/`startId` filtering) is treated
as non-paginated.

---

## Detection (spec-load time)

`OperationSpec` gains:

```python
pagination: Literal["scroll", "offset"] | None = None
```

Decided in `loader._extract_operations()` from query-parameter names:

1. Any param named `scrollId` → `scroll`.
2. Else, a `page` param plus one of `pageSize` / `count` / `limit` → `offset`.
3. Else → `None`.

Detection happens once and is cached on `OperationSpec`. Response shape is
sanity-checked at runtime — if the spec hint says `scroll` but the response has no
`pageInfo.scrollId`, the dispatcher falls back to single-page passthrough and logs a
warning.

---

## Config

New block in `config.yaml`, parsed by `config.py`:

```yaml
sdwan:
  pagination:
    enabled: true     # master switch
    max_pages: 5      # default cap on auto-follow
    page_size: null   # null = use endpoint default; offset-style only
```

Loaded into a `PaginationConfig` dataclass and passed to `Dispatcher` at
construction.

## Per-call reserved params

The dispatcher strips these from `raw_params` before forwarding to vManage:

| Param | Type | Meaning |
|---|---|---|
| `_pagination` | `"off"` | Skip pagination entirely for this call; return raw first page. The literal string `"off"` is the only recognised disabling value; any other value is ignored. |
| `_max_pages` | int | Override `config.max_pages` for this call |
| `_page_size` | int | Override `config.page_size` for this call (offset-style only) |

One short line at the top of every tool's description documents these — they are
universal, not per-action, so no per-action description bloat. Example:

> *Pagination: paginated actions auto-stitch up to N pages and return
> `{data, pagination: {...}}`. Override per call with `_max_pages`, `_page_size`,
> or `_pagination: "off"`.*

---

## Dispatcher flow

New module `sdwan_mcp/pagination.py` with a strategy per shape:

```python
class Paginator(Protocol):
    async def paginate(
        self,
        op: OperationSpec,
        params: dict,
        executor: Callable[[OperationSpec, dict], Awaitable[dict]],
        max_pages: int,
        page_size: int | None,
    ) -> dict: ...


class ScrollPaginator: ...
class OffsetPaginator: ...
```

`Dispatcher._execute_with_retry` becomes:

```
1. ensure_fresh()
2. strip reserved params from raw_params; resolve effective max_pages/page_size
3. if op.pagination and cfg.enabled and not opted_out:
       result = paginator.paginate(op, params, self._execute_one, max_pages, page_size)
   else:
       result = self._execute_one(op, params)
4. handle session-expired retry as today
```

`executor` is `Dispatcher._execute` bound to the same auth/retry context — strategies
do not know about login, retry, or transport.

### `ScrollPaginator.paginate`

```
pages = []
cursor = None
while len(pages) < max_pages:
    if cursor: params["scrollId"] = cursor
    page = await executor(op, params)
    pages.append(page)
    info = page.get("pageInfo") or {}
    cursor = info.get("scrollId")
    if not info.get("hasMoreData") or not cursor:
        cursor = None
        break
return stitch(pages, style="scroll", cursor={"scrollId": cursor} if cursor else None)
```

### `OffsetPaginator.paginate`

```
pages = []
page_num = int(params.get("page", 1))
size = page_size or params.get("pageSize") or params.get("count") or params.get("limit")
while len(pages) < max_pages:
    params["page"] = page_num
    if page_size is not None: params["pageSize"] = page_size  # only when overridden
    page = await executor(op, params)
    pages.append(page)
    items = _first_list_value(page) or []
    if size and len(items) < int(size):
        next_cursor = None
        break
    if not items:
        next_cursor = None
        break
    page_num += 1
else:
    next_cursor = {"page": page_num, "pageSize": size} if size else {"page": page_num}
return stitch(pages, style="offset", cursor=next_cursor)
```

### Stitching

`_first_list_value(response)` returns the first top-level list-typed value.
In vManage that is reliably `data`. Stitched output:

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

Rules:

- `truncated: true` ⇔ `next_cursor` is non-null.
- The wrapper is added **only when pagination engaged**. Non-paginated ops and
  opt-out (`_pagination: "off"`) calls return the raw response unchanged.
- If the first page has no list-typed top-level value, we log a warning and fall
  back to passthrough of page one (defensive — shouldn't happen in practice).
- Other top-level fields from the first page (e.g. `header`, `totalCount`) are
  preserved at the wrapper root under their original names, alongside `data` and
  `pagination`. `pageInfo` itself is consumed by the paginator and not re-emitted
  at the root — its useful bits are folded into the `pagination` block.

---

## Resumption protocol

When `truncated: true`, the LLM resends the same action with the `next_cursor`
fields as ordinary params:

- `scroll`: `params["scrollId"] = "<cursor>"`
- `offset`: `params["page"] = N` (and optionally `pageSize`)

Pagination then runs again from that cursor for up to `max_pages` more pages. No
server-side cursor state — every call is self-contained.

---

## Files touched

| File | Change |
|---|---|
| `sdwan_mcp/loader.py` | Detect pagination style; add `pagination` field to `OperationSpec`. |
| `sdwan_mcp/config.py` | New `PaginationConfig`; parse `sdwan.pagination` block. |
| `sdwan_mcp/pagination.py` | **New.** `Paginator` protocol + `ScrollPaginator` + `OffsetPaginator` + stitch helper. |
| `sdwan_mcp/dispatcher.py` | Accept `PaginationConfig`; strip reserved params; route to paginator when applicable. |
| `sdwan_mcp/tools.py` | Prepend one-line pagination note to every tool description. |
| `sdwan_mcp/server.py` | Wire config block into dispatcher constructor. |
| `config.yaml` | Add `sdwan.pagination` defaults. |
| `tests/test_pagination.py` | **New.** See test plan below. |
| `tests/test_loader.py` | Add cases for pagination-style detection. |
| `tests/conftest.py` | Extend minimal spec fixture with one scroll + one offset op. |
| `docs/architecture/data-flow.md` | Add pagination step to tool-call flow. |
| `docs/guides/pagination.md` | **New.** Behavior, styles, knobs, examples, opt-out. |
| `mkdocs.yml` | Register the new guide. |
| `CLAUDE.md` | Config block + decisions-log row. |
| `CHANGELOG.md` | Entry under Unreleased. |

---

## Test plan (`tests/test_pagination.py`, respx)

Each test uses a tiny in-memory spec and a respx-mocked vManage.

1. **scroll, full drain** — page 1 `hasMoreData=true`, page 2 `hasMoreData=false`.
   Assert: 2 requests, stitched `data` length = sum, `pagination.truncated=false`,
   `next_cursor` is null.
2. **scroll, truncated** — `max_pages=2`, server always returns `hasMoreData=true`.
   Assert: 2 requests, `truncated=true`, `next_cursor.scrollId` matches page-2's
   `pageInfo.scrollId`.
3. **scroll, opt-out** — `_pagination: "off"`. Assert: 1 request, raw shape
   (no `pagination` key).
4. **offset, full drain** — `pageSize=10`, page 1 has 10 items, page 2 has 3.
   Assert: 2 requests, stitched length 13, `truncated=false`.
5. **offset, truncated via `_max_pages`** — override to 1, server has many.
   Assert: 1 request, `truncated=true`, `next_cursor.page == 2`.
6. **offset, `_page_size` override** — sent on every paginated request as `pageSize`.
7. **non-paginated op** — `OperationSpec.pagination is None`. Assert: 1 request,
   raw shape, no wrapper.
8. **spec hint vs response mismatch** — op marked `scroll`, response lacks
   `pageInfo`. Assert: fallback to single page passthrough + warning logged.
9. **scroll on POST** — body is preserved across pages, only `scrollId` in query
   changes.
10. **reserved params stripped** — `_max_pages`, `_page_size`, `_pagination` never
    reach the wire.

`tests/test_loader.py` additions:

- Detect `scroll` from a `scrollId` query param.
- Detect `offset` from `page` + `pageSize`.
- Do not flag ops with `page` alone (no size param) as paginated.

---

## Out of scope

- Cross-call cursor caching (each call is self-contained; LLM passes cursor back).
- Per-action config overrides (`_pagination: "off"` is the escape hatch).
- Heuristics for non-standard shapes (e.g. timestamp-window walking on stats).
- Streaming responses; everything stays a single dict.
- Concurrency across pages — sequential only; vManage cursors are not parallel-safe.

---

## Decisions log delta (for CLAUDE.md)

| Decision | Choice | Reason |
|---|---|---|
| Pagination strategy | Hybrid auto-follow + cursor | Common case "just works"; truncation honest, resumable. |
| Pagination knobs | Config defaults + `_*` reserved params | Server-wide default, per-call override without bloating action params. |
| Response shape | Always wrap when paginated (`{data, pagination}`) | Predictable signal to LLM that auto-follow ran. |
| Style detection | Spec-load time from param names | Cheap, deterministic; response sanity-check covers drift. |
