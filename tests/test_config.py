"""Tests for the YAML + env-var config loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from sdwan_mcp.config import load_config


def test_load_config_interpolates_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VMANAGE_USERNAME", "alice")
    monkeypatch.setenv("VMANAGE_PASSWORD", "s3cret")

    cfg = tmp_path / "sdwan-mcp.yaml"
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


def test_load_config_missing_file_returns_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The YAML file is optional (#49): an absent file yields defaults + env."""
    for var in ("VMANAGE_HOST", "VMANAGE_PORT", "VMANAGE_USERNAME", "VMANAGE_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    cfg = load_config(str(tmp_path / "nope.yaml"))
    assert cfg.vmanage.host == "sandbox-sdwan-2.cisco.com"
    assert cfg.sdwan.active_version == "20.18"
    assert cfg.transport.mode == "stdio"


def test_verify_ssl_defaults_to_true(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Secure by default (#55 H1): TLS verification must be ON unless opted out."""
    monkeypatch.delenv("VMANAGE_VERIFY_SSL", raising=False)
    cfg = load_config(str(tmp_path / "nope.yaml"))
    assert cfg.vmanage.verify_ssl is True


def test_verify_ssl_env_opt_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Operators on the self-signed sandbox opt out explicitly via env."""
    monkeypatch.setenv("VMANAGE_VERIFY_SSL", "false")
    cfg = load_config(str(tmp_path / "nope.yaml"))
    assert cfg.vmanage.verify_ssl is False


def test_bare_yaml_sections_fall_back_to_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare `vmanage:` section (parses to None) must not crash."""
    for var in ("VMANAGE_HOST", "VMANAGE_USERNAME", "VMANAGE_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    cfg = tmp_path / "c.yaml"
    cfg.write_text("vmanage:\nsdwan:\ntransport:\n")
    config = load_config(str(cfg))
    assert config.vmanage.host == "sandbox-sdwan-2.cisco.com"
    assert config.sdwan.active_version == "20.18"
    assert config.transport.mode == "stdio"


def test_load_config_missing_file_required_raises(tmp_path: Path) -> None:
    """When the user explicitly asks for a file (required=True), missing errors."""
    with pytest.raises(FileNotFoundError):
        load_config(str(tmp_path / "nope.yaml"), required=True)


def test_credentials_from_env_without_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Core scenario for #49: no config file, creds + host from env vars."""
    monkeypatch.setenv("VMANAGE_USERNAME", "bob")
    monkeypatch.setenv("VMANAGE_PASSWORD", "hunter2")
    monkeypatch.setenv("VMANAGE_HOST", "vm.example.net")
    monkeypatch.setenv("VMANAGE_PORT", "8443")
    cfg = load_config(str(tmp_path / "absent.yaml"))
    assert cfg.vmanage.username == "bob"
    assert cfg.vmanage.password == "hunter2"
    assert cfg.vmanage.base_url == "https://vm.example.net:8443/dataservice"


def test_env_overrides_yaml_vmanage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Env vars win over YAML, but unspecified YAML fields are preserved."""
    monkeypatch.setenv("VMANAGE_USERNAME", "from-env")
    monkeypatch.delenv("VMANAGE_HOST", raising=False)
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text("vmanage:\n  host: yaml-host\n  username: from-yaml\n  password: yaml-pw\n")
    cfg = load_config(str(cfg_file))
    assert cfg.vmanage.username == "from-env"  # env overrides
    assert cfg.vmanage.host == "yaml-host"  # YAML value preserved (deep merge)
    assert cfg.vmanage.password == "yaml-pw"


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


def test_transport_auth_defaults_to_none(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text("vmanage:\n  host: vm.test\nsdwan:\n  active_version: '20.18'\n")
    config = load_config(str(cfg))
    assert config.transport.auth.type == "none"
    assert config.transport.auth.token == ""


def test_transport_auth_bearer_with_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SDWAN_MCP_TOKEN", "s3cret-token-long-enough")
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
    assert config.transport.auth.token == "s3cret-token-long-enough"


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
    with pytest.raises(ValueError, match=r"transport\.auth\.type=bearer requires"):
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
    with pytest.raises(ValueError, match=r"token configured but transport\.auth\.type=none"):
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
    # The Literal type now rejects unknown values during model construction.
    with pytest.raises(ValueError, match=r"transport\.auth\.type"):
        load_config(str(cfg))


def test_transport_auth_bearer_env_var_unset_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SDWAN_MCP_TOKEN", raising=False)
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "vmanage:\n  host: vm.test\nsdwan:\n  active_version: '20.18'\n"
        'transport:\n  auth:\n    type: bearer\n    token: "${SDWAN_MCP_TOKEN}"\n'
    )
    with pytest.raises(ValueError, match=r"transport\.auth\.type=bearer requires"):
        load_config(str(cfg))


def test_transport_auth_bearer_short_token_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "vmanage:\n  host: vm.test\nsdwan:\n  active_version: '20.18'\n"
        'transport:\n  auth:\n    type: bearer\n    token: "abc12"\n'
    )
    with pytest.raises(ValueError, match=r"transport\.auth\.token is too short"):
        load_config(str(cfg))


def test_transport_auth_bearer_soft_floor_warns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "vmanage:\n  host: vm.test\nsdwan:\n  active_version: '20.18'\n"
        'transport:\n  auth:\n    type: bearer\n    token: "tenchars-x"\n'
    )
    config = load_config(str(cfg))
    assert config.transport.auth.token == "tenchars-x"
    err = capsys.readouterr().err
    assert "shorter than 16 chars" in err
