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
