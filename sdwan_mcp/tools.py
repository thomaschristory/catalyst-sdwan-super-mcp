"""
tools.py — dynamically registers one FastMCP tool per ToolGroup.

Tool shape:
  name:        group slug  (e.g. "monitoring_device_details")
  description: lists all actions with params, generated from the spec
  args:
    action:    str — one of the derived action_names in this group
    params:    dict — keys/values vary by action, documented in description

NOTE: Each handler is built by a factory (_make_tool_handler) so it closes over
its own group's values in a fresh scope. This avoids the classic loop-variable
aliasing bug WITHOUT leaking internal objects (dispatcher, valid_actions) into
the handler signature — fastmcp 3.x introspects the signature to build the tool
schema and cannot generate a pydantic schema for arbitrary types like Dispatcher
(see #52).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastmcp import FastMCP

from .dispatcher import Dispatcher, DispatchResult
from .loader import (
    BodyFieldSpec,
    OperationSpec,
    ParameterSpec,
    SpecIndex,
    ToolGroup,
    is_stats_query_body,
)

# The statistics-DB query DSL accepts a fixed set of top-level fields, but Cisco's
# spec declares these bodies as a bare object — so we bake the known list into the
# description for the stats family (#78, item 2).
_STATS_DB_QUERY_FIELDS = "size, aggregation, plot_data, fields, category, query, sort"

# ---------------------------------------------------------------------------
# Description builder
# ---------------------------------------------------------------------------


def _format_param(p: ParameterSpec) -> str:
    req = "" if p.required else "?"
    desc = f" — {p.description}" if p.description else ""
    default = f" (default: {p.default})" if p.default is not None else ""
    return f"{p.name}{req}: {p.type}{desc}{default}"


def _format_body_field(f: BodyFieldSpec) -> str:
    req = "" if f.required else "?"
    return f"{f.name}{req}: {f.type}"


def _format_body(op: OperationSpec) -> str:
    """Render the request body for a POST/PUT/PATCH action — terse.

    Body fields are TOP-LEVEL keys of ``params``, not nested under a ``body`` key.
    The old ``body: object`` rendering read as "pass {"body": {...}}" and
    double-wrapped the payload, which vManage rejected (#78). We name the real
    fields inline when the spec describes them, else fall back to ``body(JSON)``;
    the one-line convention ("…go in params at the top level, not under a 'body'
    key") is stated once per tool in the trailing guidance rather than repeated on
    every action, to keep the description compact.
    """
    if op.body_fields:
        fields = ", ".join(_format_body_field(f) for f in op.body_fields)
        return f"body fields (top-level): {fields}"
    if is_stats_query_body(op):
        return f"body fields (top-level, stats-DB query DSL): {_STATS_DB_QUERY_FIELDS}"
    return "body fields (top-level): opaque JSON object — see action summary"


_PAGINATION_HINT = (
    "Pagination: paginated actions auto-stitch up to N pages and return "
    "{data, pagination: {...}}. Override per call with _max_pages, _page_size, "
    'or _pagination: "off".'
)


def _build_description(group: ToolGroup) -> str:
    lines = [group.display_tag, "", _PAGINATION_HINT, "", "Actions:"]

    for op in group.operations:
        path_params = [p for p in op.parameters if p.location == "path"]
        query_params = [p for p in op.parameters if p.location == "query"]

        param_parts = []
        for p in path_params:
            param_parts.append(_format_param(p))
        for p in query_params:
            param_parts.append(_format_param(p))
        if op.has_body:
            param_parts.append(_format_body(op))

        params_str = ", ".join(param_parts) if param_parts else ""
        summary = op.summary.strip() if op.summary else ""

        lines.append(f"  - {op.action_name}({params_str}) [{op.method.upper()}]")
        if summary:
            lines.append(f"    {summary}")

    lines.append("")
    lines.append("Pass 'action' as one of the action names above.")
    lines.append("Pass 'params' as a dict matching the action's parameter list.")
    # Only worth the tokens when the tool actually has a body-bearing action —
    # in the default read-only mode most tools have none (#78 review).
    if any(op.has_body for op in group.operations):
        lines.append(
            "For POST/PUT/PATCH, put the request-body fields directly in 'params' at the "
            "top level — do NOT nest them under a 'body' key."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_tools(mcp: FastMCP, index: SpecIndex, dispatcher: Dispatcher) -> int:
    """Register one MCP tool per ToolGroup. Returns the number registered."""
    for group in index.groups:
        _register_group_tool(mcp, group, dispatcher)

    count = len(index.groups)
    print(f"[tools] Registered {count} MCP tools")
    return count


def _make_tool_handler(
    tool_name: str,
    valid_actions: frozenset[str],
    dispatcher: Dispatcher,
) -> Callable[[str, dict[str, Any] | None], Awaitable[DispatchResult]]:
    """Build a handler that closes over this group's values in a fresh scope.

    The factory call gives each handler its own binding of tool_name/
    valid_actions/dispatcher, so the only parameters fastmcp sees on the
    signature are `action` and `params` (see module docstring / #52)."""

    async def tool_handler(
        action: str,
        params: dict[str, Any] | None = None,
    ) -> DispatchResult:
        if action not in valid_actions:
            return {
                "error": True,
                "message": (
                    f"Unknown action '{action}' for tool '{tool_name}'. "
                    f"Valid actions: {sorted(valid_actions)}"
                ),
            }
        return await dispatcher.call(action, params or {}, tool_name=tool_name)

    return tool_handler


def _register_group_tool(
    mcp: FastMCP,
    group: ToolGroup,
    dispatcher: Dispatcher,
) -> None:
    tool_name = group.name
    description = _build_description(group)
    valid_actions = frozenset(op.action_name for op in group.operations)

    tool_handler = _make_tool_handler(tool_name, valid_actions, dispatcher)
    tool_handler.__name__ = tool_name
    tool_handler.__doc__ = description

    mcp.tool(name=tool_name, description=description)(tool_handler)
