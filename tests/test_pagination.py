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
