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
    All requests: Cookie: JSESSIONID=...
                  X-XSRF-TOKEN: {xsrfToken}

Set use_jwt: false in config.yaml to force session mode.
"""

from __future__ import annotations

import contextlib
import time

import httpx

# Refresh JWT this many seconds before it actually expires
REFRESH_MARGIN_SECONDS = 120

# Assume this token lifetime if vManage doesn't tell us (30 min is the default)
DEFAULT_TOKEN_LIFETIME_SECONDS = 1800


class VManageAuth:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        verify_ssl: bool = False,
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

    async def login(self, client: httpx.AsyncClient) -> None:
        """Authenticate and populate internal token state."""
        if not self._username or not self._password:
            raise RuntimeError(
                "vManage credentials are not set.\n"
                "Set VMANAGE_USERNAME and VMANAGE_PASSWORD in your .env file."
            )
        if self._use_jwt:
            await self._login_jwt(client)
        else:
            await self._login_session(client)

    async def ensure_fresh(self, client: httpx.AsyncClient) -> None:
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

        In session mode we rely on httpx's automatic cookie jar (the AsyncClient
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

    def is_session_expired(self, response: httpx.Response) -> bool:
        """
        Detect session expiry — vManage returns a 302 redirect to welcome.html
        when the session is invalidated, or 401 for JWT expiry.
        """
        if response.status_code == 302:
            location = response.headers.get("location", "")
            if "welcome.html" in location:
                return True
        return response.status_code == 401

    async def logout(self, client: httpx.AsyncClient) -> None:
        """Cleanly release the session on the server side (best effort)."""
        with contextlib.suppress(Exception):
            await client.post(f"{self._base_url}/logout", headers=self.headers())

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    async def _login_jwt(self, client: httpx.AsyncClient) -> None:
        """JWT login — single call returns both tokens (20.18.1+)."""
        try:
            response = await client.post(
                f"{self._base_url}/j_security_check",
                data={
                    "j_username": self._username,
                    "j_password": self._password,
                },
            )
        except httpx.ConnectError as e:
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
                f"Try setting use_jwt: false in config.yaml for older versions.\n"
                f"Response: {response.text}"
            ) from e

        # Record expiry time — use expiresIn from response if available
        lifetime = DEFAULT_TOKEN_LIFETIME_SECONDS
        if "expiresIn" in data:
            with contextlib.suppress(ValueError, TypeError):
                lifetime = int(data["expiresIn"])
        self._token_expires_at = time.monotonic() + lifetime
        print(f"[auth] JWT login successful (token valid for ~{lifetime}s)")

    async def _login_session(self, client: httpx.AsyncClient) -> None:
        """Session-based login — two-step: JSESSIONID then XSRF token."""
        try:
            response = await client.post(
                f"{self._base_url}/j_security_check",
                data={
                    "j_username": self._username,
                    "j_password": self._password,
                },
            )
        except httpx.ConnectError as e:
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
