from pathlib import Path
from typing import Any

import pytest
import yaml
from fastmcp import FastMCP

from sdwan_mcp.loader import SpecLoader
from sdwan_mcp.tools import _build_description, register_tools


class _StubDispatcher:
    """Stand-in for Dispatcher; registration never invokes it."""

    async def call(
        self, action: str, params: dict[str, Any], tool_name: str | None = None
    ) -> dict[str, Any]:
        return {"action": action, "params": params, "tool_name": tool_name}


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
    assert len(tiny_index.groups) >= 1
    assert count == len(tiny_index.groups)


@pytest.mark.asyncio
async def test_registered_tool_schema_exposes_only_action_and_params(tiny_index):
    mcp = FastMCP("test")
    register_tools(mcp, tiny_index, _StubDispatcher())
    tool_name = tiny_index.groups[0].name
    tool = await mcp.get_tool(tool_name)
    assert set(tool.parameters["properties"]) == {"action", "params"}


# ---------------------------------------------------------------------------
# POST body rendering: top-level convention, no misleading `body: object` (#78)
# ---------------------------------------------------------------------------

from sdwan_mcp.loader import BodyFieldSpec, OperationSpec, ToolGroup  # noqa: E402
from sdwan_mcp.tools import _build_description as _bd  # noqa: E402


def _post_group(op: OperationSpec) -> ToolGroup:
    return ToolGroup(name="stats", display_tag="Statistics", operations=[op])


def test_description_lists_known_body_fields_at_top_level() -> None:
    op = OperationSpec(
        operation_id="x",
        action_name="post_interface_aggregation",
        summary="",
        method="post",
        path="/statistics/interface/aggregation",
        tag="t",
        has_body=True,
        body_description="Query filter",
        body_fields=[
            BodyFieldSpec(name="query", type="object", required=True),
            BodyFieldSpec(name="aggregation", type="object"),
            BodyFieldSpec(name="size", type="integer"),
        ],
    )
    desc = _bd(_post_group(op))
    # Real field names are surfaced; the misleading `body: object` is gone.
    assert "body: object" not in desc
    assert "query" in desc and "aggregation" in desc and "size" in desc
    # Required marker distinguishes mandatory fields.
    assert "query: object" in desc and "query?" not in desc  # required, no '?'
    # The top-level convention is stated once at the tool level (not per action).
    assert "top level" in desc
    assert "do NOT nest them under a 'body' key" in desc


def test_description_body_without_known_fields_still_warns_top_level() -> None:
    op = OperationSpec(
        operation_id="x",
        action_name="post_thing",
        summary="",
        method="post",
        path="/thing",
        tag="t",
        has_body=True,
        body_description="Device config",
        body_fields=[],
    )
    desc = _bd(_post_group(op))
    assert "body: object" not in desc
    # No invented fields, but the top-level convention is still stated once.
    assert "top level" in desc
    assert "do NOT nest them under a 'body' key" in desc


def test_description_bakes_stats_query_fields() -> None:
    """A stats POST with a bare-object body (no spec fields) still names the known
    stats-DB query DSL fields instead of leaving the LLM to guess (#78 item 2)."""
    op = OperationSpec(
        operation_id="x",
        action_name="post_interface_aggregation",
        summary="",
        method="post",
        path="/statistics/interface/aggregation",
        tag="t",
        has_body=True,
        body_fields=[],
    )
    desc = _bd(_post_group(op))
    assert "stats-DB query DSL" in desc
    for f in ("size", "aggregation", "plot_data", "fields", "category", "query", "sort"):
        assert f in desc


def test_body_note_omitted_when_no_body_action(tiny_index) -> None:
    """The /alarms tiny_index group is GET-only — the POST/PUT/PATCH body note
    must not be spent on it (#78 review: token economy)."""
    desc = _build_description(tiny_index.groups[0])
    assert "do NOT nest them under a 'body' key" not in desc
