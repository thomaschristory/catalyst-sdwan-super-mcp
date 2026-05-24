"""Tests for the YAML + env-var config loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from sdwan_mcp.config import load_config


def test_load_config_interpolates_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VMANAGE_USERNAME", "alice")
    monkeypatch.setenv("VMANAGE_PASSWORD", "s3cret")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """\
vmanage:
  host: example.local
  port: 8443
  verify_ssl: false
  username: "${VMANAGE_USERNAME}"
  password: "${VMANAGE_PASSWORD}"
  use_jwt: true

sdwan:
  specs_dir: ./specs
  active_version: "20.18"

transport:
  mode: stdio
  host: 127.0.0.1
  port: 8000
"""
    )

    config = load_config(str(cfg))
    assert config.vmanage.username == "alice"
    assert config.vmanage.password == "s3cret"
    assert config.vmanage.base_url == "https://example.local:8443/dataservice"
    assert config.sdwan.active_version == "20.18"
    assert config.transport.mode == "stdio"


def test_load_config_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(str(tmp_path / "nope.yaml"))


def test_pagination_defaults(tmp_path):
    from sdwan_mcp.config import load_config

    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text("vmanage:\n  host: vm.test\nsdwan:\n  active_version: '20.18'\n")
    cfg = load_config(str(cfg_file))
    assert cfg.sdwan.pagination.enabled is True
    assert cfg.sdwan.pagination.max_pages == 5
    assert cfg.sdwan.pagination.page_size is None


def test_retry_defaults(tmp_path: Path) -> None:
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text("vmanage:\n  host: vm.test\nsdwan:\n  active_version: '20.18'\n")
    cfg = load_config(str(cfg_file))
    assert cfg.vmanage.timeout == 30.0
    assert cfg.vmanage.retries.max_attempts == 3
    assert cfg.vmanage.retries.statuses == (502, 503, 504)
    assert cfg.vmanage.retries.retry_mutating is False


def test_retry_overrides_and_null_statuses(tmp_path: Path) -> None:
    """`statuses: ~` (YAML null) must fall back to defaults, not crash."""
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        "vmanage:\n"
        "  host: vm.test\n"
        "  timeout: 12.5\n"
        "  retries:\n"
        "    max_attempts: 5\n"
        "    statuses: ~\n"
        "    backoff_base: 1.0\n"
        "    backoff_cap: 16.0\n"
        "    retry_mutating: true\n"
    )
    cfg = load_config(str(cfg_file))
    assert cfg.vmanage.timeout == 12.5
    assert cfg.vmanage.retries.max_attempts == 5
    assert cfg.vmanage.retries.statuses == (502, 503, 504)
    assert cfg.vmanage.retries.backoff_base == 1.0
    assert cfg.vmanage.retries.backoff_cap == 16.0
    assert cfg.vmanage.retries.retry_mutating is True


def test_pagination_overrides(tmp_path):
    from sdwan_mcp.config import load_config

    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        "vmanage:\n"
        "  host: vm.test\n"
        "sdwan:\n"
        "  pagination:\n"
        "    enabled: false\n"
        "    max_pages: 12\n"
        "    page_size: 200\n"
    )
    cfg = load_config(str(cfg_file))
    assert cfg.sdwan.pagination.enabled is False
    assert cfg.sdwan.pagination.max_pages == 12
    assert cfg.sdwan.pagination.page_size == 200
