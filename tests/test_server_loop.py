"""The pre-flight and serving phases must share ONE event loop (#56).

The dispatcher's ``httpx.AsyncClient`` is created during pre-flight
(``_connect_and_register``) and is bound to the loop running at that moment.
Before the fix, pre-flight ran in its own ``asyncio.run()`` and ``mcp.run()``
opened a second loop, so the first tool call of a session hit a client bound to
an already-closed loop ("Event loop is closed") and only succeeded on retry.
``build_and_run`` now drives both phases through ``_serve`` on a single loop.
"""

from __future__ import annotations

import argparse
import asyncio

import pytest

from sdwan_mcp import server
from sdwan_mcp.config import AppConfig


def _args() -> argparse.Namespace:
    return argparse.Namespace(
        config=None,
        version=None,
        transport="stdio",
        host=None,
        port=None,
        read_write=False,
        insecure_allow_public=False,
        max_actions_per_tool=None,
    )


def test_preflight_serve_and_close_share_one_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """connect → serve → close must all observe the same running loop."""
    seen: dict[str, asyncio.AbstractEventLoop] = {}

    class FakeMCP:
        async def run_async(self, **kwargs: object) -> None:
            seen["serve"] = asyncio.get_running_loop()

    class FakeDispatcher:
        async def close(self) -> None:
            seen["close"] = asyncio.get_running_loop()

    async def fake_connect(args: argparse.Namespace):
        seen["connect"] = asyncio.get_running_loop()
        return FakeMCP(), FakeDispatcher(), "stdio", "127.0.0.1", 8000, []

    monkeypatch.setattr(server, "_connect_and_register", fake_connect)

    server.build_and_run(_args())

    assert set(seen) == {"connect", "serve", "close"}
    assert seen["connect"] is seen["serve"] is seen["close"]


def test_close_runs_even_if_serve_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A crash while serving must still trigger dispatcher shutdown."""
    closed = False

    class FakeMCP:
        async def run_async(self, **kwargs: object) -> None:
            raise RuntimeError("boom")

    class FakeDispatcher:
        async def close(self) -> None:
            nonlocal closed
            closed = True

    async def fake_connect(args: argparse.Namespace):
        return FakeMCP(), FakeDispatcher(), "stdio", "127.0.0.1", 8000, []

    monkeypatch.setattr(server, "_connect_and_register", fake_connect)

    with pytest.raises(RuntimeError, match="boom"):
        server.build_and_run(_args())
    assert closed is True


def test_tls_warning_fires_when_verification_disabled(capsys: pytest.CaptureFixture[str]) -> None:
    """A disabled verify_ssl must emit a loud stderr WARNING before login (#55 H1)."""
    cfg = AppConfig()
    cfg.vmanage.verify_ssl = False
    cfg.vmanage.host = "vm.prod.example"
    server._warn_if_tls_unverified(cfg)
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "verification is DISABLED" in err
    assert "vm.prod.example" in err


def test_no_tls_warning_when_verification_enabled(capsys: pytest.CaptureFixture[str]) -> None:
    """The secure default stays quiet."""
    cfg = AppConfig()
    cfg.vmanage.verify_ssl = True
    server._warn_if_tls_unverified(cfg)
    assert capsys.readouterr().err == ""
