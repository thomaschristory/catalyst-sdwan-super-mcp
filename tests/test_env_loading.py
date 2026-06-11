"""Tests for .env discovery.

Regression coverage for #44: python-dotenv's bare ``load_dotenv()`` searches
upward from the *calling module's* directory. Once the package is installed
(``uv tool install`` / pipx), that directory is site-packages, so a ``.env``
sitting in the user's project dir is never found. ``_load_env`` must search the
current working directory instead (and, as a bonus, next to ``--config``).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sdwan_mcp.server import _load_env


def test_load_env_finds_dotenv_in_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A .env in the current working directory is loaded regardless of where
    the package itself lives on disk."""
    monkeypatch.delenv("SDWAN_TEST_CWD_VAR", raising=False)
    (tmp_path / ".env").write_text("SDWAN_TEST_CWD_VAR=from-cwd\n")
    monkeypatch.chdir(tmp_path)

    _load_env()

    assert os.environ["SDWAN_TEST_CWD_VAR"] == "from-cwd"


def test_load_env_finds_dotenv_next_to_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When --config points elsewhere, a .env beside the config is still found
    even though the cwd has none."""
    monkeypatch.delenv("SDWAN_TEST_CFG_VAR", raising=False)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".env").write_text("SDWAN_TEST_CFG_VAR=from-config-dir\n")
    cfg = proj / "sdwan-mcp.yaml"
    cfg.write_text("vmanage:\n  host: vm.test\n")

    run_dir = tmp_path / "elsewhere"
    run_dir.mkdir()
    monkeypatch.chdir(run_dir)

    _load_env(str(cfg))

    assert os.environ["SDWAN_TEST_CFG_VAR"] == "from-config-dir"


def test_exported_env_wins_over_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An already-exported shell variable must not be clobbered by .env."""
    monkeypatch.setenv("SDWAN_TEST_PRECEDENCE", "from-shell")
    (tmp_path / ".env").write_text("SDWAN_TEST_PRECEDENCE=from-file\n")
    monkeypatch.chdir(tmp_path)

    _load_env()

    assert os.environ["SDWAN_TEST_PRECEDENCE"] == "from-shell"


def test_load_env_no_dotenv_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No .env anywhere reachable must not raise."""
    monkeypatch.chdir(tmp_path)
    _load_env()  # must not raise
    _load_env(str(tmp_path / "missing.yaml"))  # must not raise
