from pathlib import Path
from typing import Any

import pytest
import yaml
from fastmcp import FastMCP

from sdwan_mcp.loader import SpecLoader
from sdwan_mcp.tools import _build_description, register_tools


class _StubDispatcher:
    """Stand-in for Dispatcher; registration never invokes it."""

    async def call(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"action": action, "params": params}


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


def test_register_tools_does_not_leak_internal_params(tiny_index):
    """Regression for #52: fastmcp 3.x introspects the handler signature to
    build the tool schema. The value-capture default args must not leak the
    Dispatcher (or other internal closures) into the signature, or pydantic
    fails with PydanticSchemaGenerationError on the arbitrary type."""
    mcp = FastMCP("test")
    count = register_tools(mcp, tiny_index, _StubDispatcher())
    assert count == len(tiny_index.groups) >= 1


@pytest.mark.asyncio
async def test_registered_tool_schema_exposes_only_action_and_params(tiny_index):
    mcp = FastMCP("test")
    register_tools(mcp, tiny_index, _StubDispatcher())
    tool_name = tiny_index.groups[0].name
    tool = await mcp.get_tool(tool_name)
    assert set(tool.parameters["properties"]) == {"action", "params"}
