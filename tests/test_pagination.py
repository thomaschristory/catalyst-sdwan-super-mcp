"""Tests for sdwan_mcp.pagination — stitch helper, scroll/offset paginators."""

from __future__ import annotations

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
