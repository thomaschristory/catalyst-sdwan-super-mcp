"""
dispatcher.py — httpx2 async client for vManage API calls.

Handles:
  - Auth via VManageAuth (JWT or session-based)
  - Proactive JWT refresh before each request
  - Automatic re-login on unexpected session expiry
  - Path param substitution
  - Query vs body param routing based on the spec
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import sys
import time
from typing import Any, TypeAlias
from urllib.parse import quote

import httpx2

from .auth import VManageAuth
from .config import DebugConfig, PaginationConfig, RetryConfig
from .loader import OperationSpec, SpecIndex
from .pagination import OffsetPaginator, Paginator, ScrollPaginator

_MUTATING_METHODS = frozenset({"post", "put", "delete", "patch"})

_RESERVED_PAGINATION_KEYS = ("_pagination", "_max_pages", "_page_size")

# Headers whose values are auth secrets — redacted from debug capture by
# default (#72). Compared case-insensitively. Covers both request-side
# (Authorization / X-XSRF-TOKEN / Cookie) and response-side (Set-Cookie).
_SENSITIVE_HEADERS = frozenset(
    {"authorization", "x-xsrf-token", "cookie", "set-cookie", "proxy-authorization"}
)
_REDACTED = "<redacted>"

# Body/query keys whose VALUES are credentials and must be masked when redaction
# is on (#72). Header redaction alone is not enough: several reachable GETs
# return a live token *in the response body* — e.g. GET /client/token
# (getCsrfToken) yields {"token": "<live XSRF token>"}, and the cloud-services
# access-token endpoints do the same. Matched case-insensitively as a substring
# of the key, so this also catches xsrfToken / sessionId / apiKey etc.
_SENSITIVE_KEY_RE = re.compile(
    r"token|secret|password|passwd|passphrase|credential|xsrf|cookie|"
    r"api[_-]?key|session[_-]?id|authorization|private[_-]?key",
    re.IGNORECASE,
)

# Captured bodies are truncated past this many serialized chars so an opt-in
# debug session can't silently double or overflow a tool result with a large
# upstream payload (#72).
_MAX_DEBUG_BODY_CHARS = 20_000


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
        debug: DebugConfig | None = None,
        transport: httpx2.AsyncBaseTransport | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._auth = auth
        self._index: SpecIndex | None = None
        self._pagination_cfg = pagination or PaginationConfig()
        self._retry_cfg = retry or RetryConfig()
        self._debug_cfg = debug or DebugConfig()

        self._client = httpx2.AsyncClient(
            base_url=self._base_url,
            verify=verify_ssl,
            timeout=timeout,
            # Don't follow redirects automatically — we detect 302 to welcome.html
            # as a session expiry signal
            follow_redirects=False,
            # None → httpx2 picks its default transport. Tests inject a MockTransport.
            transport=transport,
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
        """Attach the spec index so the dispatcher can resolve derived action names."""
        self._index = index

    async def call(
        self, action_name: str, params: dict[str, Any], tool_name: str | None = None
    ) -> DispatchResult:
        """
        Execute an API call for the given derived action name.

        tool_name: the calling tool's name. Action names are unique only within
                   a tool, so this scopes resolution to that tool's namespace and
                   prevents a name shared by another tool from misrouting (#65).
                   Omitted by direct/legacy callers, which resolve flat.
        params: flat dict — dispatcher splits into path / query / body
                based on the spec definition.
        """
        if self._index is None:
            raise RuntimeError("SpecIndex not set — call set_index() first")

        op = self._index.resolve(action_name, tool_name)
        if op is None:
            return {
                "error": True,
                "message": (
                    f"Unknown action: '{action_name}'. "
                    f"Check the tool description for valid action names."
                ),
            }

        return await self._execute_with_retry(op, params, tool_name)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _execute_with_retry(
        self, op: OperationSpec, params: dict[str, Any], tool_name: str | None = None
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
            response = await self._execute_one_with_retry(op, clean_params, tool_name)
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

        # Bind tool_name into the per-page executor the paginator drives, so
        # captured debug records carry the calling tool without widening the
        # Paginator.paginate(op, params, fn) contract.
        async def _run_page(o: OperationSpec, p: dict[str, Any]) -> DispatchResult:
            return await self._execute_one_with_retry(o, p, tool_name)

        return await paginator.paginate(
            op,
            clean_params,
            _run_page,
            max_pages=max_pages,
            page_size=page_size,
        )

    async def _execute_one_with_retry(
        self, op: OperationSpec, params: dict[str, Any], tool_name: str | None = None
    ) -> DispatchResult:
        """One request with the existing session-expiry retry behaviour."""
        response = await self._execute(op, params, tool_name)
        if isinstance(response, dict) and response.get("_session_expired"):
            print("[dispatcher] Session expired unexpectedly — re-authenticating")
            await self._auth.login(self._client)
            response = await self._execute(op, params, tool_name)
            if isinstance(response, dict) and response.get("_session_expired"):
                # Re-login succeeded but the retry is STILL a login page — e.g.
                # the session is invalidated again immediately, or a
                # concurrent-session limit on shared credentials keeps evicting
                # us. Surface a real error rather than leaking the internal
                # sentinel to the caller/LLM (#93 review).
                print("[dispatcher] Still unauthenticated after re-login — giving up")
                return {
                    "error": True,
                    "message": (
                        "Session expired and re-authentication did not recover it. "
                        "This can happen with a concurrent-session limit on shared "
                        "credentials, or if the session is invalidated immediately "
                        "after login."
                    ),
                }
        return response

    async def _execute(
        self, op: OperationSpec, raw_params: dict[str, Any], tool_name: str | None = None
    ) -> DispatchResult:
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

        # Substitute path params into the URL template. Each value is
        # percent-encoded with safe='' (#55 L3) so a value containing '/', '..',
        # '?', or '#' cannot reshape the request path to a sibling endpoint —
        # request-path injection under the server's privileged vManage session.
        # quote() leaves unreserved chars (letters, digits, '-._~') untouched,
        # so ordinary ids like '10.0.0.1' pass through unchanged.
        url = op.path
        for name, value in path_params.items():
            url = url.replace(f"{{{name}}}", quote(str(value), safe=""))

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

        sent_body = body_params if body_params else None
        # Defensive unwrap (#78): the body fields belong at the top level of
        # params, but a caller that followed the old `body: object` schema may
        # nest the whole payload under a lone `body` key. Forwarding that
        # verbatim double-wraps the request (vManage rejects it as an
        # "Unrecognized field 'body'"). When `body` is the *sole* body key,
        # unwrap it — whatever its value (object, array, or scalar) — so every
        # nested-style call works; a real field literally named `body` alongside
        # other fields is left untouched. (A None value can't occur here: None
        # params are dropped during the path/query/body split above.)
        if isinstance(sent_body, dict) and set(sent_body) == {"body"}:
            sent_body = sent_body["body"]
        debug_on = self._debug_cfg.enabled
        started = time.monotonic()

        try:
            response = await self._send_with_retry(
                method=op.method.upper(),
                url=url,
                params=query_params or None,
                json=sent_body,
                headers=headers,
                retryable=self._is_retryable(op.method),
            )
        except httpx2.RequestError as e:
            result: dict[str, Any] = {"error": True, "message": f"Request failed: {e}"}
            if debug_on:
                dbg = self._build_debug(
                    op,
                    tool_name,
                    url,
                    query_params,
                    sent_body,
                    headers,
                    response=None,
                    elapsed_ms=_elapsed_ms(started),
                    request_error=str(e),
                )
                self._emit_debug(dbg)
                result["debug"] = dbg
            return result

        # Detect session expiry — signal caller to re-auth. This is an internal
        # round we retry transparently, so it is intentionally not captured.
        if self._auth.is_session_expired(response):
            return {"_session_expired": True}

        elapsed_ms = _elapsed_ms(started)

        if response.is_error:
            body = _safe_json(response)
            result = {
                "error": True,
                "status_code": response.status_code,
                "message": f"HTTP {response.status_code}",
                "body": body,
            }
            hint = _stats_db_hint(op, response.status_code, body)
            if hint:
                result["hint"] = hint
            # A failed upstream call is captured under BOTH capture modes —
            # diagnosing failures is the whole point of debug mode (#72).
            if debug_on:
                dbg = self._build_debug(
                    op,
                    tool_name,
                    url,
                    query_params,
                    sent_body,
                    headers,
                    response=response,
                    elapsed_ms=elapsed_ms,
                )
                self._emit_debug(dbg)
                result["debug"] = dbg
            return result

        data = _safe_json(response)
        if debug_on and self._debug_cfg.capture == "all":
            dbg = self._build_debug(
                op,
                tool_name,
                url,
                query_params,
                sent_body,
                headers,
                response=response,
                elapsed_ms=elapsed_ms,
            )
            self._emit_debug(dbg)
            # Only dict results can carry the debug object without reshaping the
            # payload; list/str successes are logged to stderr only (honest, no
            # silent wrapping that would confuse the LLM consumer).
            if isinstance(data, dict) and "debug" not in data:
                data = {**data, "debug": dbg}

        return data

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
    ) -> httpx2.Response:
        cfg = self._retry_cfg
        attempts = max(1, cfg.max_attempts) if retryable else 1
        last_response: httpx2.Response | None = None

        for attempt in range(attempts):
            try:
                response = await self._client.request(
                    method=method,
                    url=url,
                    params=params,
                    json=json,
                    headers=headers,
                )
            except httpx2.RequestError:
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

    # ------------------------------------------------------------------
    # Debug capture (#72)
    # ------------------------------------------------------------------

    def _build_debug(
        self,
        op: OperationSpec,
        tool_name: str | None,
        path: str,
        query_params: dict[str, Any],
        body: dict[str, Any] | None,
        request_headers: dict[str, str],
        *,
        response: httpx2.Response | None,
        elapsed_ms: float,
        request_error: str | None = None,
    ) -> dict[str, Any]:
        """Assemble the structured ``debug`` record for one upstream exchange.

        Captures exactly what was sent (resolved path, query, the serialized
        body — which is where the ``params``-becomes-body gotcha shows up) and
        what came back (status, vManage error code, headers, body). When
        redaction is on, auth headers AND credential-shaped body/query values
        are masked, and oversized bodies are truncated."""
        redact = self._debug_cfg.redact
        # error_code is read from the raw body before redaction; the vManage
        # 'code' key (e.g. REST0001) is not a secret and isn't masked anyway.
        dbg: dict[str, Any] = {
            "tool": tool_name,
            "action": op.action_name,
            "operation_id": op.operation_id,
            "timing_ms": round(elapsed_ms, 1),
            "request": {
                "method": op.method.upper(),
                "path": path,
                "url": f"{self._base_url}{path}",
                "query_params": _redact_data(dict(query_params), redact),
                "body": _cap_body(_redact_data(body, redact)),
                "headers": _redact_headers(request_headers, redact),
            },
        }
        if request_error is not None:
            dbg["request_error"] = request_error
            return dbg
        if response is not None:
            resp_body = _safe_json(response)
            dbg["response"] = {
                "status_code": response.status_code,
                "error_code": _error_code(resp_body) or None,
                "headers": _redact_headers(dict(response.headers), redact),
                "body": _cap_body(_redact_data(resp_body, redact)),
            }
        return dbg

    def _emit_debug(self, dbg: dict[str, Any]) -> None:
        """Log one redacted debug record to stderr as a single JSON line."""
        print(f"[dispatcher][debug] {json.dumps(dbg, default=str)}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _elapsed_ms(started: float) -> float:
    """Milliseconds elapsed since a ``time.monotonic()`` mark."""
    return (time.monotonic() - started) * 1000.0


def _redact_headers(headers: dict[str, str], redact: bool) -> dict[str, str]:
    """Copy headers, masking auth-bearing ones when ``redact`` is on (#72)."""
    out: dict[str, str] = {}
    for key, value in (headers or {}).items():
        if redact and key.lower() in _SENSITIVE_HEADERS:
            out[key] = _REDACTED
        else:
            out[key] = value
    return out


def _redact_data(obj: Any, redact: bool) -> Any:
    """Recursively mask values under credential-shaped keys when ``redact`` is on.

    Header redaction alone leaks tokens that vManage returns *in the body*: e.g.
    GET /client/token (getCsrfToken) — reachable even in read-only mode —
    responds with ``{"token": "<live XSRF token>"}``. We walk the captured
    request/response body and query dict and replace the value of any key whose
    name matches ``_SENSITIVE_KEY_RE`` with ``<redacted>``, so a shared capture
    can't carry a replayable credential. Non-matching values (the query DSL,
    error codes, ordinary data) pass through untouched."""
    if not redact:
        return obj
    if isinstance(obj, dict):
        return {
            key: (_REDACTED if _SENSITIVE_KEY_RE.search(str(key)) else _redact_data(value, redact))
            for key, value in obj.items()
        }
    if isinstance(obj, list):
        return [_redact_data(item, redact) for item in obj]
    return obj


def _cap_body(value: Any) -> Any:
    """Truncate an oversized captured body to keep debug records bounded (#72)."""
    if value is None:
        return None
    try:
        serialized = json.dumps(value, default=str)
    except Exception:
        serialized = str(value)
    if len(serialized) <= _MAX_DEBUG_BODY_CHARS:
        return value
    return {
        "_truncated": True,
        "_original_chars": len(serialized),
        "preview": serialized[:_MAX_DEBUG_BODY_CHARS],
    }


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


def _has_query_param(op: OperationSpec) -> bool:
    return any(p.name == "query" and p.location == "query" for p in op.parameters)


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
    permission error, so that lead-in is hedged.

    A 400 ``STATS_VALIDATION0001`` is the complementary, post-auth signal: the
    request cleared RBAC and the *stats engine* rejected the query shape. The
    canonical cause is the ``body``-wrapper mistake (#78), so the hint names the
    top-level convention and the accepted fields. The 500-vs-400 distinction is
    itself diagnostic: 500 ``REST0001`` is pre-query (auth/RBAC), 400
    ``STATS_VALIDATION0001`` is post-auth (query validation)."""
    if status_code == 400 and _error_code(body) == "STATS_VALIDATION0001":
        return (
            "vManage's statistics engine rejected this query's shape "
            "(STATS_VALIDATION0001). Auth/RBAC passed — this is a query-validation "
            "error, not a permission error. Pass the request-body fields at the TOP "
            "LEVEL of params (do NOT nest them under a 'body' key). Accepted "
            "top-level fields: size, aggregation, plot_data, fields, category, "
            "query, sort."
        )
    if status_code != 500:
        return None
    is_rest0001 = _error_code(body) == "REST0001"
    is_stats_path = op.path.startswith("/statistics")
    if not (is_rest0001 or is_stats_path):
        return None
    # The GET raw-query form (`?query=…`) of a statistics endpoint is rejected
    # with REST0001 on current builds; the POST twin (query in the body) works.
    # Read-only mode now prefers that POST form, but a caller can still reach the
    # broken GET in read-write mode — so name the real fix rather than the
    # stats-DB-disabled guess. (#62)
    if is_rest0001 and op.method == "get" and _has_query_param(op):
        return (
            "vManage rejected the GET raw-query form of this statistics endpoint "
            "(REST0001). This build only serves the query over POST, with the "
            "filter in the request body. Use the POST variant of this action (it "
            "is registered even in read-only mode) and pass a 'query' payload."
        )
    if is_rest0001:
        return f"vManage returned REST0001 for this statistics-database query. {_STATS_DB_CAUSES}"
    return (
        "This statistics-database endpoint returned a server-side 500. If this is "
        f"a stats-DB query rather than a validation or permission error: {_STATS_DB_CAUSES}"
    )


def _safe_json(response: httpx2.Response) -> DispatchResult:
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
