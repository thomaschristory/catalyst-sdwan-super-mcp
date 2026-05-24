"""transport_auth.py — HTTP transport auth (bearer token) and bind-safety logic.

Two responsibilities:
1. decide_bind(): a pure function that decides whether to honor the requested
   bind host or demote it to loopback, given the configured auth type and
   the --insecure-allow-public override flag. Easy to unit test in isolation.
2. BearerAuthMiddleware: a Starlette middleware enforcing
   `Authorization: Bearer <token>` on every HTTP request.
"""

from __future__ import annotations

_LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})


def decide_bind(
    host: str,
    auth_type: str,
    insecure_ok: bool,
) -> tuple[str, list[str]]:
    """Decide the effective bind host given the configured auth and override.

    Rules:
      - Loopback hosts (127.0.0.1, ::1, localhost) are never demoted.
      - Non-loopback + auth_type == "bearer" → bind as requested.
      - Non-loopback + auth_type == "none" + insecure_ok=False → demote
        to 127.0.0.1 with warnings explaining how to opt in.
      - Non-loopback + auth_type == "none" + insecure_ok=True → bind as
        requested (operator has acknowledged the risk).

    Returns:
      (effective_host, warning_lines)
    """
    if host in _LOOPBACK_HOSTS:
        return host, []
    if auth_type == "bearer":
        return host, []
    if insecure_ok:
        return host, []

    warnings = [
        f"refusing to bind {host} with transport.auth.type=none.",
        "Demoting bind to 127.0.0.1. To expose externally, set "
        "transport.auth.type=bearer",
        "and transport.auth.token, OR set transport.auth.type=none "
        "explicitly AND pass",
        "--insecure-allow-public to acknowledge the risk.",
    ]
    return "127.0.0.1", warnings
