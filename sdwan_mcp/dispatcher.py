"""
dispatcher.py — httpx async client for vManage API calls.

Handles:
  - Auth via VManageAuth (JWT or session-based)
  - Proactive JWT refresh before each request
  - Automatic re-login on unexpected session expiry
  - Path param substitution
  - Query vs body param routing based on the spec
"""

from __future__ import annotations

import re

import httpx

from .auth import VManageAuth
from .loader import OperationSpec, SpecIndex


class Dispatcher:
    def __init__(
        self,
        base_url: str,
        auth: VManageAuth,
        verify_ssl: bool = False,
        timeout: float = 30.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._auth = auth
        self._index: SpecIndex | None = None

        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            verify=verify_ssl,
            timeout=timeout,
            # Don't follow redirects automatically — we detect 302 to welcome.html
            # as a session expiry signal
            follow_redirects=False,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Login to vManage. Must be called before any tool invocations."""
        await self._auth.login(self._client)

    async def close(self) -> None:
        """Logout and close the HTTP client."""
        await self._auth.logout(self._client)
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def set_index(self, index: SpecIndex) -> None:
        """Attach the spec index so the dispatcher can resolve operationIds."""
        self._index = index

    async def call(self, operation_id: str, params: dict) -> dict:
        """
        Execute an API call for the given operationId.

        params: flat dict — dispatcher splits into path / query / body
                based on the spec definition.
        """
        if self._index is None:
            raise RuntimeError("SpecIndex not set — call set_index() first")

        op = self._index.by_operation_id.get(operation_id)
        if op is None:
            return {
                "error": True,
                "message": (
                    f"Unknown operationId: '{operation_id}'. "
                    f"Check the tool description for valid action names."
                ),
            }

        return await self._execute_with_retry(op, params)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _execute_with_retry(self, op: OperationSpec, params: dict) -> dict:
        """
        Proactively refresh token if needed, execute request,
        re-authenticate once on unexpected session expiry.
        """
        # Proactive refresh (JWT only — no-op for session mode)
        await self._auth.ensure_fresh(self._client)

        response = await self._execute(op, params)

        # Reactive re-login on unexpected expiry (e.g. server-side invalidation)
        if isinstance(response, dict) and response.get("_session_expired"):
            print("[dispatcher] Session expired unexpectedly — re-authenticating")
            await self._auth.login(self._client)
            response = await self._execute(op, params)

        return response

    async def _execute(self, op: OperationSpec, raw_params: dict) -> dict:
        # Split params by location
        path_param_names = {p.name for p in op.parameters if p.location == "path"}
        query_param_names = {p.name for p in op.parameters if p.location == "query"}

        path_params: dict = {}
        query_params: dict = {}
        body_params: dict = {}
        unknown_params: dict = {}

        for key, value in (raw_params or {}).items():
            if value is None:
                continue
            if key in path_param_names:
                path_params[key] = value
            elif key in query_param_names:
                query_params[key] = value
            elif op.has_body and op.method in ("post", "put", "patch"):
                body_params[key] = value
            else:
                unknown_params[key] = value

        if unknown_params:
            print(
                f"[dispatcher] WARNING: unrecognised params for '{op.operation_id}': "
                f"{list(unknown_params.keys())} — forwarding as query params"
            )
            query_params.update(unknown_params)

        # Substitute path params into URL template
        url = op.path
        for name, value in path_params.items():
            url = url.replace(f"{{{name}}}", str(value))

        # Check for any unresolved path params
        if "{" in url:
            missing = re.findall(r"\{([^}]+)\}", url)
            return {
                "error": True,
                "message": (
                    f"Missing required path param(s) for '{op.operation_id}': {missing}. "
                    f"Provide them in the params dict."
                ),
            }

        headers = {
            "Content-Type": "application/json",
            **self._auth.headers(),
        }

        try:
            response = await self._client.request(
                method=op.method.upper(),
                url=url,
                params=query_params or None,
                json=body_params if body_params else None,
                headers=headers,
            )
        except httpx.RequestError as e:
            return {"error": True, "message": f"Request failed: {e}"}

        # Detect session expiry — signal caller to re-auth
        if self._auth.is_session_expired(response):
            return {"_session_expired": True}

        if response.is_error:
            return {
                "error": True,
                "status_code": response.status_code,
                "message": f"HTTP {response.status_code}",
                "body": _safe_json(response),
            }

        return _safe_json(response)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_json(response: httpx.Response) -> dict | list | str:
    """Try JSON parse; fall back to raw text."""
    try:
        return response.json()
    except Exception:
        return {"raw": response.text}
