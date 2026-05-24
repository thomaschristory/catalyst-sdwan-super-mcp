"""Live end-to-end test against developer.cisco.com.

Skipped unless ``RUN_LIVE_FETCH=1`` is set in the environment, because the
real fetch is ~150s and downloads ~166 MB of data. Useful for verifying the
fetcher still works after a DevNet SPA shape change.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sdwan_mcp.fetcher import fetch_version
from sdwan_mcp.loader import SpecLoader

LIVE = os.environ.get("RUN_LIVE_FETCH") == "1"
pytestmark = pytest.mark.skipif(not LIVE, reason="RUN_LIVE_FETCH=1 required")


async def test_live_fetch_20_18_then_load(tmp_path: Path) -> None:
    target = await fetch_version(
        "20.18",
        specs_dir=tmp_path,
        use_cache=False,
        log=False,
        timeout=180.0,
        concurrency=20,
    )
    assert target.exists()
    assert target.stat().st_size > 50_000_000  # at least 50 MB

    # SpecLoader must accept the result.
    loader = SpecLoader(
        specs_dir=str(tmp_path),
        version="20.18",
        read_write=True,
    )
    index = loader.load()
    # 20.18 is ~3700 ops across ~360 groups
    assert len(index.by_action_name) >= 3500
    assert len(index.groups) >= 300
