"""
config.py — loads config.yaml and resolves ${ENV_VAR} interpolation.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class RetryConfig:
    """Retry policy for transient HTTP failures from vManage / its load balancer."""

    max_attempts: int = 3  # total attempts including the first try
    statuses: tuple[int, ...] = (502, 503, 504)
    backoff_base: float = 0.5  # seconds; first backoff is base * 2**0
    backoff_cap: float = 8.0  # seconds; upper bound on a single backoff
    retry_mutating: bool = False  # by default, only GET is retried


@dataclass
class VManageConfig:
    host: str
    port: int = 8443
    verify_ssl: bool = False
    username: str = ""
    password: str = ""
    use_jwt: bool = True  # True = JWT (20.18.1+), False = session-based
    timeout: float = 30.0  # seconds, applied to every vManage HTTP request
    retries: RetryConfig = field(default_factory=RetryConfig)

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


@dataclass
class TransportConfig:
    mode: str = "stdio"  # stdio | sse | streamable-http
    host: str = "127.0.0.1"
    port: int = 8000


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

    retries_raw = vmanage_raw.get("retries", {}) or {}
    statuses_raw = retries_raw.get("statuses", [502, 503, 504])
    retries = RetryConfig(
        max_attempts=int(retries_raw.get("max_attempts", 3)),
        statuses=tuple(int(s) for s in statuses_raw),
        backoff_base=float(retries_raw.get("backoff_base", 0.5)),
        backoff_cap=float(retries_raw.get("backoff_cap", 8.0)),
        retry_mutating=bool(retries_raw.get("retry_mutating", False)),
    )

    vmanage = VManageConfig(
        host=vmanage_raw.get("host", ""),
        port=int(vmanage_raw.get("port", 8443)),
        verify_ssl=bool(vmanage_raw.get("verify_ssl", False)),
        username=vmanage_raw.get("username", ""),
        password=vmanage_raw.get("password", ""),
        use_jwt=bool(vmanage_raw.get("use_jwt", True)),
        timeout=float(vmanage_raw.get("timeout", 30.0)),
        retries=retries,
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

    transport = TransportConfig(
        mode=transport_raw.get("mode", "stdio"),
        host=transport_raw.get("host", "127.0.0.1"),
        port=int(transport_raw.get("port", 8000)),
    )

    return AppConfig(vmanage=vmanage, sdwan=sdwan, transport=transport)
