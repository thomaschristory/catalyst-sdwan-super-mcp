"""transport_auth.py — HTTP transport auth (bearer token) and bind-safety logic.

Two responsibilities:
1. decide_bind(): a pure function that decides whether to honor the requested
   bind host or demote it to loopback, given the configured auth type and
   the --insecure-allow-public override flag. Easy to unit test in isolation.
2. BearerAuthMiddleware: a pure ASGI middleware enforcing
   `Authorization: Bearer <token>` on every HTTP request.

The middleware is written as pure ASGI (not Starlette's BaseHTTPMiddleware)
so it stays out of the response stream — important because SSE and
streamable-http are long-lived streaming transports and BaseHTTPMiddleware
is documented to break those.
"""

from __future__ import annotations

import hmac
import ipaddress
import logging
import time
from typing import Literal

from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

# WWW-Authenticate realm advertised on 401s. RFC 6750 §3.
_REALM = "catalyst-sdwan"

# Log throttling: at most _LOG_BURST rejection lines per _LOG_WINDOW_SEC,
# then a single "suppressed N more" rollup on the next window. Protects logs
# (and SIEM cost) from a bad-token flood.
_LOG_BURST = 10
_LOG_WINDOW_SEC = 60.0


def _is_loopback(host: str) -> bool:
    """True if `host` is a loopback bind target.

    Covers the whole 127.0.0.0/8 range plus ::1, the literal "localhost",
    and bracketed IPv6 forms ("[::1]"). Falls back to False on anything we
    can't parse — `decide_bind` errs safe by demoting unknown hosts.
    """
    if host == "localhost":
        return True
    candidate = host
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


def decide_bind(
    host: str,
    auth_type: Literal["none", "bearer"],
    insecure_ok: bool,
) -> tuple[str, list[str]]:
    """Decide the effective bind host given the configured auth and override.

    Rules:
      - Loopback hosts (127.0.0.0/8, ::1, localhost) are never demoted.
      - Non-loopback + auth_type == "bearer" → bind as requested.
      - Non-loopback + auth_type == "none" + insecure_ok=False → demote
        to 127.0.0.1 with warnings explaining how to opt in.
      - Non-loopback + auth_type == "none" + insecure_ok=True → bind as
        requested (operator has acknowledged the risk).

    Returns:
      (effective_host, warning_lines)
    """
    if _is_loopback(host):
        return host, []
    if auth_type == "bearer":
        return host, []
    if insecure_ok:
        return host, []

    warnings = [
        f"refusing to bind {host} with transport.auth.type=none.",
        "Demoting bind to 127.0.0.1. To expose externally, set transport.auth.type=bearer",
        "and transport.auth.token, OR set transport.auth.type=none explicitly AND pass",
        "--insecure-allow-public to acknowledge the risk.",
    ]
    return "127.0.0.1", warnings


class BearerAuthMiddleware:
    """Pure ASGI middleware that enforces `Authorization: Bearer <token>`.

    Compares the supplied token to the configured one in constant time
    (`hmac.compare_digest`). On failure returns a 401 with an RFC 6750 §3
    `WWW-Authenticate` challenge. Never logs the supplied token — not even
    a prefix; that would leak rotation state.

    Pure ASGI (rather than `BaseHTTPMiddleware`) so we don't wrap the
    downstream response — SSE and streamable-http are streaming transports
    and BaseHTTPMiddleware is documented to break those.
    """

    def __init__(self, app: ASGIApp, expected_token: str) -> None:
        if not expected_token:
            raise ValueError("BearerAuthMiddleware requires a non-empty expected_token")
        self._app = app
        self._expected_token = expected_token
        # Mutable log-throttle state — instance-local, fine for a single-process server.
        self._log_count = 0
        self._log_window_start = 0.0
        self._log_suppressed = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Only HTTP requests are authenticated; websocket / lifespan pass through.
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        header = self._extract_authorization(scope)
        # split(None, 1) collapses runs of whitespace per RFC 7235 (1*SP).
        parts = header.split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
            await self._send_401(
                scope,
                send,
                error="invalid_request",
                description="missing or malformed Authorization header",
            )
            return

        if not hmac.compare_digest(parts[1], self._expected_token):
            await self._send_401(scope, send, error="invalid_token", description="invalid token")
            return

        await self._app(scope, receive, send)

    @staticmethod
    def _extract_authorization(scope: Scope) -> str:
        for raw_name, raw_value in scope.get("headers") or []:
            if raw_name == b"authorization":
                return str(raw_value.decode("latin-1"))
        return ""

    async def _send_401(
        self,
        scope: Scope,
        send: Send,
        *,
        error: str,
        description: str,
    ) -> None:
        self._log_rejection(scope, description)
        body = b'{"error":"' + description.encode("ascii", "replace") + b'"}'
        challenge = (
            f'Bearer realm="{_REALM}", error="{error}", error_description="{description}"'
        ).encode("ascii")
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"www-authenticate", challenge),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    def _log_rejection(self, scope: Scope, reason: str) -> None:
        now = time.monotonic()
        if now - self._log_window_start >= _LOG_WINDOW_SEC:
            if self._log_suppressed:
                logger.warning(
                    "auth: suppressed %d additional rejections in last %ds window",
                    self._log_suppressed,
                    int(_LOG_WINDOW_SEC),
                )
            self._log_window_start = now
            self._log_count = 0
            self._log_suppressed = 0

        if self._log_count >= _LOG_BURST:
            self._log_suppressed += 1
            return

        self._log_count += 1
        client = scope.get("client")
        client_host = client[0] if client else "unknown"
        logger.warning(
            "auth rejected: remote=%s path=%s reason=%s",
            client_host,
            scope.get("path", ""),
            reason,
        )
