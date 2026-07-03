"""Tests for the request dispatcher — auth, param routing, retry."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from sdwan_mcp.auth import VManageAuth
from sdwan_mcp.dispatcher import Dispatcher, _stats_db_hint
from sdwan_mcp.loader import OperationSpec, SpecLoader, ToolGroup


@pytest.fixture
def dispatcher(specs_dir: Path) -> Dispatcher:
    index = SpecLoader(str(specs_dir), "20.99", read_write=True).load()
    auth = VManageAuth(
        host="vm.test",
        port=8443,
        username="admin",
        password="pwd",
        verify_ssl=False,
        use_jwt=True,
    )
    # Pre-populate auth state so we don't need to mock /j_security_check.
    auth._jwt_token = "fake-jwt"
    auth._xsrf_token = "fake-xsrf"
    auth._token_expires_at = 1e18

    d = Dispatcher(
        base_url="https://vm.test:8443/dataservice",
        auth=auth,
        verify_ssl=False,
    )
    d.set_index(index)
    return d


@pytest.mark.asyncio
async def test_dispatcher_substitutes_path_params(dispatcher: Dispatcher) -> None:
    with respx.mock(assert_all_called=True) as router:
        route = router.get("https://vm.test:8443/dataservice/devices/10.0.0.1/info").mock(
            return_value=httpx.Response(200, json={"deviceId": "10.0.0.1"})
        )
        result = await dispatcher.call("get_device_details_info", {"deviceId": "10.0.0.1"})

    assert route.called
    assert result == {"deviceId": "10.0.0.1"}


@pytest.mark.asyncio
async def test_dispatcher_encodes_path_params(dispatcher: Dispatcher) -> None:
    """Path-param values are percent-encoded so separators can't reshape the
    request path (#55 L3). A traversal payload stays a single, escaped segment."""
    with respx.mock(assert_all_called=True) as router:
        route = router.get("https://vm.test:8443/dataservice/devices/a%2F..%2Fadmin/info").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        await dispatcher.call("get_device_details_info", {"deviceId": "a/../admin"})

    # raw_path preserves wire encoding (.path decodes it). The '/' is escaped to
    # %2F, so the value stays one segment — no traversal to a sibling path. The
    # respx route above only matched because the request was sent encoded.
    raw = route.calls.last.request.url.raw_path
    assert b"%2F" in raw
    assert b"/devices/a/../admin/" not in raw


@pytest.mark.asyncio
async def test_dispatcher_leaves_ordinary_ids_untouched(dispatcher: Dispatcher) -> None:
    """Unreserved chars (e.g. a dotted device id) pass through unencoded."""
    with respx.mock(assert_all_called=True) as router:
        router.get("https://vm.test:8443/dataservice/devices/10.0.0.1/info").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        result = await dispatcher.call("get_device_details_info", {"deviceId": "10.0.0.1"})
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_dispatcher_routes_query_params(dispatcher: Dispatcher) -> None:
    with respx.mock(assert_all_called=True) as router:
        route = router.get("https://vm.test:8443/dataservice/devices").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        await dispatcher.call("get_device_details_devices", {"site-id": "500"})

    assert route.calls.last.request.url.params["site-id"] == "500"


@pytest.mark.asyncio
async def test_dispatcher_missing_path_param_returns_error(dispatcher: Dispatcher) -> None:
    result = await dispatcher.call("get_device_details_info", {})
    assert isinstance(result, dict)
    assert result.get("error") is True
    assert "deviceId" in result["message"]


@pytest.mark.asyncio
async def test_dispatcher_unknown_action_returns_error(dispatcher: Dispatcher) -> None:
    result = await dispatcher.call("does_not_exist", {})
    assert isinstance(result, dict)
    assert result.get("error") is True
    assert "Unknown action" in result["message"]


@pytest.mark.asyncio
async def test_dispatcher_post_routes_body(dispatcher: Dispatcher) -> None:
    with respx.mock(assert_all_called=True) as router:
        route = router.post("https://vm.test:8443/dataservice/devices/abc/config").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        await dispatcher.call(
            "post_device_actions_config",
            {"deviceId": "abc", "name": "edge-1"},
        )

    body = route.calls.last.request.content.decode()
    assert "edge-1" in body
    # deviceId must be consumed as a path param, not echoed in the body
    assert '"deviceId"' not in body


# ---------------------------------------------------------------------------
# Statistics-database error hint (#56)
# ---------------------------------------------------------------------------

REST0001_BODY = {
    "error": {
        "message": "Server error",
        "code": "REST0001",
        "details": "vManage server experience an unexpected error",
    }
}


def _op(path: str, method: str = "get") -> OperationSpec:
    return OperationSpec(
        operation_id="x",
        action_name="x",
        summary="",
        method=method,
        path=path,
        tag="t",
    )


def test_stats_hint_fires_on_rest0001_code() -> None:
    """REST0001 in the body triggers the confident hint regardless of path."""
    hint = _stats_db_hint(_op("/device/monitor"), 500, REST0001_BODY)
    assert hint is not None
    assert hint.startswith("vManage returned REST0001")  # confident lead-in


def test_stats_hint_points_get_query_form_at_post_variant() -> None:
    """A REST0001 on the GET `?query=` form names the POST fix, not the DB guess (#62)."""
    from sdwan_mcp.loader import ParameterSpec

    op = OperationSpec(
        operation_id="getStatDataRawData",
        action_name="get_bfd",
        summary="",
        method="get",
        path="/statistics/bfd",
        tag="Monitoring - BFD",
        parameters=[ParameterSpec(name="query", location="query")],
    )
    hint = _stats_db_hint(op, 500, REST0001_BODY)
    assert hint is not None
    assert "POST variant" in hint
    assert "read-only mode" in hint


def test_stats_hint_path_only_is_hedged() -> None:
    """A 500 on /statistics/* without REST0001 hedges — it might be a
    validation/permission error, not a stats-DB outage (review of PR #59)."""
    hint = _stats_db_hint(_op("/statistics/system/status"), 500, {"error": "opaque"})
    assert hint is not None
    assert "validation or permission error" in hint
    assert "REST0001" not in hint


def test_stats_hint_silent_on_unrelated_500() -> None:
    """A 500 that is neither a stats path nor REST0001 gets no hint."""
    assert _stats_db_hint(_op("/devices"), 500, {"error": {"code": "OTHER"}}) is None


def test_stats_hint_silent_on_non_500() -> None:
    """Only 500s are annotated — a 404 on a stats path is left untouched."""
    assert _stats_db_hint(_op("/statistics/system/status"), 404, REST0001_BODY) is None


@pytest.mark.asyncio
async def test_dispatcher_annotates_stats_db_500(dispatcher: Dispatcher) -> None:
    """End-to-end: a REST0001 500 surfaces the hint alongside the raw error."""
    with respx.mock(assert_all_called=True) as router:
        router.get("https://vm.test:8443/dataservice/devices").mock(
            return_value=httpx.Response(500, json=REST0001_BODY)
        )
        result = await dispatcher.call("get_device_details_devices", {})

    assert isinstance(result, dict)
    assert result["error"] is True
    assert result["status_code"] == 500
    assert result["body"] == REST0001_BODY  # raw body preserved
    assert "hint" in result and "statistics" in result["hint"].lower()


# ---------------------------------------------------------------------------
# Tool-scoped dispatch: a colliding action_name routes to the calling tool (#65)
# ---------------------------------------------------------------------------


def _colliding_dispatcher() -> Dispatcher:
    """Two tools both expose action `get_bgp`, pointing at different paths."""
    op_a = OperationSpec(
        operation_id="a", action_name="get_bgp", summary="", method="get", path="/a/bgp", tag="t"
    )
    op_b = OperationSpec(
        operation_id="b", action_name="get_bgp", summary="", method="get", path="/b/bgp", tag="t"
    )
    index = SpecLoader._build_index(
        [
            ToolGroup(name="tool_a", display_tag="A", operations=[op_a]),
            ToolGroup(name="tool_b", display_tag="B", operations=[op_b]),
        ]
    )
    auth = VManageAuth(
        host="vm.test", port=8443, username="admin", password="pwd", verify_ssl=False, use_jwt=True
    )
    auth._jwt_token = "fake-jwt"
    auth._xsrf_token = "fake-xsrf"
    auth._token_expires_at = 1e18
    d = Dispatcher(base_url="https://vm.test:8443/dataservice", auth=auth, verify_ssl=False)
    d.set_index(index)
    return d


@pytest.mark.asyncio
async def test_dispatcher_routes_colliding_action_to_calling_tool() -> None:
    """`get_bgp` on tool_b must hit /b/bgp, not tool_a's /a/bgp. Regression for
    the #65 misroute where the global index kept only the first occurrence."""
    d = _colliding_dispatcher()
    with respx.mock(assert_all_called=True) as router:
        route_a = router.get("https://vm.test:8443/dataservice/a/bgp").mock(
            return_value=httpx.Response(200, json={"tool": "a"})
        )
        route_b = router.get("https://vm.test:8443/dataservice/b/bgp").mock(
            return_value=httpx.Response(200, json={"tool": "b"})
        )
        # Both directions: each tool resolves its OWN op, neither clobbers the other.
        result_b = await d.call("get_bgp", {}, tool_name="tool_b")
        result_a = await d.call("get_bgp", {}, tool_name="tool_a")

    assert route_a.called and route_b.called
    assert result_a == {"tool": "a"}
    assert result_b == {"tool": "b"}


@pytest.mark.asyncio
async def test_dispatcher_unknown_tool_name_fails_safe() -> None:
    """A provided-but-unknown tool_name returns the not-found error rather than
    silently degrading to the lossy flat index (#65 review hardening)."""
    d = _colliding_dispatcher()
    result = await d.call("get_bgp", {}, tool_name="no_such_tool")
    assert isinstance(result, dict)
    assert result.get("error") is True
    assert "Unknown action" in result["message"]


@pytest.mark.asyncio
async def test_dispatcher_no_hint_on_ordinary_500(dispatcher: Dispatcher) -> None:
    """A non-stats 500 stays clean — no spurious hint key."""
    with respx.mock(assert_all_called=True) as router:
        router.get("https://vm.test:8443/dataservice/devices").mock(
            return_value=httpx.Response(500, json={"error": {"code": "OTHER"}})
        )
        result = await dispatcher.call("get_device_details_devices", {})

    assert isinstance(result, dict)
    assert result["error"] is True
    assert "hint" not in result


# ---------------------------------------------------------------------------
# POST body: top-level convention + defensive `body`-wrapper unwrap (#78)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatcher_unwraps_lone_body_wrapper(dispatcher: Dispatcher) -> None:
    """A caller that nested the whole payload under a lone `body` key (the shape
    the old `body: object` schema implied) must not double-wrap: the dispatcher
    unwraps it so vManage sees the fields at the top level (#78)."""
    with respx.mock(assert_all_called=True) as router:
        route = router.post("https://vm.test:8443/dataservice/devices/abc/config").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        await dispatcher.call(
            "post_device_actions_config",
            {"deviceId": "abc", "body": {"name": "edge-1"}},
        )

    body = route.calls.last.request.content.decode()
    assert '"name"' in body and "edge-1" in body
    # The literal `body` wrapper must NOT reach vManage.
    assert '"body"' not in body
    assert '"deviceId"' not in body  # still consumed as a path param


@pytest.mark.asyncio
async def test_dispatcher_keeps_body_field_alongside_others(dispatcher: Dispatcher) -> None:
    """Only a *lone* `body` key is unwrapped. A genuine field named `body` next to
    other fields is forwarded verbatim — we don't guess it's a wrapper."""
    with respx.mock(assert_all_called=True) as router:
        route = router.post("https://vm.test:8443/dataservice/devices/abc/config").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        await dispatcher.call(
            "post_device_actions_config",
            {"deviceId": "abc", "body": "literal", "name": "edge-1"},
        )

    body = route.calls.last.request.content.decode()
    assert '"body"' in body and "literal" in body
    assert "edge-1" in body


def test_stats_hint_fires_on_400_stats_validation() -> None:
    """A 400 STATS_VALIDATION0001 is the post-auth query-shape signal: name the
    top-level convention and the accepted fields (#78)."""
    body = {"error": {"message": "Invalid query.", "code": "STATS_VALIDATION0001"}}
    hint = _stats_db_hint(_op("/statistics/interface/aggregation", "post"), 400, body)
    assert hint is not None
    assert "STATS_VALIDATION0001" in hint
    assert "top level" in hint.lower()
    assert "body" in hint.lower()


def test_stats_hint_silent_on_other_400() -> None:
    """An unrelated 400 (different error code) gets no stats hint."""
    assert _stats_db_hint(_op("/devices"), 400, {"error": {"code": "OTHER"}}) is None


@pytest.mark.asyncio
async def test_dispatcher_unwraps_lone_body_wrapper_non_dict(dispatcher: Dispatcher) -> None:
    """The lone-`body` unwrap covers non-dict payloads too (e.g. an array body
    nested under `body`) — otherwise the double-wrap persists (#78 review)."""
    with respx.mock(assert_all_called=True) as router:
        route = router.post("https://vm.test:8443/dataservice/devices/abc/config").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        await dispatcher.call(
            "post_device_actions_config",
            {"deviceId": "abc", "body": [{"x": 1}, {"x": 2}]},
        )

    import json as _json

    sent = _json.loads(route.calls.last.request.content.decode())
    assert sent == [{"x": 1}, {"x": 2}]  # array forwarded, not {"body": [...]}


def test_stats_hint_400_rest0001_gets_no_stats_db_hint() -> None:
    """A 400 carrying REST0001 (not STATS_VALIDATION0001) must NOT get the
    stats-DB-disabled hint — that hint is for 500s. No false signal (#78 review)."""
    assert _stats_db_hint(_op("/statistics/x", "post"), 400, REST0001_BODY) is None


def test_stats_hint_500_stats_validation_falls_through_to_500_path() -> None:
    """A 500 carrying STATS_VALIDATION0001 is not the 400 query-shape case; it
    falls through to the 500 handling, not the new 400 hint (#78 review)."""
    body = {"error": {"code": "STATS_VALIDATION0001"}}
    hint = _stats_db_hint(_op("/statistics/x", "post"), 500, body)
    # Not the 400 query-shape hint.
    assert hint is None or "STATS_VALIDATION0001" not in hint


# A representative vManage login page returned on a stale JSESSIONID (#93).
_LOGIN_PAGE_HTML = (
    '<html><head><meta http-equiv="refresh" content="0; url=welcome.html">'
    '</head><body><form method="post" action="/j_security_check"></form></body></html>'
)


@pytest.mark.asyncio
async def test_session_mode_reauths_on_login_page_200(specs_dir: Path) -> None:
    """Legacy session mode (#93): a stale session answers an API call with a
    200 login page (not a 302/401). The dispatcher must detect that, re-login,
    and transparently retry — no restart. Proven end-to-end through call()."""
    index = SpecLoader(str(specs_dir), "20.99", read_write=True).load()
    auth = VManageAuth(
        host="vm.test",
        port=8443,
        username="admin",
        password="pwd",
        verify_ssl=False,
        use_jwt=False,
    )
    # Pre-populate a (now stale) session so headers() works on the first call.
    auth._session_id = "stale"
    auth._xsrf_token = "stale-xsrf"

    d = Dispatcher(base_url="https://vm.test:8443/dataservice", auth=auth, verify_ssl=False)
    d.set_index(index)

    with respx.mock(assert_all_called=True) as router:
        api = router.get("https://vm.test:8443/dataservice/devices/10.0.0.1/info").mock(
            side_effect=[
                httpx.Response(200, html=_LOGIN_PAGE_HTML),  # stale → login page
                httpx.Response(200, json={"deviceId": "10.0.0.1"}),  # after re-login
            ]
        )
        login = router.post("https://vm.test:8443/j_security_check").mock(
            return_value=httpx.Response(
                200, text="", headers={"Set-Cookie": "JSESSIONID=fresh; Path=/"}
            )
        )
        token = router.get("https://vm.test:8443/dataservice/client/token").mock(
            return_value=httpx.Response(200, text="fresh-xsrf")
        )
        result = await d.call("get_device_details_info", {"deviceId": "10.0.0.1"})

    assert result == {"deviceId": "10.0.0.1"}  # retry payload, not the login HTML
    assert api.call_count == 2  # first login page, then success
    assert login.called and token.called  # one full re-login happened
    assert auth._xsrf_token == "fresh-xsrf"  # session state refreshed


@pytest.mark.asyncio
async def test_session_mode_persistent_login_page_returns_error(specs_dir: Path) -> None:
    """If re-login succeeds but the retry is STILL a login page (e.g. a
    concurrent-session limit keeps evicting us), the dispatcher must return a
    real error — never leak the internal `_session_expired` sentinel to the
    caller/LLM (#93 review)."""
    index = SpecLoader(str(specs_dir), "20.99", read_write=True).load()
    auth = VManageAuth(
        host="vm.test",
        port=8443,
        username="admin",
        password="pwd",
        verify_ssl=False,
        use_jwt=False,
    )
    auth._session_id = "stale"
    auth._xsrf_token = "stale-xsrf"

    d = Dispatcher(base_url="https://vm.test:8443/dataservice", auth=auth, verify_ssl=False)
    d.set_index(index)

    with respx.mock(assert_all_called=True) as router:
        api = router.get("https://vm.test:8443/dataservice/devices/10.0.0.1/info").mock(
            return_value=httpx.Response(200, html=_LOGIN_PAGE_HTML)  # always a login page
        )
        router.post("https://vm.test:8443/j_security_check").mock(
            return_value=httpx.Response(
                200, text="", headers={"Set-Cookie": "JSESSIONID=fresh; Path=/"}
            )
        )
        router.get("https://vm.test:8443/dataservice/client/token").mock(
            return_value=httpx.Response(200, text="fresh-xsrf")
        )
        result = await d.call("get_device_details_info", {"deviceId": "10.0.0.1"})

    assert isinstance(result, dict)
    assert result.get("error") is True
    assert "_session_expired" not in result  # sentinel never leaks
    assert "re-authentication" in result["message"].lower()
    assert api.call_count == 2  # bounded: one original + one post-reauth retry


@pytest.mark.asyncio
async def test_session_mode_no_reauth_on_normal_200(specs_dir: Path) -> None:
    """Guard: a normal JSON 200 in session mode must NOT trigger a re-login."""
    index = SpecLoader(str(specs_dir), "20.99", read_write=True).load()
    auth = VManageAuth(
        host="vm.test",
        port=8443,
        username="admin",
        password="pwd",
        verify_ssl=False,
        use_jwt=False,
    )
    auth._session_id = "live"
    auth._xsrf_token = "live-xsrf"

    d = Dispatcher(base_url="https://vm.test:8443/dataservice", auth=auth, verify_ssl=False)
    d.set_index(index)

    with respx.mock(assert_all_called=False) as router:
        router.get("https://vm.test:8443/dataservice/devices/10.0.0.1/info").mock(
            return_value=httpx.Response(200, json={"deviceId": "10.0.0.1"})
        )
        login = router.post("https://vm.test:8443/j_security_check").mock(
            return_value=httpx.Response(200)
        )
        result = await d.call("get_device_details_info", {"deviceId": "10.0.0.1"})

    assert result == {"deviceId": "10.0.0.1"}
    assert not login.called  # no re-login on a healthy response
