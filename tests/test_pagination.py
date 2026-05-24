"""Tests for sdwan_mcp.pagination — stitch helper, scroll/offset paginators."""

from __future__ import annotations

import httpx
import pytest
import respx
import yaml

from sdwan_mcp.auth import VManageAuth
from sdwan_mcp.config import PaginationConfig
from sdwan_mcp.dispatcher import Dispatcher
from sdwan_mcp.loader import OperationSpec, ParameterSpec, SpecLoader
from sdwan_mcp.pagination import (
    OffsetPaginator,
    ScrollPaginator,
    _first_list_key,
    _offset_size_param_name,
    stitch,
)


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
    for i, (more, cursor) in enumerate(zip(with_more, cursors, strict=True)):
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


def _op_with_query_param(name: str) -> OperationSpec:
    return OperationSpec(
        operation_id="x",
        action_name="x",
        summary="",
        method="get",
        path="/x",
        tag="t",
        parameters=[ParameterSpec(name=name, location="query")],
        has_body=False,
        pagination="offset",
    )


def test_offset_size_param_name_prefers_page_size():
    op = OperationSpec(
        operation_id="x", action_name="x", summary="", method="get", path="/x", tag="t",
        parameters=[
            ParameterSpec(name="pageSize", location="query"),
            ParameterSpec(name="count", location="query"),
        ],
        has_body=False,
        pagination="offset",
    )
    assert _offset_size_param_name(op) == "pageSize"


def test_offset_size_param_name_falls_back_to_count():
    assert _offset_size_param_name(_op_with_query_param("count")) == "count"


def test_offset_size_param_name_falls_back_to_limit():
    assert _offset_size_param_name(_op_with_query_param("limit")) == "limit"


def test_offset_size_param_name_default_when_none_declared():
    op = OperationSpec(
        operation_id="x", action_name="x", summary="", method="get", path="/x", tag="t",
        parameters=[],
        has_body=False,
        pagination="offset",
    )
    assert _offset_size_param_name(op) == "pageSize"


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


# ---------------------------------------------------------------------------
# Dispatcher integration tests
# ---------------------------------------------------------------------------


def _paginated_spec_dir(tmp_path):
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


def _make_dispatcher(specs_dir, *, pagination):
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
    d = _make_dispatcher(_paginated_spec_dir(tmp_path), pagination=PaginationConfig())
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
    d = _make_dispatcher(_paginated_spec_dir(tmp_path), pagination=PaginationConfig(max_pages=5))
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
    d = _make_dispatcher(_paginated_spec_dir(tmp_path), pagination=PaginationConfig())
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
    d = _make_dispatcher(_paginated_spec_dir(tmp_path), pagination=PaginationConfig(enabled=False))
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
    d = _make_dispatcher(_paginated_spec_dir(tmp_path), pagination=PaginationConfig())
    action = next(a for a in d._index.by_action_name if "single" in a.lower())

    with respx.mock(assert_all_called=True) as router:
        router.get("https://vm.test:8443/dataservice/single").mock(
            return_value=httpx.Response(200, json={"hello": "world"})
        )
        result = await d.call(action, {})

    assert result == {"hello": "world"}


@pytest.mark.asyncio
async def test_dispatcher_user_supplied_cursor_resumes(tmp_path):
    d = _make_dispatcher(_paginated_spec_dir(tmp_path), pagination=PaginationConfig())
    action = next(a for a in d._index.by_action_name if "alarm" in a.lower())

    with respx.mock(assert_all_called=True) as router:
        route = router.get("https://vm.test:8443/dataservice/alarms")
        route.mock(return_value=httpx.Response(200, json={
            "data": [{"i": 99}],
            "pageInfo": {"scrollId": "cN", "hasMoreData": False},
        }))
        await d.call(action, {"scrollId": "resume-from-here"})

    assert route.calls[0].request.url.params["scrollId"] == "resume-from-here"
