"""Startup ordering: credentials must be validated before spec loading (#47).

Loading (and possibly auto-fetching) the spec is expensive; there's no point
doing it when the vManage credentials are missing and login is guaranteed to
fail. The check must fire immediately after config load, before ``SpecLoader``.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import pytest

from sdwan_mcp.auth import require_credentials
from sdwan_mcp.server import _connect_and_register


def _args(config: str) -> argparse.Namespace:
    return argparse.Namespace(
        config=config,
        version=None,
        transport="stdio",
        host=None,
        port=None,
        read_write=False,
        insecure_allow_public=False,
        max_actions_per_tool=None,
    )


def test_require_credentials_raises_when_missing() -> None:
    with pytest.raises(RuntimeError, match="credentials are not set"):
        require_credentials("", "")
    with pytest.raises(RuntimeError, match="credentials are not set"):
        require_credentials("user", "")
    with pytest.raises(RuntimeError, match="credentials are not set"):
        require_credentials("", "pass")


def test_require_credentials_ok() -> None:
    require_credentials("user", "pass")  # must not raise


def test_credentials_validated_before_spec_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With creds missing AND specs absent (auto_fetch off), the credentials
    error must win — proving the check runs before SpecLoader's
    FileNotFoundError."""
    monkeypatch.delenv("VMANAGE_USERNAME", raising=False)
    monkeypatch.delenv("VMANAGE_PASSWORD", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env reachable from here

    cfg = tmp_path / "sdwan-mcp.yaml"
    cfg.write_text(
        "vmanage:\n"
        "  host: vm.test\n"
        "sdwan:\n"
        f"  specs_dir: {tmp_path / 'no-such-specs'}\n"
        "  active_version: '20.18'\n"
        "  auto_fetch: false\n"
    )

    with pytest.raises(RuntimeError, match="credentials are not set"):
        asyncio.run(_connect_and_register(_args(str(cfg))))
