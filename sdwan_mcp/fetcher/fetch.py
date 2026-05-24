"""HTTP fetch layer for the spec fetcher.

Concerns:
- Async, bounded-concurrency download of the discovery HTML + every fragment.
- Retry/backoff on transient HTTP failures (5xx / 429 / network).
- Optional fragment disk cache at ``~/.cache/sdwan-mcp/fragments/{V}/``.
- Atomic write of the final stitched YAML (tempfile + rename).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml

from .discover import FragmentRef

DEFAULT_CACHE_ROOT = Path.home() / ".cache" / "sdwan-mcp" / "fragments"
DEFAULT_USER_AGENT = "catalyst-sdwan-super-mcp/fetcher (+https://github.com/thomaschristory/catalyst-sdwan-super-mcp)"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 4
BACKOFF_BASE = 0.5
BACKOFF_CAP = 8.0


@dataclass(frozen=True)
class FetchProgress:
    """Lightweight progress event for stderr reporting."""

    completed: int
    total: int
    last_url: str | None = None


ProgressCallback = Callable[[FetchProgress], None]


class FetchError(RuntimeError):
    """Raised when a fragment cannot be retrieved after all retries."""


async def fetch_discovery_html(
    client: httpx.AsyncClient,
    *,
    url: str,
) -> str:
    resp = await _request_with_retry(client, "GET", url)
    return resp.text


async def fetch_fragments(
    client: httpx.AsyncClient,
    *,
    refs: list[FragmentRef],
    concurrency: int = 10,
    cache_dir: Path | None = None,
    progress_cb: ProgressCallback | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetch every fragment and return ``{ref.url: parsed_json_body}``."""
    sem = asyncio.Semaphore(max(1, concurrency))
    results: dict[str, dict[str, Any]] = {}
    counter = {"done": 0}
    total = len(refs)

    async def _one(ref: FragmentRef) -> None:
        async with sem:
            body = await _fetch_one_fragment(client, ref, cache_dir)
            results[ref.url] = body
            counter["done"] += 1
            if progress_cb is not None:
                progress_cb(FetchProgress(counter["done"], total, ref.url))

    await asyncio.gather(*(_one(r) for r in refs))
    return results


async def _fetch_one_fragment(
    client: httpx.AsyncClient,
    ref: FragmentRef,
    cache_dir: Path | None,
) -> dict[str, Any]:
    cache_path = _cache_path_for(ref, cache_dir)
    if cache_path is not None and cache_path.exists():
        try:
            return _load_json(cache_path)
        except (OSError, json.JSONDecodeError):
            # Corrupt cache file — fall through to re-fetch.
            cache_path.unlink(missing_ok=True)

    resp = await _request_with_retry(client, "GET", ref.url)
    try:
        body = resp.json()
    except json.JSONDecodeError as exc:
        raise FetchError(f"Fragment at {ref.url} did not return valid JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise FetchError(f"Fragment at {ref.url} is not a JSON object")

    if cache_path is not None:
        _atomic_write_bytes(cache_path, json.dumps(body, separators=(",", ":")).encode())

    return body


async def _request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        retry_after: float | None = None
        try:
            resp = await client.request(method, url)
            if resp.status_code < 400:
                return resp
            if resp.status_code not in RETRY_STATUSES:
                raise FetchError(f"{method} {url} failed with HTTP {resp.status_code}")
            retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
            last_exc = FetchError(f"{method} {url} returned HTTP {resp.status_code} (retryable)")
        except httpx.RequestError as exc:
            last_exc = exc
        if attempt < MAX_ATTEMPTS - 1:
            await _sleep_backoff(attempt, override=retry_after)
    assert last_exc is not None
    raise FetchError(
        f"{method} {url} failed after {MAX_ATTEMPTS} attempts: {last_exc}"
    ) from last_exc


def _parse_retry_after(raw: str | None) -> float | None:
    """Parse the ``Retry-After`` header; only integer-seconds form is honoured."""
    if raw is None:
        return None
    try:
        seconds = float(raw.strip())
    except ValueError:
        # We don't bother with HTTP-date form; backoff is already conservative.
        return None
    if seconds < 0:
        return None
    # Refuse absurdly long server hints; we cap at our own backoff_cap * 4.
    return min(seconds, BACKOFF_CAP * 4)


async def _sleep_backoff(attempt: int, *, override: float | None = None) -> None:
    if override is not None:
        await asyncio.sleep(override)
        return
    raw = min(BACKOFF_CAP, BACKOFF_BASE * (2**attempt))
    half = raw / 2
    delay = half + random.uniform(0, half)
    await asyncio.sleep(delay)


def _cache_path_for(ref: FragmentRef, cache_dir: Path | None) -> Path | None:
    """Map a fragment ref to its on-disk cache path, refusing path traversal.

    ``ref.rest`` comes from a regex over the upstream HTML. We trust the regex
    to exclude double-quotes, but it does not exclude ``..``. A malicious or
    misconfigured upstream could otherwise produce a path that escapes
    ``cache_dir`` once joined and resolved.
    """
    if cache_dir is None:
        return None
    cache_root = cache_dir.resolve()
    candidate = (cache_root / ref.uuid / ref.kind / ref.rest).resolve()
    # candidate must be strictly under cache_root
    try:
        candidate.relative_to(cache_root)
    except ValueError as exc:
        raise FetchError(
            f"Refusing to cache fragment outside cache dir (suspected path "
            f"traversal): rest={ref.rest!r}"
        ) from exc
    return candidate


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data: Any = json.load(f)
    if not isinstance(data, dict):
        raise json.JSONDecodeError("expected object", str(path), 0)
    return data


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp_name, target)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def write_yaml(doc: dict[str, Any], target: Path) -> int:
    """Dump ``doc`` to ``target`` atomically. Returns the number of bytes written."""
    payload = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True).encode("utf-8")
    _atomic_write_bytes(target, payload)
    return len(payload)


def make_client(*, timeout: float = 60.0, verify_ssl: bool = True) -> httpx.AsyncClient:
    """Construct an httpx.AsyncClient pre-configured for DevNet fetches."""
    return httpx.AsyncClient(
        timeout=timeout,
        verify=verify_ssl,
        follow_redirects=True,
        headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "*/*"},
        http2=False,
    )
