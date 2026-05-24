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
from typing import Any, TypeAlias

import httpx

from .auth import VManageAuth
from .loader import OperationSpec, SpecIndex

DispatchResult: TypeAlias = dict[str, Any] | list[Any] | str


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

    async def call(self, action_name: str, params: dict[str, Any]) -> DispatchResult:
        """
        Execute an API call for the given derived action name.

        params: flat dict — dispatcher splits into path / query / body
                based on the spec definition.
        """
        if self._index is None:
            raise RuntimeError("SpecIndex not set — call set_index() first")

        op = self._index.by_action_name.get(action_name)
        if op is None:
            return {
                "error": True,
                "message": (
                    f"Unknown action: '{action_name}'. "
                    f"Check the tool description for valid action names."
                ),
            }

        return await self._execute_with_retry(op, params)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _execute_with_retry(
        self, op: OperationSpec, params: dict[str, Any]
    ) -> DispatchResult:
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

    async def _execute(self, op: OperationSpec, raw_params: dict[str, Any]) -> DispatchResult:
        # Split params by location
        path_param_names = {p.name for p in op.parameters if p.location == "path"}
        query_param_names = {p.name for p in op.parameters if p.location == "query"}

        path_params: dict[str, Any] = {}
        query_params: dict[str, Any] = {}
        body_params: dict[str, Any] = {}
        unknown_params: dict[str, Any] = {}

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
                f"[dispatcher] WARNING: unrecognised params for '{op.action_name}': "
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
                    f"Missing required path param(s) for '{op.action_name}': {missing}. "
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


def _safe_json(response: httpx.Response) -> DispatchResult:
    """Try JSON parse; fall back to raw text."""
    try:
        data = response.json()
    except Exception:
        return {"raw": response.text}

    if isinstance(data, (dict, list, str)):
        return data
    return {"raw": str(data)}
