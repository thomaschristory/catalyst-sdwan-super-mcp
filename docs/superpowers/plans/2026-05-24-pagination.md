# Pagination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-follow paginated vManage endpoints up to a configurable cap, return a stitched response with an optional resumable cursor.

**Architecture:** Loader marks each `OperationSpec` with `pagination = "scroll" | "offset" | None`. A new `pagination.py` module implements one paginator per shape behind a common `Paginator` protocol. The `Dispatcher` strips reserved `_max_pages` / `_page_size` / `_pagination` params from the call, then either routes to the matching paginator (which calls back into a single-page executor) or executes once as today. Stitched responses are wrapped as `{data, pagination: {...}, ...rest}`; non-paginated calls are unchanged.

**Tech Stack:** Python 3.11+, `httpx` (async), `pytest` + `pytest-asyncio` + `respx`, `pyyaml`, `fastmcp`.

**Spec:** `docs/superpowers/specs/2026-05-24-pagination-design.md`

---

## File Structure

| Path | Status | Responsibility |
|---|---|---|
| `sdwan_mcp/config.py` | modify | Add `PaginationConfig` dataclass; parse `sdwan.pagination` block. |
| `sdwan_mcp/loader.py` | modify | Add `pagination` field to `OperationSpec`; detect style in `_parse_operation`. |
| `sdwan_mcp/pagination.py` | **new** | `Paginator` protocol, `ScrollPaginator`, `OffsetPaginator`, `stitch()` helper, `PaginatedResult`. |
| `sdwan_mcp/dispatcher.py` | modify | Accept `PaginationConfig`; strip reserved params; route to paginator. |
| `sdwan_mcp/tools.py` | modify | Prepend one-line pagination note to tool descriptions. |
| `sdwan_mcp/server.py` | modify | Pass `cfg.sdwan.pagination` into `Dispatcher(...)`. |
| `config.yaml` | modify | Add `sdwan.pagination` block. |
| `tests/test_config.py` | modify | Cover new `pagination` parsing. |
| `tests/test_loader.py` | modify | Cover pagination-style detection. |
| `tests/test_pagination.py` | **new** | All pagination behavior (scroll, offset, opt-out, overrides, mismatch). |
| `tests/test_dispatcher.py` | modify | One smoke test that paginated route still works end-to-end. |
| `tests/test_tools.py` | modify (create if absent) | Verify pagination note prepended to description. |
| `tests/conftest.py` | unchanged | Existing fixtures remain. Pagination tests build their own minimal specs. |
| `docs/architecture/data-flow.md` | modify | Add pagination step. |
| `docs/guides/pagination.md` | **new** | User-facing behavior, knobs, examples. |
| `mkdocs.yml` | modify | Register new guide. |
| `CLAUDE.md` | modify | Config block + decisions-log row. |
| `CHANGELOG.md` | modify | Unreleased entry. |

---

## Task 1: PaginationConfig dataclass + config.yaml parsing

**Files:**
- Modify: `sdwan_mcp/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing test for default pagination config**

Add to `tests/test_config.py`:

```python
def test_pagination_defaults(tmp_path):
    from sdwan_mcp.config import load_config
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        "vmanage:\n"
        "  host: vm.test\n"
        "sdwan:\n"
        "  active_version: '20.18'\n"
    )
    cfg = load_config(str(cfg_file))
    assert cfg.sdwan.pagination.enabled is True
    assert cfg.sdwan.pagination.max_pages == 5
    assert cfg.sdwan.pagination.page_size is None


def test_pagination_overrides(tmp_path):
    from sdwan_mcp.config import load_config
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        "vmanage:\n"
        "  host: vm.test\n"
        "sdwan:\n"
        "  pagination:\n"
        "    enabled: false\n"
        "    max_pages: 12\n"
        "    page_size: 200\n"
    )
    cfg = load_config(str(cfg_file))
    assert cfg.sdwan.pagination.enabled is False
    assert cfg.sdwan.pagination.max_pages == 12
    assert cfg.sdwan.pagination.page_size == 200
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `uv run pytest tests/test_config.py::test_pagination_defaults tests/test_config.py::test_pagination_overrides -v`
Expected: FAIL with `AttributeError: 'SDWANConfig' object has no attribute 'pagination'`.

- [ ] **Step 3: Add PaginationConfig dataclass and wire it in**

In `sdwan_mcp/config.py`, add above `SDWANConfig`:

```python
@dataclass
class PaginationConfig:
    enabled: bool = True
    max_pages: int = 5
    page_size: int | None = None
```

Modify `SDWANConfig` to include the field:

```python
@dataclass
class SDWANConfig:
    specs_dir: str = "./specs"
    active_version: str = "20.18"
    max_actions_per_tool: int = 150
    pagination: PaginationConfig = field(default_factory=PaginationConfig)
```

In `load_config()`, after the existing `sdwan = SDWANConfig(...)` block, parse the pagination sub-block. Replace the existing `sdwan = SDWANConfig(...)` assignment with:

```python
    pagination_raw = sdwan_raw.get("pagination", {}) or {}
    pagination = PaginationConfig(
        enabled=bool(pagination_raw.get("enabled", True)),
        max_pages=int(pagination_raw.get("max_pages", 5)),
        page_size=(
            int(pagination_raw["page_size"])
            if pagination_raw.get("page_size") is not None
            else None
        ),
    )

    sdwan = SDWANConfig(
        specs_dir=sdwan_raw.get("specs_dir", "./specs"),
        active_version=str(sdwan_raw.get("active_version", "20.18")),
        max_actions_per_tool=int(sdwan_raw.get("max_actions_per_tool", 150)),
        pagination=pagination,
    )
```

- [ ] **Step 4: Run tests, confirm they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS, all existing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add sdwan_mcp/config.py tests/test_config.py
git commit -m "feat(config): add PaginationConfig with enabled/max_pages/page_size"
```

---

## Task 2: Pagination-style detection in the loader

**Files:**
- Modify: `sdwan_mcp/loader.py` (`OperationSpec` dataclass and `_parse_operation`)
- Modify: `tests/test_loader.py`

- [ ] **Step 1: Write failing tests for style detection**

Add to `tests/test_loader.py`:

```python
import yaml
from sdwan_mcp.loader import SpecLoader


def _write_spec(tmp_path, paths):
    version_dir = tmp_path / "specs" / "20.99"
    version_dir.mkdir(parents=True)
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "t", "version": "1.0"},
        "paths": paths,
    }
    (version_dir / "ops.yaml").write_text(yaml.safe_dump(spec))
    return tmp_path / "specs"


def _op_by_action(idx, name):
    return idx.by_action_name[name]


def test_loader_detects_scroll_style(tmp_path):
    paths = {
        "/alarms": {
            "get": {
                "tags": ["Monitoring - Alarms"],
                "operationId": "getAlarms",
                "parameters": [
                    {"name": "scrollId", "in": "query", "schema": {"type": "string"}},
                ],
            }
        }
    }
    idx = SpecLoader(str(_write_spec(tmp_path, paths)), "20.99", read_write=False).load()
    op = next(iter(idx.by_action_name.values()))
    assert op.pagination == "scroll"


def test_loader_detects_offset_style(tmp_path):
    paths = {
        "/devices": {
            "get": {
                "tags": ["Configuration - Devices"],
                "operationId": "listDevices",
                "parameters": [
                    {"name": "page", "in": "query", "schema": {"type": "integer"}},
                    {"name": "pageSize", "in": "query", "schema": {"type": "integer"}},
                ],
            }
        }
    }
    idx = SpecLoader(str(_write_spec(tmp_path, paths)), "20.99", read_write=False).load()
    op = next(iter(idx.by_action_name.values()))
    assert op.pagination == "offset"


def test_loader_offset_with_count_or_limit(tmp_path):
    for size_param in ("count", "limit"):
        paths = {
            f"/items_{size_param}": {
                "get": {
                    "tags": ["Misc - Items"],
                    "operationId": f"listItems_{size_param}",
                    "parameters": [
                        {"name": "page", "in": "query", "schema": {"type": "integer"}},
                        {"name": size_param, "in": "query", "schema": {"type": "integer"}},
                    ],
                }
            }
        }
        idx = SpecLoader(
            str(_write_spec(tmp_path / size_param, paths)),
            "20.99",
            read_write=False,
        ).load()
        op = next(iter(idx.by_action_name.values()))
        assert op.pagination == "offset", f"failed for size param {size_param}"


def test_loader_no_pagination_when_only_page_param(tmp_path):
    paths = {
        "/x": {
            "get": {
                "tags": ["Misc - X"],
                "operationId": "listX",
                "parameters": [
                    {"name": "page", "in": "query", "schema": {"type": "integer"}},
                ],
            }
        }
    }
    idx = SpecLoader(str(_write_spec(tmp_path, paths)), "20.99", read_write=False).load()
    op = next(iter(idx.by_action_name.values()))
    assert op.pagination is None


def test_loader_no_pagination_for_plain_op(tmp_path):
    paths = {
        "/single": {
            "get": {
                "tags": ["Misc - Single"],
                "operationId": "getSingle",
            }
        }
    }
    idx = SpecLoader(str(_write_spec(tmp_path, paths)), "20.99", read_write=False).load()
    op = next(iter(idx.by_action_name.values()))
    assert op.pagination is None
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `uv run pytest tests/test_loader.py -k "pagination" -v`
Expected: FAIL with `AttributeError: 'OperationSpec' object has no attribute 'pagination'`.

- [ ] **Step 3: Add pagination field and detection helper**

In `sdwan_mcp/loader.py`, modify `OperationSpec`:

```python
@dataclass
class OperationSpec:
    operation_id: str
    action_name: str
    summary: str
    method: str
    path: str
    tag: str
    parameters: list[ParameterSpec] = field(default_factory=list)
    has_body: bool = False
    body_description: str = ""
    pagination: str | None = None  # "scroll" | "offset" | None
```

Add a detection helper near the top-level helpers (above `_parse_operation`):

```python
_OFFSET_SIZE_PARAMS = {"pageSize", "count", "limit"}


def _detect_pagination_style(parameters: list[ParameterSpec]) -> str | None:
    """
    Decide pagination style from a parsed parameter list.

    - "scroll" if any param is named scrollId
    - "offset" if both `page` and one of pageSize/count/limit are present
    - None otherwise
    """
    names = {p.name for p in parameters if p.location == "query"}
    if "scrollId" in names:
        return "scroll"
    if "page" in names and (names & _OFFSET_SIZE_PARAMS):
        return "offset"
    return None
```

Modify `_parse_operation` to populate the field. Replace the existing `return OperationSpec(...)` block at the bottom of `_parse_operation` with:

```python
    parameters = _parse_parameters(operation.get("parameters", []))
    return OperationSpec(
        operation_id=op_id,
        action_name=_derive_action_name(method, path, tag),
        summary=operation.get("summary") or operation.get("description", ""),
        method=method,
        path=path,
        tag=tag,
        parameters=parameters,
        has_body=has_body,
        body_description=body_desc,
        pagination=_detect_pagination_style(parameters),
    )
```

- [ ] **Step 4: Run tests, confirm they pass**

Run: `uv run pytest tests/test_loader.py -v`
Expected: PASS, all existing loader tests still green.

- [ ] **Step 5: Commit**

```bash
git add sdwan_mcp/loader.py tests/test_loader.py
git commit -m "feat(loader): detect scroll/offset pagination style on OperationSpec"
```

---

## Task 3: `pagination.py` skeleton — types, stitch helper, protocol

**Files:**
- Create: `sdwan_mcp/pagination.py`
- Create: `tests/test_pagination.py`

- [ ] **Step 1: Write failing tests for stitch + first-list-value**

Create `tests/test_pagination.py`:

```python
"""Tests for sdwan_mcp.pagination — stitch helper, scroll/offset paginators."""

from __future__ import annotations

import pytest

from sdwan_mcp.pagination import _first_list_key, stitch


def test_first_list_key_returns_data_when_present():
    assert _first_list_key({"data": [1, 2], "header": {}}) == "data"


def test_first_list_key_returns_first_list_field():
    assert _first_list_key({"meta": {}, "items": [1], "data": [2]}) in {"items", "data"}


def test_first_list_key_returns_none_when_no_list():
    assert _first_list_key({"meta": {}, "header": {}}) is None


def test_stitch_concatenates_data_across_pages():
    pages = [
        {"data": [1, 2], "header": {"sig": "first"}},
        {"data": [3, 4]},
        {"data": [5]},
    ]
    result = stitch(pages, style="offset", next_cursor=None)
    assert result["data"] == [1, 2, 3, 4, 5]
    assert result["pagination"] == {
        "style": "offset",
        "pages_fetched": 3,
        "truncated": False,
        "next_cursor": None,
    }
    # Other top-level fields from page 1 are preserved.
    assert result["header"] == {"sig": "first"}


def test_stitch_marks_truncated_when_cursor_present():
    pages = [{"data": [1]}]
    cursor = {"scrollId": "abc"}
    result = stitch(pages, style="scroll", next_cursor=cursor)
    assert result["pagination"]["truncated"] is True
    assert result["pagination"]["next_cursor"] == cursor


def test_stitch_drops_pageInfo_from_root():
    pages = [{"data": [1], "pageInfo": {"scrollId": "abc"}}]
    result = stitch(pages, style="scroll", next_cursor={"scrollId": "abc"})
    assert "pageInfo" not in result
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `uv run pytest tests/test_pagination.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sdwan_mcp.pagination'`.

- [ ] **Step 3: Implement the module skeleton**

Create `sdwan_mcp/pagination.py`:

```python
"""
pagination.py — auto-follow paginated vManage endpoints.

Two shapes are supported, detected at spec-load time and stored on
`OperationSpec.pagination`:

  - "scroll": Elasticsearch-style. Request param scrollId; response carries
    pageInfo.scrollId and pageInfo.hasMoreData.
  - "offset": Classic page/pageSize. Stop when a page returns fewer items
    than pageSize, or when it is empty.

A paginator pulls up to `max_pages` pages from `executor` (a bound single-call
function on the dispatcher), then returns a stitched dict via `stitch()`.

The response envelope when pagination engaged:

    {
        "data": [...concatenated items...],
        "pagination": {
            "style": "scroll" | "offset",
            "pages_fetched": int,
            "truncated": bool,
            "next_cursor": dict | None,
        },
        ...other top-level fields from page 1 (except pageInfo)...,
    }
"""

from __future__ import annotations

from typing import Awaitable, Callable, Protocol

from .loader import OperationSpec


Executor = Callable[[OperationSpec, dict], Awaitable[dict]]


class Paginator(Protocol):
    async def paginate(
        self,
        op: OperationSpec,
        params: dict,
        executor: Executor,
        max_pages: int,
        page_size: int | None,
    ) -> dict: ...


# ---------------------------------------------------------------------------
# Stitching helpers
# ---------------------------------------------------------------------------


def _first_list_key(page: dict) -> str | None:
    """Return the first top-level key whose value is a list, or None."""
    if not isinstance(page, dict):
        return None
    for key, value in page.items():
        if isinstance(value, list):
            return key
    return None


def stitch(pages: list[dict], style: str, next_cursor: dict | None) -> dict:
    """
    Concatenate the list-typed top-level field across pages and wrap with
    a pagination block. Other top-level fields from the first page are
    preserved at the root, except `pageInfo` (it is folded into `pagination`).
    """
    if not pages:
        return {
            "data": [],
            "pagination": {
                "style": style,
                "pages_fetched": 0,
                "truncated": False,
                "next_cursor": None,
            },
        }

    first = pages[0] if isinstance(pages[0], dict) else {}
    list_key = _first_list_key(first) or "data"

    stitched_items: list = []
    for page in pages:
        if isinstance(page, dict):
            items = page.get(list_key)
            if isinstance(items, list):
                stitched_items.extend(items)

    # Preserve page-1 root fields, but strip the per-page list (it's partial)
    # and pageInfo (folded into `pagination`). Stitched list is always exposed
    # under "data" for a predictable envelope.
    out: dict = {
        k: v
        for k, v in first.items()
        if k not in {list_key, "pageInfo", "data"}
    }
    out["data"] = stitched_items
    out["pagination"] = {
        "style": style,
        "pages_fetched": len(pages),
        "truncated": next_cursor is not None,
        "next_cursor": next_cursor,
    }
    return out
```

- [ ] **Step 4: Run tests, confirm they pass**

Run: `uv run pytest tests/test_pagination.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add sdwan_mcp/pagination.py tests/test_pagination.py
git commit -m "feat(pagination): add module skeleton with stitch helper"
```

---

## Task 4: `ScrollPaginator`

**Files:**
- Modify: `sdwan_mcp/pagination.py`
- Modify: `tests/test_pagination.py`

- [ ] **Step 1: Write failing tests for the scroll paginator**

Append to `tests/test_pagination.py`:

```python
from sdwan_mcp.loader import OperationSpec, ParameterSpec
from sdwan_mcp.pagination import ScrollPaginator


def _scroll_op(method: str = "get") -> OperationSpec:
    return OperationSpec(
        operation_id="getAlarms",
        action_name="get_alarms",
        summary="",
        method=method,
        path="/alarms",
        tag="Monitoring - Alarms",
        parameters=[ParameterSpec(name="scrollId", location="query")],
        has_body=False,
        pagination="scroll",
    )


def _scroll_pages(*, with_more: list[bool], cursors: list[str]):
    """Build a list of fake response dicts matching pageInfo shape."""
    assert len(with_more) == len(cursors)
    out = []
    for i, (more, cursor) in enumerate(zip(with_more, cursors)):
        out.append({
            "data": [{"i": i, "n": 0}, {"i": i, "n": 1}],
            "pageInfo": {
                "scrollId": cursor,
                "hasMoreData": more,
                "count": 2,
                "totalCount": 10,
            },
        })
    return out


@pytest.mark.asyncio
async def test_scroll_full_drain():
    pages = _scroll_pages(with_more=[True, False], cursors=["c1", "c2"])
    seen_params: list[dict] = []

    async def executor(op, params):
        seen_params.append(dict(params))
        return pages[len(seen_params) - 1]

    result = await ScrollPaginator().paginate(
        _scroll_op(), {}, executor, max_pages=5, page_size=None
    )
    assert [len(p["data"]) for p in pages] == [2, 2]
    assert result["data"] == [
        {"i": 0, "n": 0}, {"i": 0, "n": 1},
        {"i": 1, "n": 0}, {"i": 1, "n": 1},
    ]
    assert result["pagination"]["truncated"] is False
    assert result["pagination"]["next_cursor"] is None
    assert result["pagination"]["pages_fetched"] == 2
    # First call has no scrollId; second carries the cursor from page 1.
    assert "scrollId" not in seen_params[0]
    assert seen_params[1]["scrollId"] == "c1"


@pytest.mark.asyncio
async def test_scroll_truncated_at_max_pages():
    pages = _scroll_pages(
        with_more=[True, True, True],
        cursors=["c1", "c2", "c3"],
    )

    async def executor(op, params):
        return pages.pop(0)

    result = await ScrollPaginator().paginate(
        _scroll_op(), {}, executor, max_pages=2, page_size=None
    )
    assert result["pagination"]["truncated"] is True
    assert result["pagination"]["next_cursor"] == {"scrollId": "c2"}
    assert result["pagination"]["pages_fetched"] == 2


@pytest.mark.asyncio
async def test_scroll_preserves_body_on_post():
    pages = _scroll_pages(with_more=[False], cursors=["c1"])
    seen: list[dict] = []

    async def executor(op, params):
        seen.append(dict(params))
        return pages.pop(0)

    op = _scroll_op(method="post")
    await ScrollPaginator().paginate(
        op, {"filter": {"severity": "critical"}}, executor, max_pages=3, page_size=None
    )
    # Body / filter params survive untouched.
    assert seen[0]["filter"] == {"severity": "critical"}


@pytest.mark.asyncio
async def test_scroll_response_missing_pageInfo_returns_single_page():
    """If the spec says scroll but the server returns no pageInfo, stop after one page."""
    page = {"data": [1, 2, 3]}

    async def executor(op, params):
        return page

    result = await ScrollPaginator().paginate(
        _scroll_op(), {}, executor, max_pages=5, page_size=None
    )
    assert result["data"] == [1, 2, 3]
    assert result["pagination"]["pages_fetched"] == 1
    assert result["pagination"]["truncated"] is False
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `uv run pytest tests/test_pagination.py -v`
Expected: FAIL with `ImportError: cannot import name 'ScrollPaginator'`.

- [ ] **Step 3: Implement ScrollPaginator**

Append to `sdwan_mcp/pagination.py`:

```python
# ---------------------------------------------------------------------------
# Scroll paginator
# ---------------------------------------------------------------------------


class ScrollPaginator:
    """Elasticsearch-style cursor pagination via scrollId / pageInfo.hasMoreData."""

    async def paginate(
        self,
        op: OperationSpec,
        params: dict,
        executor: Executor,
        max_pages: int,
        page_size: int | None,  # noqa: ARG002 — irrelevant for scroll
    ) -> dict:
        pages: list[dict] = []
        cursor: str | None = params.get("scrollId")
        current = dict(params)

        while len(pages) < max_pages:
            if cursor is not None:
                current["scrollId"] = cursor
            page = await executor(op, current)
            pages.append(page if isinstance(page, dict) else {})

            info = (page.get("pageInfo") if isinstance(page, dict) else None) or {}
            next_cursor = info.get("scrollId")
            has_more = bool(info.get("hasMoreData"))
            if not has_more or not next_cursor:
                cursor = None
                break
            cursor = next_cursor
        else:
            # Loop exited because we hit max_pages with more available.
            pass

        next_cursor_obj = {"scrollId": cursor} if cursor else None
        return stitch(pages, style="scroll", next_cursor=next_cursor_obj)
```

- [ ] **Step 4: Run tests, confirm they pass**

Run: `uv run pytest tests/test_pagination.py -v`
Expected: PASS (all scroll tests green; earlier tests still green).

- [ ] **Step 5: Commit**

```bash
git add sdwan_mcp/pagination.py tests/test_pagination.py
git commit -m "feat(pagination): ScrollPaginator for scrollId/pageInfo endpoints"
```

---

## Task 5: `OffsetPaginator`

**Files:**
- Modify: `sdwan_mcp/pagination.py`
- Modify: `tests/test_pagination.py`

- [ ] **Step 1: Write failing tests for the offset paginator**

Append to `tests/test_pagination.py`:

```python
from sdwan_mcp.pagination import OffsetPaginator


def _offset_op() -> OperationSpec:
    return OperationSpec(
        operation_id="listDevices",
        action_name="list_devices",
        summary="",
        method="get",
        path="/devices",
        tag="Configuration - Devices",
        parameters=[
            ParameterSpec(name="page", location="query"),
            ParameterSpec(name="pageSize", location="query"),
        ],
        has_body=False,
        pagination="offset",
    )


@pytest.mark.asyncio
async def test_offset_full_drain_stops_on_short_page():
    pages = [
        {"data": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]},
        {"data": [11, 12, 13]},
    ]
    seen: list[dict] = []

    async def executor(op, params):
        seen.append(dict(params))
        return pages[len(seen) - 1]

    result = await OffsetPaginator().paginate(
        _offset_op(), {"pageSize": 10}, executor, max_pages=5, page_size=None
    )
    assert result["data"] == list(range(1, 14))
    assert result["pagination"]["truncated"] is False
    assert result["pagination"]["next_cursor"] is None
    assert [c["page"] for c in seen] == [1, 2]


@pytest.mark.asyncio
async def test_offset_truncated_at_max_pages():
    pages = [{"data": list(range(10))} for _ in range(5)]
    seen: list[dict] = []

    async def executor(op, params):
        seen.append(dict(params))
        return pages[len(seen) - 1]

    result = await OffsetPaginator().paginate(
        _offset_op(), {"pageSize": 10}, executor, max_pages=2, page_size=None
    )
    assert result["pagination"]["truncated"] is True
    assert result["pagination"]["next_cursor"] == {"page": 3, "pageSize": 10}
    assert [c["page"] for c in seen] == [1, 2]


@pytest.mark.asyncio
async def test_offset_page_size_override_is_sent_each_call():
    pages = [{"data": [1, 2]}, {"data": []}]
    seen: list[dict] = []

    async def executor(op, params):
        seen.append(dict(params))
        return pages[len(seen) - 1]

    await OffsetPaginator().paginate(
        _offset_op(), {}, executor, max_pages=5, page_size=2
    )
    assert all(c.get("pageSize") == 2 for c in seen)


@pytest.mark.asyncio
async def test_offset_stops_on_empty_page():
    pages = [{"data": [1, 2]}, {"data": []}]

    async def executor(op, params):
        return pages.pop(0)

    result = await OffsetPaginator().paginate(
        _offset_op(), {}, executor, max_pages=5, page_size=None
    )
    assert result["data"] == [1, 2]
    assert result["pagination"]["pages_fetched"] == 2
    assert result["pagination"]["truncated"] is False


@pytest.mark.asyncio
async def test_offset_resumes_from_user_supplied_page():
    pages = [{"data": [50, 51, 52]}]

    async def executor(op, params):
        return pages.pop(0)

    result = await OffsetPaginator().paginate(
        _offset_op(), {"page": 5, "pageSize": 100}, executor, max_pages=5, page_size=None
    )
    # Short page (3 < 100) → stop without cursor.
    assert result["pagination"]["truncated"] is False
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `uv run pytest tests/test_pagination.py -v`
Expected: FAIL with `ImportError: cannot import name 'OffsetPaginator'`.

- [ ] **Step 3: Implement OffsetPaginator**

Append to `sdwan_mcp/pagination.py`:

```python
# ---------------------------------------------------------------------------
# Offset paginator
# ---------------------------------------------------------------------------


_OFFSET_SIZE_KEYS = ("pageSize", "count", "limit")


def _offset_size_param_name(op: OperationSpec) -> str:
    """Which size-param name does this op use? Defaults to pageSize if none declared."""
    query_names = {p.name for p in op.parameters if p.location == "query"}
    for key in _OFFSET_SIZE_KEYS:
        if key in query_names:
            return key
    return "pageSize"


class OffsetPaginator:
    """Classic page/<size> pagination. Stops on a short page or an empty page."""

    async def paginate(
        self,
        op: OperationSpec,
        params: dict,
        executor: Executor,
        max_pages: int,
        page_size: int | None,
    ) -> dict:
        pages: list[dict] = []
        current = dict(params)
        size_key = _offset_size_param_name(op)

        # Resolve the effective page size: explicit override > caller param > None.
        effective_size: int | None = page_size
        if effective_size is None:
            for key in _OFFSET_SIZE_KEYS:
                if key in current and current[key] is not None:
                    try:
                        effective_size = int(current[key])
                    except (TypeError, ValueError):
                        effective_size = None
                    break

        page_num = int(current.get("page", 1) or 1)
        next_cursor: dict | None = None

        while len(pages) < max_pages:
            current["page"] = page_num
            if page_size is not None:
                current[size_key] = page_size
            page = await executor(op, current)
            pages.append(page if isinstance(page, dict) else {})

            list_key = _first_list_key(page) if isinstance(page, dict) else None
            items = page.get(list_key) if (isinstance(page, dict) and list_key) else []

            # Stop conditions.
            if not items:
                next_cursor = None
                break
            if effective_size is not None and len(items) < effective_size:
                next_cursor = None
                break

            page_num += 1
        else:
            # Loop exited because we hit max_pages with potentially more available.
            next_cursor = {"page": page_num}
            if effective_size is not None:
                next_cursor[size_key] = effective_size

        return stitch(pages, style="offset", next_cursor=next_cursor)
```

- [ ] **Step 4: Run tests, confirm they pass**

Run: `uv run pytest tests/test_pagination.py -v`
Expected: PASS (all scroll + offset tests green).

- [ ] **Step 5: Commit**

```bash
git add sdwan_mcp/pagination.py tests/test_pagination.py
git commit -m "feat(pagination): OffsetPaginator for page/pageSize endpoints"
```

---

## Task 6: Dispatcher integration

**Files:**
- Modify: `sdwan_mcp/dispatcher.py`
- Modify: `tests/test_pagination.py`
- Modify: `tests/test_dispatcher.py` (smoke test only)

- [ ] **Step 1: Write failing integration tests**

Append to `tests/test_pagination.py`:

```python
import httpx
import respx
import yaml
from pathlib import Path

from sdwan_mcp.auth import VManageAuth
from sdwan_mcp.config import PaginationConfig
from sdwan_mcp.dispatcher import Dispatcher
from sdwan_mcp.loader import SpecLoader


def _paginated_spec_dir(tmp_path: Path) -> Path:
    version_dir = tmp_path / "specs" / "20.99"
    version_dir.mkdir(parents=True)
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "t", "version": "1.0"},
        "paths": {
            "/alarms": {
                "get": {
                    "tags": ["Monitoring - Alarms"],
                    "operationId": "getAlarms",
                    "parameters": [
                        {"name": "scrollId", "in": "query", "schema": {"type": "string"}},
                    ],
                }
            },
            "/devices": {
                "get": {
                    "tags": ["Configuration - Devices"],
                    "operationId": "listDevices",
                    "parameters": [
                        {"name": "page", "in": "query", "schema": {"type": "integer"}},
                        {"name": "pageSize", "in": "query", "schema": {"type": "integer"}},
                    ],
                }
            },
            "/single": {
                "get": {
                    "tags": ["Misc - Single"],
                    "operationId": "getSingle",
                }
            },
        },
    }
    (version_dir / "ops.yaml").write_text(yaml.safe_dump(spec))
    return tmp_path / "specs"


def _make_dispatcher(specs_dir: Path, *, pagination: PaginationConfig) -> Dispatcher:
    index = SpecLoader(str(specs_dir), "20.99", read_write=True).load()
    auth = VManageAuth(
        host="vm.test", port=8443, username="a", password="b",
        verify_ssl=False, use_jwt=True,
    )
    auth._jwt_token = "fake"
    auth._xsrf_token = "fake"
    auth._token_expires_at = 1e18

    d = Dispatcher(
        base_url="https://vm.test:8443/dataservice",
        auth=auth,
        verify_ssl=False,
        pagination=pagination,
    )
    d.set_index(index)
    return d


@pytest.mark.asyncio
async def test_dispatcher_scroll_full_drain(tmp_path):
    d = _make_dispatcher(tmp_path, pagination=PaginationConfig())
    action = next(a for a in d._index.by_action_name if "alarm" in a.lower())

    with respx.mock(assert_all_called=True) as router:
        route = router.get("https://vm.test:8443/dataservice/alarms")
        route.mock(side_effect=[
            httpx.Response(200, json={
                "data": [{"i": 1}],
                "pageInfo": {"scrollId": "c1", "hasMoreData": True, "count": 1, "totalCount": 2},
            }),
            httpx.Response(200, json={
                "data": [{"i": 2}],
                "pageInfo": {"scrollId": "c2", "hasMoreData": False, "count": 1, "totalCount": 2},
            }),
        ])

        result = await d.call(action, {})

    assert result["data"] == [{"i": 1}, {"i": 2}]
    assert result["pagination"]["pages_fetched"] == 2
    assert result["pagination"]["truncated"] is False
    assert route.calls[1].request.url.params["scrollId"] == "c1"


@pytest.mark.asyncio
async def test_dispatcher_offset_truncated_via_override(tmp_path):
    d = _make_dispatcher(tmp_path, pagination=PaginationConfig(max_pages=5))
    action = next(a for a in d._index.by_action_name if "device" in a.lower())

    with respx.mock(assert_all_called=True) as router:
        route = router.get("https://vm.test:8443/dataservice/devices")
        route.mock(return_value=httpx.Response(200, json={"data": list(range(10))}))

        result = await d.call(action, {"pageSize": 10, "_max_pages": 2})

    assert result["pagination"]["truncated"] is True
    assert result["pagination"]["next_cursor"] == {"page": 3, "pageSize": 10}
    assert len(route.calls) == 2
    # Reserved params must never reach the wire.
    for call in route.calls:
        assert "_max_pages" not in call.request.url.params
        assert "_page_size" not in call.request.url.params
        assert "_pagination" not in call.request.url.params


@pytest.mark.asyncio
async def test_dispatcher_opt_out_returns_raw_first_page(tmp_path):
    d = _make_dispatcher(tmp_path, pagination=PaginationConfig())
    action = next(a for a in d._index.by_action_name if "alarm" in a.lower())

    with respx.mock(assert_all_called=True) as router:
        router.get("https://vm.test:8443/dataservice/alarms").mock(
            return_value=httpx.Response(200, json={
                "data": [{"i": 1}],
                "pageInfo": {"scrollId": "c1", "hasMoreData": True},
            })
        )
        result = await d.call(action, {"_pagination": "off"})

    # Raw shape — no wrapper.
    assert "pagination" not in result
    assert result["pageInfo"]["scrollId"] == "c1"


@pytest.mark.asyncio
async def test_dispatcher_disabled_globally(tmp_path):
    d = _make_dispatcher(tmp_path, pagination=PaginationConfig(enabled=False))
    action = next(a for a in d._index.by_action_name if "alarm" in a.lower())

    with respx.mock(assert_all_called=True) as router:
        router.get("https://vm.test:8443/dataservice/alarms").mock(
            return_value=httpx.Response(200, json={
                "data": [{"i": 1}],
                "pageInfo": {"scrollId": "c1", "hasMoreData": True},
            })
        )
        result = await d.call(action, {})

    assert "pagination" not in result


@pytest.mark.asyncio
async def test_dispatcher_non_paginated_op_unchanged(tmp_path):
    d = _make_dispatcher(tmp_path, pagination=PaginationConfig())
    action = next(a for a in d._index.by_action_name if "single" in a.lower())

    with respx.mock(assert_all_called=True) as router:
        router.get("https://vm.test:8443/dataservice/single").mock(
            return_value=httpx.Response(200, json={"hello": "world"})
        )
        result = await d.call(action, {})

    assert result == {"hello": "world"}


@pytest.mark.asyncio
async def test_dispatcher_user_supplied_cursor_resumes(tmp_path):
    d = _make_dispatcher(tmp_path, pagination=PaginationConfig())
    action = next(a for a in d._index.by_action_name if "alarm" in a.lower())

    with respx.mock(assert_all_called=True) as router:
        route = router.get("https://vm.test:8443/dataservice/alarms")
        route.mock(return_value=httpx.Response(200, json={
            "data": [{"i": 99}],
            "pageInfo": {"scrollId": "cN", "hasMoreData": False},
        }))
        await d.call(action, {"scrollId": "resume-from-here"})

    assert route.calls[0].request.url.params["scrollId"] == "resume-from-here"
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `uv run pytest tests/test_pagination.py -v`
Expected: FAIL because `Dispatcher.__init__()` does not accept `pagination` and the routing logic does not exist yet.

- [ ] **Step 3: Update Dispatcher**

In `sdwan_mcp/dispatcher.py`:

Add imports near the top:

```python
from .config import PaginationConfig
from .pagination import OffsetPaginator, Paginator, ScrollPaginator
```

Define a module-level constant tuple for reserved keys (just below the imports):

```python
_RESERVED_PAGINATION_KEYS = ("_pagination", "_max_pages", "_page_size")


def _pick_paginator(style: str | None) -> Paginator | None:
    if style == "scroll":
        return ScrollPaginator()
    if style == "offset":
        return OffsetPaginator()
    return None
```

Replace the `__init__` signature and body with:

```python
    def __init__(
        self,
        base_url: str,
        auth: VManageAuth,
        verify_ssl: bool = False,
        timeout: float = 30.0,
        pagination: PaginationConfig | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._auth = auth
        self._index: SpecIndex | None = None
        self._pagination_cfg = pagination or PaginationConfig()

        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            verify=verify_ssl,
            timeout=timeout,
            follow_redirects=False,
        )
```

Modify `_execute_with_retry` to strip reserved params and route to a paginator
when applicable. Replace the existing body with:

```python
    async def _execute_with_retry(self, op: OperationSpec, params: dict) -> dict:
        """
        Proactively refresh token, route through a paginator if applicable,
        and re-authenticate once on unexpected session expiry.
        """
        await self._auth.ensure_fresh(self._client)

        clean_params, overrides = _strip_reserved(params)
        opted_out = overrides.get("pagination") == "off"

        paginator = (
            _pick_paginator(op.pagination)
            if (self._pagination_cfg.enabled and not opted_out)
            else None
        )

        if paginator is None:
            response = await self._execute_one_with_retry(op, clean_params)
            return response

        max_pages = int(overrides.get("max_pages") or self._pagination_cfg.max_pages)
        page_size = overrides.get("page_size")
        if page_size is None:
            page_size = self._pagination_cfg.page_size

        return await paginator.paginate(
            op,
            clean_params,
            self._execute_one_with_retry,
            max_pages=max_pages,
            page_size=page_size,
        )

    async def _execute_one_with_retry(self, op: OperationSpec, params: dict) -> dict:
        """One request with the existing session-expiry retry behaviour."""
        response = await self._execute(op, params)
        if isinstance(response, dict) and response.get("_session_expired"):
            print("[dispatcher] Session expired unexpectedly — re-authenticating")
            await self._auth.login(self._client)
            response = await self._execute(op, params)
        return response
```

Add `_strip_reserved` at module bottom (next to `_safe_json`):

```python
def _strip_reserved(params: dict | None) -> tuple[dict, dict]:
    """
    Split reserved underscore keys out of params.

    Returns (clean_params, overrides) where overrides has the un-underscored keys:
      _pagination -> overrides["pagination"]
      _max_pages  -> overrides["max_pages"]
      _page_size  -> overrides["page_size"]
    """
    clean: dict = {}
    overrides: dict = {}
    for key, value in (params or {}).items():
        if key in _RESERVED_PAGINATION_KEYS:
            overrides[key.lstrip("_")] = value
        else:
            clean[key] = value
    return clean, overrides
```

- [ ] **Step 4: Run pagination tests**

Run: `uv run pytest tests/test_pagination.py -v`
Expected: PASS — all unit + integration tests green.

- [ ] **Step 5: Run full test suite to catch regressions**

Run: `uv run pytest -v`
Expected: PASS — including all existing `test_dispatcher.py` and `test_loader.py` tests.

- [ ] **Step 6: Commit**

```bash
git add sdwan_mcp/dispatcher.py tests/test_pagination.py
git commit -m "feat(dispatcher): route paginated ops through ScrollPaginator/OffsetPaginator"
```

---

## Task 7: Tool-description pagination hint

**Files:**
- Modify: `sdwan_mcp/tools.py`
- Create: `tests/test_tools.py`

- [ ] **Step 1: Write failing test for description hint**

Create `tests/test_tools.py`:

```python
from pathlib import Path

import pytest
import yaml

from sdwan_mcp.loader import SpecLoader
from sdwan_mcp.tools import _build_description


@pytest.fixture
def tiny_index(tmp_path: Path):
    version_dir = tmp_path / "specs" / "20.99"
    version_dir.mkdir(parents=True)
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "t", "version": "1.0"},
        "paths": {
            "/alarms": {
                "get": {
                    "tags": ["Monitoring - Alarms"],
                    "operationId": "getAlarms",
                    "parameters": [
                        {"name": "scrollId", "in": "query", "schema": {"type": "string"}},
                    ],
                }
            }
        },
    }
    (version_dir / "ops.yaml").write_text(yaml.safe_dump(spec))
    return SpecLoader(str(tmp_path / "specs"), "20.99", read_write=False).load()


def test_description_includes_pagination_note(tiny_index):
    group = tiny_index.groups[0]
    desc = _build_description(group)
    assert "Pagination:" in desc
    assert "_max_pages" in desc
    assert "_pagination" in desc
```

- [ ] **Step 2: Run test, confirm failure**

Run: `uv run pytest tests/test_tools.py -v`
Expected: FAIL — `"Pagination:"` not in description.

- [ ] **Step 3: Prepend the pagination note**

In `sdwan_mcp/tools.py`, modify `_build_description`. Replace the existing
function body with:

```python
_PAGINATION_HINT = (
    "Pagination: paginated actions auto-stitch up to N pages and return "
    '{data, pagination: {...}}. Override per call with _max_pages, _page_size, '
    'or _pagination: "off".'
)


def _build_description(group: ToolGroup) -> str:
    lines = [group.display_tag, "", _PAGINATION_HINT, "", "Actions:"]

    for op in group.operations:
        path_params = [p for p in op.parameters if p.location == "path"]
        query_params = [p for p in op.parameters if p.location == "query"]

        param_parts = []
        for p in path_params:
            param_parts.append(_format_param(p))
        for p in query_params:
            param_parts.append(_format_param(p))
        if op.has_body:
            param_parts.append(f"body: object — {op.body_description}")

        params_str = ", ".join(param_parts) if param_parts else ""
        summary = op.summary.strip() if op.summary else ""

        lines.append(f"  - {op.action_name}({params_str}) [{op.method.upper()}]")
        if summary:
            lines.append(f"    {summary}")

    lines.append("")
    lines.append("Pass 'action' as one of the action names above.")
    lines.append("Pass 'params' as a dict matching the action's parameter list.")

    return "\n".join(lines)
```

- [ ] **Step 4: Run test**

Run: `uv run pytest tests/test_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sdwan_mcp/tools.py tests/test_tools.py
git commit -m "feat(tools): document pagination reserved params in tool descriptions"
```

---

## Task 8: Wire config through `server.py` + update `config.yaml`

**Files:**
- Modify: `sdwan_mcp/server.py`
- Modify: `config.yaml`

- [ ] **Step 1: Locate the Dispatcher construction in server.py**

Run: `grep -n 'Dispatcher(' /Users/thomas/python/catalyst-sdwan-super-mcp/sdwan_mcp/server.py`
Expected: one match in `build_and_run` (or similar). Open that line.

- [ ] **Step 2: Pass pagination config into the constructor**

Replace the `dispatcher = Dispatcher(...)` line in `server.py` with:

```python
    dispatcher = Dispatcher(
        base_url=cfg.vmanage.base_url,
        auth=auth,
        verify_ssl=cfg.vmanage.verify_ssl,
        pagination=cfg.sdwan.pagination,
    )
```

(Keep any other keyword args that were already there — only add `pagination=`.)

- [ ] **Step 3: Add the pagination block to the shipped config**

In `config.yaml`, inside the `sdwan:` section, after `max_actions_per_tool: 150`:

```yaml
  pagination:
    enabled: true
    max_pages: 5
    page_size: null
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -v`
Expected: PASS.

- [ ] **Step 5: Smoke-load the CLI to confirm no import / startup error**

Run: `uv run sdwan-mcp --help`
Expected: usage banner printed, no traceback.

- [ ] **Step 6: Commit**

```bash
git add sdwan_mcp/server.py config.yaml
git commit -m "feat(server): wire PaginationConfig from yaml into Dispatcher"
```

---

## Task 9: Documentation

**Files:**
- Modify: `docs/architecture/data-flow.md`
- Create: `docs/guides/pagination.md`
- Modify: `mkdocs.yml`
- Modify: `CLAUDE.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update data-flow doc**

In `docs/architecture/data-flow.md`, find the "Tool call" section. After the
`dispatcher.call(...)` step add:

```
  → dispatcher    if op.pagination is set and pagination is enabled:
                    route to ScrollPaginator or OffsetPaginator
                    (calls back into the single-page executor up to max_pages)
                    stitch pages → wrap as {data, pagination, ...rest}
                  else: single request as before
```

- [ ] **Step 2: Create the user-facing guide**

Create `docs/guides/pagination.md`:

````markdown
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
````

- [ ] **Step 3: Register the guide in mkdocs.yml**

In `mkdocs.yml`, under the `Guides:` section of `nav:`, add:

```yaml
      - Pagination: guides/pagination.md
```

(Place it in the same order it appears in this plan — alongside `read-write`
and `tool-splitting`.)

- [ ] **Step 4: Update CLAUDE.md**

In `CLAUDE.md`'s `## Config file` block, add the pagination keys under `sdwan:`:

```yaml
  pagination:
    enabled: true
    max_pages: 5
    page_size: null
```

In the `## Key decisions log` table, append four rows:

```markdown
| Pagination strategy | Hybrid auto-follow + cursor | Common case "just works"; truncation honest, resumable. (#8) |
| Pagination knobs | Config defaults + `_*` reserved params | Server-wide default, per-call override without bloating action params. (#8) |
| Pagination response shape | Always wrap when paginated (`{data, pagination}`) | Predictable signal to LLM that auto-follow ran. (#8) |
| Pagination detection | Spec-load time from param names | Cheap, deterministic; response sanity-check covers drift. (#8) |
```

- [ ] **Step 5: Update CHANGELOG**

Add to `CHANGELOG.md` under `## [Unreleased]` (create the section if missing):

```markdown
### Added
- Response pagination for bulk endpoints. Auto-follows scroll and offset
  endpoint families up to `sdwan.pagination.max_pages` (default 5), then
  surfaces a resumable cursor under `pagination.next_cursor`. Per-call
  overrides via `_max_pages`, `_page_size`, `_pagination` params. (#8)
```

- [ ] **Step 6: Build docs locally to catch typos**

Run: `uv run mkdocs build --strict`
Expected: build succeeds with no warnings.

- [ ] **Step 7: Commit**

```bash
git add docs/architecture/data-flow.md docs/guides/pagination.md mkdocs.yml CLAUDE.md CHANGELOG.md
git commit -m "docs(pagination): user guide, data-flow update, CLAUDE.md, CHANGELOG"
```

---

## Task 10: Final verification

**Files:** none — verification only.

- [ ] **Step 1: Run the full suite**

Run: `uv run pytest -v`
Expected: PASS, no regressions.

- [ ] **Step 2: Lint**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: clean (run `uv run ruff format .` if format-check fails).

- [ ] **Step 3: Confirm CLI still boots**

Run: `uv run sdwan-mcp --version`
Expected: prints the version, no traceback.

- [ ] **Step 4: Confirm docs build**

Run: `uv run mkdocs build --strict`
Expected: build succeeds.

- [ ] **Step 5: Commit any formatting fixups**

```bash
git status
# If ruff format modified files, stage and commit them.
git add -A
git commit -m "style: ruff format" || echo "nothing to commit"
```

- [ ] **Step 6: Push and open PR**

```bash
git push -u origin HEAD
gh pr create --title "feat: response pagination for bulk endpoints (#8)" --body "$(cat <<'EOF'
## Summary
- Auto-follows `scroll` and `offset` paginated vManage endpoints up to a configurable cap.
- Stitched responses returned as `{data, pagination: {pages_fetched, truncated, next_cursor}, ...}`.
- Per-call overrides via reserved `_max_pages`, `_page_size`, `_pagination` params.

Closes #8.

## Test plan
- [ ] `uv run pytest -v` green locally
- [ ] `uv run mkdocs build --strict` green locally
- [ ] Manual smoke against the DevNet sandbox: call a paginated action and an opted-out call

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
