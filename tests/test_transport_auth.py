"""Tests for transport_auth: decide_bind() and BearerAuthMiddleware."""

from __future__ import annotations

import pytest

from sdwan_mcp.transport_auth import decide_bind


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_decide_bind_loopback_never_demoted(host: str) -> None:
    effective, warnings = decide_bind(host=host, auth_type="none", insecure_ok=False)
    assert effective == host
    assert warnings == []


def test_decide_bind_public_with_bearer_passes() -> None:
    effective, warnings = decide_bind(host="0.0.0.0", auth_type="bearer", insecure_ok=False)
    assert effective == "0.0.0.0"
    assert warnings == []


def test_decide_bind_public_with_none_demotes_to_loopback() -> None:
    effective, warnings = decide_bind(host="0.0.0.0", auth_type="none", insecure_ok=False)
    assert effective == "127.0.0.1"
    assert any("Demoting bind to 127.0.0.1" in w for w in warnings)
    assert any("--insecure-allow-public" in w for w in warnings)


def test_decide_bind_public_with_none_and_override_passes() -> None:
    effective, warnings = decide_bind(host="0.0.0.0", auth_type="none", insecure_ok=True)
    assert effective == "0.0.0.0"
    assert warnings == []


def test_decide_bind_arbitrary_public_host_demoted() -> None:
    # Any non-loopback host without bearer + without override gets demoted.
    effective, warnings = decide_bind(host="10.0.0.5", auth_type="none", insecure_ok=False)
    assert effective == "127.0.0.1"
    assert len(warnings) >= 1
