"""Unit tests for VManageAuth session-expiry detection (#93)."""

from __future__ import annotations

import httpx

from sdwan_mcp.auth import VManageAuth

# A representative vManage login page: an expired JSESSIONID makes the server
# answer an authenticated API call with a 2xx carrying this HTML instead of a
# clean 302/401 (legacy session mode, 20.15). Markers: the welcome.html redirect
# and the /j_security_check form action.
LOGIN_PAGE_HTML = (
    '<html><head><meta http-equiv="refresh" content="0; url=welcome.html">'
    '</head><body><form method="post" action="/j_security_check">'
    '<input name="j_username"><input name="j_password"></form></body></html>'
)


def _auth(use_jwt: bool = False) -> VManageAuth:
    return VManageAuth(
        host="vm.test",
        port=8443,
        username="admin",
        password="pwd",
        verify_ssl=False,
        use_jwt=use_jwt,
    )


def test_expired_on_302_welcome() -> None:
    resp = httpx.Response(302, headers={"Location": "/welcome.html"})
    assert _auth().is_session_expired(resp) is True


def test_expired_on_401() -> None:
    assert _auth().is_session_expired(httpx.Response(401)) is True


def test_expired_on_login_page_200() -> None:
    """The core #93 case: a 200 whose body is the login page is a stale session."""
    resp = httpx.Response(200, html=LOGIN_PAGE_HTML)
    assert _auth().is_session_expired(resp) is True


def test_not_expired_on_normal_json_200() -> None:
    resp = httpx.Response(200, json={"data": [{"deviceId": "10.0.0.1"}]})
    assert _auth().is_session_expired(resp) is False


def test_not_expired_on_plain_text_200() -> None:
    """A plain-text success (e.g. the /client/token XSRF value) is not a login page."""
    resp = httpx.Response(200, text="a1b2c3-fake-xsrf-token")
    assert _auth().is_session_expired(resp) is False


def test_not_expired_on_unrelated_html_200() -> None:
    """Fail-safe: arbitrary HTML without the vManage login markers is NOT treated
    as expiry — we don't want to re-auth (and mask) on any HTML-returning body."""
    resp = httpx.Response(200, html="<html><body>report export</body></html>")
    assert _auth().is_session_expired(resp) is False


def test_not_expired_on_device_config_html_mentioning_welcome() -> None:
    """The RO endpoint GET /device/config/html renders a device config as HTML
    whose text can incidentally contain the bare string 'welcome.html' (e.g. an
    ip-http redirect line). The anchored marker must NOT trip on that — otherwise
    a valid config response is discarded and a spurious re-login fires (#93 review)."""
    config_html = (
        "<html><body><pre>ip http client source-interface Loopback0\n"
        "ip http redirect url http://portal.example.com/welcome.html\n"
        "hostname edge-01</pre></body></html>"
    )
    resp = httpx.Response(200, html=config_html)
    assert _auth().is_session_expired(resp) is False


def test_expired_on_welcome_redirect_without_form() -> None:
    """A pure meta-refresh redirect to welcome.html (no login form) is still an
    expired session — the redirect is anchored to the refresh url attribute."""
    redirect = '<html><head><meta http-equiv="refresh" content="0; url=welcome.html"></head></html>'
    assert _auth().is_session_expired(httpx.Response(200, html=redirect)) is True


def test_not_expired_on_403() -> None:
    """A 403 is a permission/RBAC denial, not a session timeout — must NOT trigger
    re-auth, which would mask the real error."""
    assert _auth().is_session_expired(httpx.Response(403)) is False


def test_login_page_detected_in_jwt_mode_too() -> None:
    """Detection is auth-mode agnostic — the guard lives on the response shape."""
    resp = httpx.Response(200, html=LOGIN_PAGE_HTML)
    assert _auth(use_jwt=True).is_session_expired(resp) is True
