"""Live ingestion of split-spec vManage versions (>=20.16).

Public entrypoints:

- :func:`fetch_version`  — async; downloads + stitches + writes the YAML.
- :func:`list_known_versions` — names a curated set of versions known to work.
- :data:`KNOWN_VERSIONS` — the same set as a plain tuple.

See ``docs/dev/issue-31-plan.md`` for the design notes.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

from .discover import (
    Discovery,
    DiscoveryError,
    discovery_url,
    parse_discovery_html,
    version_to_slug,
)
from .fetch import (
    DEFAULT_CACHE_ROOT,
    FetchError,
    FetchProgress,
    fetch_discovery_html,
    fetch_fragments,
    make_client,
    write_yaml,
)
from .stitch import StitchError, stitch
from .validate import FetcherValidationError, validate

__all__ = [
    "DEFAULT_CACHE_ROOT",
    "KNOWN_VERSIONS",
    "Discovery",
    "DiscoveryError",
    "FetchError",
    "FetchProgress",
    "FetcherValidationError",
    "StitchError",
    "VersionInfo",
    "discovery_url",
    "fetch_version",
    "list_known_versions",
    "version_to_slug",
]

# Versions where the split-spec layout is known to be published. Bundled
# specs (20.15/20.16/20.18) plus the next two anticipated releases.
KNOWN_VERSIONS: tuple[str, ...] = ("20.15", "20.16", "20.18", "20.19", "21.1")


@dataclass(frozen=True)
class VersionInfo:
    """One row in the ``list-versions`` output."""

    version: str
    layout: Literal["monolith", "split"]
    cached: bool


def list_known_versions(specs_dir: Path) -> list[VersionInfo]:
    """Return curated KNOWN_VERSIONS annotated with on-disk cache status.

    A version is considered "cached" if ``specs_dir/<version>/`` exists and
    contains at least one ``*.{yaml,yml,json}`` file.
    """
    out: list[VersionInfo] = []
    for v in KNOWN_VERSIONS:
        cached = _has_cached_spec(specs_dir / v)
        layout: Literal["monolith", "split"] = "monolith" if v == "20.15" else "split"
        out.append(VersionInfo(version=v, layout=layout, cached=cached))
    return out


def _has_cached_spec(version_dir: Path) -> bool:
    if not version_dir.is_dir():
        return False
    return any(any(version_dir.glob(f"*.{ext}")) for ext in ("yaml", "yml", "json"))


def default_target_path(specs_dir: Path, version: str) -> Path:
    """The canonical on-disk path for a stitched spec."""
    flat = version.replace(".", "")
    return specs_dir / version / f"vmanageapi_{flat}.yaml"


async def fetch_version(
    version: str,
    *,
    specs_dir: Path,
    force: bool = False,
    use_cache: bool = True,
    cache_root: Path = DEFAULT_CACHE_ROOT,
    concurrency: int = 10,
    timeout: float = 60.0,
    verify_ssl: bool = True,
    log: bool = True,
    min_paths: int | None = None,
    min_yaml_bytes: int | None = None,
) -> Path:
    """Fetch + stitch + write the split-spec YAML for ``version``.

    Parameters
    ----------
    version:
        e.g. ``"20.18"`` or ``"20.19"``.
    specs_dir:
        Root directory where ``specs/<version>/vmanageapi_<flat>.yaml`` will be
        written. Created if missing.
    force:
        If true, refetch and overwrite even when the YAML already exists.
    use_cache:
        If true, cache individual fragment JSONs under
        ``~/.cache/sdwan-mcp/fragments/<version>/``. The implicit auto-fetch
        path passes ``False`` so a transient run leaves nothing behind.
    log:
        If true, prints progress lines to stderr in the ``[fetch]`` style.

    Returns the absolute path of the written YAML.
    """
    target = default_target_path(specs_dir, version)
    if target.exists() and not force:
        _log(log, f"[fetch] reusing cached spec at {target}")
        return target

    cache_dir = cache_root / version if use_cache else None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)

    _log(log, f"[fetch] discovering fragments for {version} from {discovery_url(version)}")
    async with make_client(timeout=timeout, verify_ssl=verify_ssl) as client:
        html = await fetch_discovery_html(client, url=discovery_url(version))
        disc = parse_discovery_html(html, version)
        _log(
            log,
            f"[fetch] found {len(disc.api_fragments)} ops + {len(disc.model_fragments)} "
            f"models across {len({f.uuid for f in disc.all_fragments})} sections",
        )

        progress = _make_progress_logger(log)
        bodies = await fetch_fragments(
            client,
            refs=list(disc.all_fragments),
            concurrency=concurrency,
            cache_dir=cache_dir,
            progress_cb=progress,
        )

    op_pairs = [(ref, bodies[ref.url]) for ref in disc.api_fragments]
    model_pairs = [(ref, bodies[ref.url]) for ref in disc.model_fragments]
    _log(log, "[fetch] stitching fragments")
    doc = stitch(version=version, op_fragments=op_pairs, model_fragments=model_pairs)

    target.parent.mkdir(parents=True, exist_ok=True)
    n_bytes = write_yaml(doc, target)
    _log(log, f"[fetch] wrote {target} ({n_bytes:,} bytes)")

    validate_kwargs: dict[str, int] = {}
    if min_paths is not None:
        validate_kwargs["min_paths"] = min_paths
    if min_yaml_bytes is not None:
        validate_kwargs["min_yaml_bytes"] = min_yaml_bytes
    warnings = validate(doc, yaml_bytes=n_bytes, **validate_kwargs)
    for w in warnings:
        _log(log, f"[fetch] WARNING: {w}")
    return target


def _make_progress_logger(log: bool) -> Callable[[FetchProgress], None] | None:
    if not log:
        return None
    last_bucket = [-1]

    def _cb(ev: FetchProgress) -> None:
        if ev.total == 0:
            return
        bucket = int(ev.completed * 10 / ev.total)
        if bucket != last_bucket[0]:
            last_bucket[0] = bucket
            pct = int(ev.completed * 100 / ev.total)
            _log(True, f"[fetch] {ev.completed}/{ev.total} ({pct}%) fragments")

    return _cb


def _log(enabled: bool, msg: str) -> None:
    if enabled:
        print(msg, file=sys.stderr, flush=True)


async def fetch_version_safe(
    version: str,
    *,
    specs_dir: Path,
    force: bool = False,
    use_cache: bool = True,
    log: bool = True,
    verify_ssl: bool = True,
) -> Path:
    """Variant of :func:`fetch_version` that re-raises with a friendlier message."""
    try:
        return await fetch_version(
            version,
            specs_dir=specs_dir,
            force=force,
            use_cache=use_cache,
            log=log,
            verify_ssl=verify_ssl,
        )
    except (DiscoveryError, FetchError, StitchError, FetcherValidationError) as exc:
        raise FetchError(
            f"Could not fetch spec for vManage {version}: {exc}.\n"
            f"Run `sdwan-mcp fetch --version {version}` manually for a full trace."
        ) from exc
    except httpx.HTTPError as exc:
        raise FetchError(f"Network error fetching spec for vManage {version}: {exc}") from exc
