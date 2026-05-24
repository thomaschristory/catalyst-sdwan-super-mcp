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
from typing import Protocol

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
