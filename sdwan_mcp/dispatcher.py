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

import asyncio
import random
import re
from typing import Any, TypeAlias

import httpx

from .auth import VManageAuth
from .config import PaginationConfig, RetryConfig
from .loader import OperationSpec, SpecIndex
from .pagination import OffsetPaginator, Paginator, ScrollPaginator

_MUTATING_METHODS = frozenset({"post", "put", "delete", "patch"})

_RESERVED_PAGINATION_KEYS = ("_pagination", "_max_pages", "_page_size")


def _pick_paginator(style: str | None) -> Paginator | None:
    if style == "scroll":
        return ScrollPaginator()
    if style == "offset":
        return OffsetPaginator()
    return None


DispatchResult: TypeAlias = dict[str, Any] | list[Any] | str


class Dispatcher:
    def __init__(
        self,
        base_url: str,
        auth: VManageAuth,
        verify_ssl: bool = False,
        timeout: float = 30.0,
        pagination: PaginationConfig | None = None,
        retry: RetryConfig | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._auth = auth
        self._index: SpecIndex | None = None
        self._pagination_cfg = pagination or PaginationConfig()
        self._retry_cfg = retry or RetryConfig()

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
        Proactively refresh token, route through a paginator if applicable,
        and re-authenticate once on unexpected session expiry.
        """
        await self._auth.ensure_fresh(self._client)

        clean_params, overrides = _strip_reserved(params)
        opted_out = overrides.get("pagination") == "off"

        paginator = (
            _pick_paginator(op.pagination)
            if (self._pagination_cfg.enabled and not opted_out)
            else None
        )

        if paginator is None:
            response = await self._execute_one_with_retry(op, clean_params)
            return response

        max_pages_override = overrides.get("max_pages")
        max_pages = (
            int(max_pages_override)
            if max_pages_override is not None
            else self._pagination_cfg.max_pages
        )
        page_size_override = overrides.get("page_size")
        page_size = (
            int(page_size_override)
            if page_size_override is not None
            else self._pagination_cfg.page_size
        )

        return await paginator.paginate(
            op,
            clean_params,
            self._execute_one_with_retry,
            max_pages=max_pages,
            page_size=page_size,
        )

    async def _execute_one_with_retry(
        self, op: OperationSpec, params: dict[str, Any]
    ) -> DispatchResult:
        """One request with the existing session-expiry retry behaviour."""
        response = await self._execute(op, params)
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
            response = await self._send_with_retry(
                method=op.method.upper(),
                url=url,
                params=query_params or None,
                json=body_params if body_params else None,
                headers=headers,
                retryable=self._is_retryable(op.method),
            )
        except httpx.RequestError as e:
            return {"error": True, "message": f"Request failed: {e}"}

        # Detect session expiry — signal caller to re-auth
        if self._auth.is_session_expired(response):
            return {"_session_expired": True}

        if response.is_error:
            body = _safe_json(response)
            result: dict[str, Any] = {
                "error": True,
                "status_code": response.status_code,
                "message": f"HTTP {response.status_code}",
                "body": body,
            }
            hint = _stats_db_hint(op, response.status_code, body)
            if hint:
                result["hint"] = hint
            return result

        return _safe_json(response)

    # ------------------------------------------------------------------
    # Transport-level retry
    # ------------------------------------------------------------------

    def _is_retryable(self, method: str) -> bool:
        if method.lower() in _MUTATING_METHODS:
            return self._retry_cfg.retry_mutating
        return True

    async def _send_with_retry(
        self,
        *,
        method: str,
        url: str,
        params: dict[str, Any] | None,
        json: dict[str, Any] | None,
        headers: dict[str, str],
        retryable: bool,
    ) -> httpx.Response:
        cfg = self._retry_cfg
        attempts = max(1, cfg.max_attempts) if retryable else 1
        last_response: httpx.Response | None = None

        for attempt in range(attempts):
            try:
                response = await self._client.request(
                    method=method,
                    url=url,
                    params=params,
                    json=json,
                    headers=headers,
                )
            except httpx.RequestError:
                if attempt + 1 >= attempts:
                    raise
                await self._sleep_backoff(attempt)
                continue

            if response.status_code in cfg.statuses and attempt + 1 < attempts:
                last_response = response
                await self._sleep_backoff(attempt)
                continue

            return response

        # Loop exits only on exhausted status-code retries (transport errors raise).
        assert last_response is not None
        return last_response

    async def _sleep_backoff(self, attempt: int) -> None:
        cfg = self._retry_cfg
        if cfg.backoff_base <= 0:
            return
        raw = min(cfg.backoff_cap, cfg.backoff_base * (2**attempt))
        # Equal jitter: half fixed, half random in [0, half].
        half = raw / 2
        delay = half + random.uniform(0, half)
        await asyncio.sleep(delay)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error_code(body: Any) -> str:
    """Pull vManage's nested error code (e.g. 'REST0001') out of a response body."""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return str(err.get("code", ""))
    return ""


# Shared list of likely causes + remediation, appended to whichever lead-in fits.
_STATS_DB_CAUSES = (
    "Common causes: the statistics database is disabled or has no data on this "
    "deployment, or the query needs a bounded time window — supply a 'query' "
    "payload (e.g. a rule on entry_time with last_n_hours). Real-time and "
    "device-state endpoints are unaffected. Check Administration > Settings > "
    "Statistics Database on vManage if this persists."
)


def _stats_db_hint(op: OperationSpec, status_code: int, body: Any) -> str | None:
    """Add an actionable hint for the statistics-database 500s seen in #56.

    vManage's ``/dataservice/statistics/*`` family returns HTTP 500 ``REST0001``
    ("vManage server experienced an unexpected error") when the statistics DB is
    disabled/empty or the query lacks a bounded time window, while real-time and
    device-state endpoints on the same server work. The raw 500 is opaque, so we
    annotate it.

    The wording is deliberately tiered to avoid over-claiming: a ``REST0001``
    body is the strong stats-DB signal and gets a confident lead-in, whereas a
    plain 500 on a ``/statistics`` path could equally be a validation or
    permission error, so that lead-in is hedged."""
    if status_code != 500:
        return None
    is_rest0001 = _error_code(body) == "REST0001"
    is_stats_path = op.path.startswith("/statistics")
    if not (is_rest0001 or is_stats_path):
        return None
    if is_rest0001:
        return f"vManage returned REST0001 for this statistics-database query. {_STATS_DB_CAUSES}"
    return (
        "This statistics-database endpoint returned a server-side 500. If this is "
        f"a stats-DB query rather than a validation or permission error: {_STATS_DB_CAUSES}"
    )


def _safe_json(response: httpx.Response) -> DispatchResult:
    """Try JSON parse; fall back to raw text."""
    try:
        data = response.json()
    except Exception:
        return {"raw": response.text}

    if isinstance(data, (dict, list, str)):
        return data
    return {"raw": str(data)}


def _strip_reserved(
    params: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Split reserved underscore keys out of params.

    Returns (clean_params, overrides) where overrides has the un-underscored keys:
      _pagination -> overrides["pagination"]
      _max_pages  -> overrides["max_pages"]
      _page_size  -> overrides["page_size"]
    """
    clean: dict[str, Any] = {}
    overrides: dict[str, Any] = {}
    for key, value in (params or {}).items():
        if key in _RESERVED_PAGINATION_KEYS:
            overrides[key.lstrip("_")] = value
        else:
            clean[key] = value
    return clean, overrides
