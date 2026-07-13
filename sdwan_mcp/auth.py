"""
auth.py — vManage authentication with JWT token refresh.

Supports two modes:
  JWT (default, recommended for 20.18.1+)
    POST /j_security_check → { token, xsrfToken }
    All requests: Authorization: Bearer {token}
                  X-XSRF-TOKEN: {xsrfToken}
    Token is refreshed proactively when within REFRESH_MARGIN_SECONDS of expiry.

  Session-based (legacy fallback for older vManage)
    POST /j_security_check → JSESSIONID cookie
    GET  /dataservice/client/token → xsrf token
    All requests: X-XSRF-TOKEN: {xsrfToken}
                  (the JSESSIONID cookie is attached automatically by httpx2's
                   cookie jar — we do NOT set a Cookie header ourselves; see
                   headers())

Set use_jwt: false in sdwan-mcp.yaml to force session mode.
"""

from __future__ import annotations

import contextlib
import re
import time

import httpx2

# Refresh JWT this many seconds before it actually expires
REFRESH_MARGIN_SECONDS = 120

# Assume this token lifetime if vManage doesn't tell us (30 min is the default)
DEFAULT_TOKEN_LIFETIME_SECONDS = 1800


# Anchored markers that identify a vManage login page in a response body:
#   - the login form's POST target (j_security_check — a servlet path that never
#     appears in API or device-config data), or
#   - a redirect to welcome.html anchored to a url/href/location attribute.
# Anchoring matters (#93 review): the read-only endpoint GET /device/config/html
# renders a device running-config as HTML whose text can incidentally contain
# the bare string "welcome.html" (e.g. an `ip http` redirect line). A loose
# substring match would misclassify that valid config as an expired session and
# discard it. Requiring the login-form action or an attribute-anchored redirect
# keeps the 2xx-login-page detection fail-safe.
_LOGIN_FORM_RE = re.compile(r"j_security_check", re.IGNORECASE)
_WELCOME_REDIRECT_RE = re.compile(
    r"(?:url|href|location(?:\.href)?)\s*[=:]\s*['\"]?[^'\"<>\s]*welcome\.html",
    re.IGNORECASE,
)


def _looks_like_login_page(response: httpx2.Response) -> bool:
    """True if ``response`` is the vManage HTML login page rather than API data.

    Cheap and conservative: JSON responses are rejected outright by content-type
    before the body is inspected; otherwise the body must look like HTML and
    carry an *anchored* login marker (the form action or a redirect attribute
    pointing at welcome.html). This is the sole signal for the 20.15
    session-timeout case where an expired JSESSIONID yields a 200 login
    page (#93)."""
    content_type = response.headers.get("content-type", "").lower()
    if "json" in content_type:
        return False
    body = response.text or ""
    head = body[:1024].lower()
    if "<html" not in head and "text/html" not in content_type:
        return False
    return bool(_LOGIN_FORM_RE.search(body) or _WELCOME_REDIRECT_RE.search(body))


def require_credentials(username: str, password: str) -> None:
    """Raise a helpful error if vManage credentials are missing.

    Called early at startup (before spec loading) to fail fast, and again in
    ``VManageAuth.login`` as defense-in-depth."""
    if not username or not password:
        raise RuntimeError(
            "vManage credentials are not set.\n"
            "Provide VMANAGE_USERNAME and VMANAGE_PASSWORD via either:\n"
            "  - exported shell environment variables, or\n"
            "  - a .env file in the directory you run the command from "
            "(or next to --config).\n"
            "When launched by an MCP client (e.g. Claude Code), the client "
            "does not inherit your shell exports — set them in the client's "
            "server `env` block instead."
        )


class VManageAuth:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        verify_ssl: bool = True,
        use_jwt: bool = True,
    ):
        self._base_url = f"https://{host}:{port}"
        self._username = username
        self._password = password
        self._verify_ssl = verify_ssl
        self._use_jwt = use_jwt

        # Populated after login()
        self._jwt_token: str = ""
        self._xsrf_token: str = ""
        self._session_id: str = ""

        # Token expiry tracking (JWT only)
        self._token_expires_at: float = 0.0

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    async def login(self, client: httpx2.AsyncClient) -> None:
        """Authenticate and populate internal token state."""
        require_credentials(self._username, self._password)
        if self._use_jwt:
            await self._login_jwt(client)
        else:
            await self._login_session(client)

    async def ensure_fresh(self, client: httpx2.AsyncClient) -> None:
        """
        Proactively refresh JWT token if it's close to expiry.
        Call this before each request in JWT mode.
        No-op in session mode (sessions don't have a predictable expiry time).
        """
        if not self._use_jwt:
            return
        if time.monotonic() >= self._token_expires_at - REFRESH_MARGIN_SECONDS:
            print("[auth] JWT token nearing expiry — refreshing")
            await self._login_jwt(client)

    def headers(self) -> dict[str, str]:
        """
        Return auth headers to inject into every API request.

        In session mode we rely on httpx2's automatic cookie jar (the AsyncClient
        already saw the Set-Cookie from /j_security_check), so we only return
        the XSRF token here. Sending a manual Cookie header alongside the jar
        produces duplicate cookies and vManage rejects the second copy.
        """
        if self._use_jwt:
            if not self._jwt_token:
                raise RuntimeError("Not authenticated — call login() first")
            return {
                "Authorization": f"Bearer {self._jwt_token}",
                "X-XSRF-TOKEN": self._xsrf_token,
            }
        if not self._session_id:
            raise RuntimeError("Not authenticated — call login() first")
        return {
            "X-XSRF-TOKEN": self._xsrf_token,
        }

    def is_session_expired(self, response: httpx2.Response) -> bool:
        """
        Detect session expiry so the dispatcher can re-authenticate and retry.

        Three signals:
          - a 302 redirect to welcome.html (clean session invalidation),
          - a 401 (e.g. JWT expiry), regardless of auth mode,
          - a 2xx whose body is the vManage login page (#93).

        The third signal covers legacy session mode on 20.15: an expired
        JSESSIONID does NOT yield a 302/401 for API calls — vManage answers with
        HTTP 200 carrying the login form (the same "returns the login form
        instead of data" quirk handled at login in ``_login_session``). Without
        this, that HTML is handed back as if it were data and the session stays
        dead until the process restarts. Detection is fail-safe: it keys on the
        vManage login markers in an HTML body, so a genuine JSON/text API success
        can never trip it (see ``_looks_like_login_page``).
        """
        if response.status_code == 302:
            location = response.headers.get("location", "")
            if "welcome.html" in location:
                return True
        if response.status_code == 401:
            return True
        return response.status_code < 400 and _looks_like_login_page(response)

    async def logout(self, client: httpx2.AsyncClient) -> None:
        """Cleanly release the session on the server side (best effort)."""
        with contextlib.suppress(Exception):
            await client.post(f"{self._base_url}/logout", headers=self.headers())

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    async def _login_jwt(self, client: httpx2.AsyncClient) -> None:
        """JWT login — single call returns both tokens (20.18.1+)."""
        try:
            response = await client.post(
                f"{self._base_url}/j_security_check",
                data={
                    "j_username": self._username,
                    "j_password": self._password,
                },
            )
        except httpx2.ConnectError as e:
            raise RuntimeError(
                f"Cannot reach vManage at {self._base_url}.\n"
                f"Check that the host/port are correct and vManage is reachable.\n"
                f"Detail: {e}"
            ) from e

        if response.status_code == 403:
            raise RuntimeError(
                "JWT login failed: access denied (HTTP 403).\n"
                "Check that VMANAGE_USERNAME and VMANAGE_PASSWORD are correct."
            )
        if response.status_code != 200:
            raise RuntimeError(f"JWT login failed: HTTP {response.status_code}\n{response.text}")

        try:
            data = response.json()
            self._jwt_token = data["token"]
            self._xsrf_token = data["xsrfToken"]
        except (KeyError, ValueError) as e:
            raise RuntimeError(
                f"JWT login: unexpected response format — are you on vManage 20.18.1+?\n"
                f"Try setting use_jwt: false in sdwan-mcp.yaml for older versions.\n"
                f"Response: {response.text}"
            ) from e

        # Record expiry time — use expiresIn from response if available
        lifetime = DEFAULT_TOKEN_LIFETIME_SECONDS
        if "expiresIn" in data:
            with contextlib.suppress(ValueError, TypeError):
                lifetime = int(data["expiresIn"])
        self._token_expires_at = time.monotonic() + lifetime
        print(f"[auth] JWT login successful (token valid for ~{lifetime}s)")

    async def _login_session(self, client: httpx2.AsyncClient) -> None:
        """Session-based login — two-step: JSESSIONID then XSRF token."""
        try:
            response = await client.post(
                f"{self._base_url}/j_security_check",
                data={
                    "j_username": self._username,
                    "j_password": self._password,
                },
            )
        except httpx2.ConnectError as e:
            raise RuntimeError(
                f"Cannot reach vManage at {self._base_url}.\n"
                f"Check that the host/port are correct and vManage is reachable.\n"
                f"Detail: {e}"
            ) from e

        if response.status_code not in (200, 302):
            raise RuntimeError(
                f"Session login failed: HTTP {response.status_code}.\n"
                f"Check that VMANAGE_USERNAME and VMANAGE_PASSWORD are correct.\n"
                f"{response.text}"
            )

        # vManage's /j_security_check returns:
        #   success — 200 with EMPTY body (and a Set-Cookie)
        #   failure — 200 with the login form HTML in the body (still sets a cookie!)
        # so we must inspect the body, not just the cookie.
        body = response.text or ""
        if body.strip() and ("<html" in body.lower() or "welcome.html" in body.lower()):
            raise RuntimeError(
                "Session login rejected by vManage. The server returned the login form "
                "instead of an empty success response — usually means wrong credentials, "
                "or the user is locked out / concurrent-session-limited.\n"
                "Check that VMANAGE_USERNAME and VMANAGE_PASSWORD are correct, and wait "
                "a few minutes if you've been retrying quickly."
            )

        # Extract JSESSIONID from Set-Cookie header
        set_cookie = response.headers.get("Set-Cookie", "")
        if "JSESSIONID=" not in set_cookie:
            raise RuntimeError(
                f"Session login: no JSESSIONID in response — login may have been rejected.\n"
                f"Set-Cookie header: {set_cookie or '(empty)'}"
            )
        self._session_id = set_cookie.split("JSESSIONID=")[1].split(";")[0]

        # Step 2: get XSRF token. The AsyncClient's cookie jar already has the
        # JSESSIONID we just received, so we don't need to re-send it manually.
        token_response = await client.get(
            f"{self._base_url}/dataservice/client/token",
        )
        if token_response.status_code != 200:
            raise RuntimeError(f"Failed to retrieve XSRF token: HTTP {token_response.status_code}")
        self._xsrf_token = token_response.text.strip()
        print("[auth] Session login successful")
