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


def test_transport_auth_defaults_to_none(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "vmanage:\n  host: vm.test\nsdwan:\n  active_version: '20.18'\n"
    )
    config = load_config(str(cfg))
    assert config.transport.auth.type == "none"
    assert config.transport.auth.token == ""


def test_transport_auth_bearer_with_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SDWAN_MCP_TOKEN", "s3cret-token")
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        """\
vmanage:
  host: vm.test
sdwan:
  active_version: '20.18'
transport:
  mode: streamable-http
  host: 0.0.0.0
  port: 8000
  auth:
    type: bearer
    token: "${SDWAN_MCP_TOKEN}"
"""
    )
    config = load_config(str(cfg))
    assert config.transport.auth.type == "bearer"
    assert config.transport.auth.token == "s3cret-token"


def test_transport_auth_bearer_missing_token_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        """\
vmanage:
  host: vm.test
sdwan:
  active_version: '20.18'
transport:
  mode: streamable-http
  auth:
    type: bearer
"""
    )
    with pytest.raises(ValueError, match="transport.auth.type=bearer requires"):
        load_config(str(cfg))


def test_transport_auth_none_with_token_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        """\
vmanage:
  host: vm.test
sdwan:
  active_version: '20.18'
transport:
  mode: streamable-http
  auth:
    type: none
    token: leftover-paste
"""
    )
    with pytest.raises(ValueError, match="token configured but transport.auth.type=none"):
        load_config(str(cfg))


def test_transport_auth_unknown_type_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        """\
vmanage:
  host: vm.test
sdwan:
  active_version: '20.18'
transport:
  auth:
    type: oidc
"""
    )
    with pytest.raises(ValueError, match="unknown transport.auth.type"):
        load_config(str(cfg))


def test_transport_auth_bearer_env_var_unset_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SDWAN_MCP_TOKEN", raising=False)
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "vmanage:\n  host: vm.test\nsdwan:\n  active_version: '20.18'\n"
        "transport:\n  auth:\n    type: bearer\n    token: \"${SDWAN_MCP_TOKEN}\"\n"
    )
    with pytest.raises(ValueError, match="transport.auth.type=bearer requires"):
        load_config(str(cfg))
