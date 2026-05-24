from pathlib import Path

import pytest
import yaml

from sdwan_mcp.loader import SpecLoader
from sdwan_mcp.tools import _build_description


@pytest.fixture
def tiny_index(tmp_path: Path):
    version_dir = tmp_path / "specs" / "20.99"
    version_dir.mkdir(parents=True)
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "t", "version": "1.0"},
        "paths": {
            "/alarms": {
                "get": {
                    "tags": ["Monitoring - Alarms"],
                    "operationId": "getAlarms",
                    "parameters": [
                        {"name": "scrollId", "in": "query", "schema": {"type": "string"}},
                    ],
                }
            }
        },
    }
    (version_dir / "ops.yaml").write_text(yaml.safe_dump(spec))
    return SpecLoader(str(tmp_path / "specs"), "20.99", read_write=False).load()


def test_description_includes_pagination_note(tiny_index):
    group = tiny_index.groups[0]
    desc = _build_description(group)
    assert "Pagination:" in desc
    assert "_max_pages" in desc
    assert "_pagination" in desc
