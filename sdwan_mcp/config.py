"""
config.py — loads config.yaml and resolves ${ENV_VAR} interpolation.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import yaml

# Minimum bearer-token lengths. Below the hard floor we refuse to start;
# between the soft and hard floors we emit a stderr WARNING. Numbers come
# from "16 chars of base64 ≈ 96 bits of entropy" — enough to resist online
# brute force when combined with the rate-limited logger.
_TOKEN_HARD_MIN = 8
_TOKEN_SOFT_MIN = 16

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class VManageConfig:
    host: str
    port: int = 8443
    verify_ssl: bool = False
    username: str = ""
    password: str = ""
    use_jwt: bool = True  # True = JWT (20.18.1+), False = session-based

    @property
    def base_url(self) -> str:
        return f"https://{self.host}:{self.port}/dataservice"


@dataclass
class PaginationConfig:
    enabled: bool = True
    max_pages: int = 5
    page_size: int | None = None


@dataclass
class SDWANConfig:
    specs_dir: str = "./specs"
    active_version: str = "20.18"
    max_actions_per_tool: int = 150  # 0 disables splitting (one tool per section)
    pagination: PaginationConfig = field(default_factory=PaginationConfig)


_VALID_AUTH_TYPES: frozenset[str] = frozenset({"none", "bearer"})


@dataclass
class TransportAuthConfig:
    """Authentication for the HTTP transports (SSE, streamable-http).

    type='none' means no auth — only safe on loopback or behind a trusted
    authenticating reverse proxy (see --insecure-allow-public in server.py).
    type='bearer' enforces an `Authorization: Bearer <token>` header on
    every request, compared in constant time.
    """

    type: Literal["none", "bearer"] = "none"
    token: str = ""


@dataclass
class TransportConfig:
    mode: str = "stdio"  # stdio | sse | streamable-http
    host: str = "127.0.0.1"
    port: int = 8000
    auth: TransportAuthConfig = field(default_factory=TransportAuthConfig)


@dataclass
class AppConfig:
    vmanage: VManageConfig = field(default_factory=lambda: VManageConfig(host=""))
    sdwan: SDWANConfig = field(default_factory=SDWANConfig)
    transport: TransportConfig = field(default_factory=TransportConfig)


# ---------------------------------------------------------------------------
# Env var interpolation
# ---------------------------------------------------------------------------

_ENV_RE = re.compile(r"\$\{([^}]+)\}")


def _interpolate(value: str) -> str:
    """Replace ${VAR} with the corresponding environment variable."""

    def replacer(match: re.Match[str]) -> str:
        var_name = match.group(1)
        result = os.environ.get(var_name, "")
        if not result:
            print(f"[config] WARNING: env var '{var_name}' is not set")
        return result

    return _ENV_RE.sub(replacer, value)


def _interpolate_dict(obj: Any) -> Any:
    """Recursively interpolate env vars in all string values of a dict."""
    if isinstance(obj, dict):
        return {k: _interpolate_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_interpolate_dict(i) for i in obj]
    if isinstance(obj, str):
        return _interpolate(obj)
    return obj


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_config(path: str = "config.yaml") -> AppConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    raw = yaml.safe_load(config_path.read_text())
    raw = _interpolate_dict(raw)

    vmanage_raw = raw.get("vmanage", {})
    sdwan_raw = raw.get("sdwan", {})
    transport_raw = raw.get("transport", {})

    vmanage = VManageConfig(
        host=vmanage_raw.get("host", ""),
        port=int(vmanage_raw.get("port", 8443)),
        verify_ssl=bool(vmanage_raw.get("verify_ssl", False)),
        username=vmanage_raw.get("username", ""),
        password=vmanage_raw.get("password", ""),
        use_jwt=bool(vmanage_raw.get("use_jwt", True)),
    )

    pagination_raw = sdwan_raw.get("pagination", {}) or {}
    pagination = PaginationConfig(
        enabled=bool(pagination_raw.get("enabled", True)),
        max_pages=int(pagination_raw.get("max_pages", 5)),
        page_size=(
            int(pagination_raw["page_size"])
            if pagination_raw.get("page_size") is not None
            else None
        ),
    )

    sdwan = SDWANConfig(
        specs_dir=sdwan_raw.get("specs_dir", "./specs"),
        active_version=str(sdwan_raw.get("active_version", "20.18")),
        max_actions_per_tool=int(sdwan_raw.get("max_actions_per_tool", 150)),
        pagination=pagination,
    )

    auth_raw = transport_raw.get("auth", {}) or {}
    auth_type_str = str(auth_raw.get("type", "none"))
    auth_token = str(auth_raw.get("token", ""))

    if auth_type_str not in _VALID_AUTH_TYPES:
        raise ValueError(
            f"unknown transport.auth.type: {auth_type_str!r}. "
            f"Choose one of {sorted(_VALID_AUTH_TYPES)}."
        )
    auth_type: Literal["none", "bearer"] = cast(Literal["none", "bearer"], auth_type_str)

    if auth_type == "bearer" and not auth_token:
        raise ValueError(
            "transport.auth.type=bearer requires a non-empty transport.auth.token "
            "(set ${SDWAN_MCP_TOKEN} or equivalent, or check the env var is exported)."
        )
    if auth_type == "bearer" and len(auth_token) < _TOKEN_HARD_MIN:
        raise ValueError(
            f"transport.auth.token is too short ({len(auth_token)} chars); "
            f"require at least {_TOKEN_HARD_MIN} characters. "
            'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )
    if auth_type == "bearer" and len(auth_token) < _TOKEN_SOFT_MIN:
        print(
            f"[config] WARNING: transport.auth.token is shorter than "
            f"{_TOKEN_SOFT_MIN} chars — recommend regenerating with "
            'python -c "import secrets; print(secrets.token_urlsafe(32))"',
            file=sys.stderr,
        )
    if auth_type_str == "none" and auth_token:
        raise ValueError(
            "token configured but transport.auth.type=none — "
            "set type: bearer to enable it, or remove the token."
        )

    transport = TransportConfig(
        mode=transport_raw.get("mode", "stdio"),
        host=transport_raw.get("host", "127.0.0.1"),
        port=int(transport_raw.get("port", 8000)),
        auth=TransportAuthConfig(type=auth_type, token=auth_token),  # type narrowed via cast above
    )

    return AppConfig(vmanage=vmanage, sdwan=sdwan, transport=transport)
