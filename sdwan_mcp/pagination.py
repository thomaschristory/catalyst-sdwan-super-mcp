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

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from .loader import OperationSpec

Executor = Callable[[OperationSpec, dict[str, Any]], Awaitable[Any]]


class Paginator(Protocol):
    async def paginate(
        self,
        op: OperationSpec,
        params: dict[str, Any],
        executor: Executor,
        max_pages: int,
        page_size: int | None,
    ) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# Stitching helpers
# ---------------------------------------------------------------------------


def _first_list_key(page: dict[str, Any]) -> str | None:
    """Return the first top-level key whose value is a list, or None."""
    if not isinstance(page, dict):
        return None
    for key, value in page.items():
        if isinstance(value, list):
            return str(key)
    return None


def stitch(
    pages: list[dict[str, Any]],
    style: str,
    next_cursor: dict[str, Any] | None,
) -> dict[str, Any]:
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

    stitched_items: list[Any] = []
    for page in pages:
        if isinstance(page, dict):
            items = page.get(list_key)
            if isinstance(items, list):
                stitched_items.extend(items)

    # Preserve page-1 root fields, but strip the per-page list (it's partial)
    # and pageInfo (folded into `pagination`). Stitched list is always exposed
    # under "data" for a predictable envelope.
    out: dict[str, Any] = {
        k: v for k, v in first.items() if k not in {list_key, "pageInfo", "data"}
    }
    out["data"] = stitched_items
    out["pagination"] = {
        "style": style,
        "pages_fetched": len(pages),
        "truncated": next_cursor is not None,
        "next_cursor": next_cursor,
    }
    return out


# ---------------------------------------------------------------------------
# Scroll paginator
# ---------------------------------------------------------------------------


class ScrollPaginator:
    """Elasticsearch-style cursor pagination via scrollId / pageInfo.hasMoreData."""

    async def paginate(
        self,
        op: OperationSpec,
        params: dict[str, Any],
        executor: Executor,
        max_pages: int,
        page_size: int | None,
    ) -> dict[str, Any]:
        del page_size  # irrelevant for scroll
        pages: list[dict[str, Any]] = []
        cursor: str | None = params.get("scrollId")
        current: dict[str, Any] = dict(params)

        while len(pages) < max_pages:
            if cursor is not None:
                current["scrollId"] = cursor
            page = await executor(op, current)
            pages.append(page if isinstance(page, dict) else {})

            info = (page.get("pageInfo") if isinstance(page, dict) else None) or {}
            next_cursor_raw = info.get("scrollId")
            has_more = bool(info.get("hasMoreData"))
            if not has_more or not next_cursor_raw:
                cursor = None
                break
            cursor = str(next_cursor_raw)

        next_cursor_obj = {"scrollId": cursor} if cursor else None
        return stitch(pages, style="scroll", next_cursor=next_cursor_obj)


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
        params: dict[str, Any],
        executor: Executor,
        max_pages: int,
        page_size: int | None,
    ) -> dict[str, Any]:
        pages: list[dict[str, Any]] = []
        current: dict[str, Any] = dict(params)
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

        raw_page = current.get("page")
        page_num = int(raw_page) if raw_page is not None else 1
        next_cursor: dict[str, Any] | None = None

        while len(pages) < max_pages:
            current["page"] = page_num
            if effective_size is not None:
                current[size_key] = effective_size
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
