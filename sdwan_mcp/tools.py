"""
tools.py — dynamically registers one FastMCP tool per ToolGroup.

Tool shape:
  name:        group slug  (e.g. "monitoring_device_details")
  description: lists all actions with params, generated from the spec
  args:
    action:    str — one of the derived action_names in this group
    params:    dict — keys/values vary by action, documented in description

NOTE: The default-arg capture pattern (_group=group, _dispatcher=dispatcher,
_valid=valid_actions) is intentional. Python closures capture variables by
reference, so without it every tool handler would point to the last group
in the loop. The default arg forces value capture at definition time.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from .dispatcher import Dispatcher, DispatchResult
from .loader import ParameterSpec, SpecIndex, ToolGroup

# ---------------------------------------------------------------------------
# Description builder
# ---------------------------------------------------------------------------


def _format_param(p: ParameterSpec) -> str:
    req = "" if p.required else "?"
    desc = f" — {p.description}" if p.description else ""
    default = f" (default: {p.default})" if p.default is not None else ""
    return f"{p.name}{req}: {p.type}{desc}{default}"


def _build_description(group: ToolGroup) -> str:
    lines = [group.display_tag, "", "Actions:"]

    for op in group.operations:
        path_params = [p for p in op.parameters if p.location == "path"]
        query_params = [p for p in op.parameters if p.location == "query"]

        param_parts = []
        for p in path_params:
            param_parts.append(_format_param(p))
        for p in query_params:
            param_parts.append(_format_param(p))
        if op.has_body:
            param_parts.append(f"body: object — {op.body_description}")

        params_str = ", ".join(param_parts) if param_parts else ""
        summary = op.summary.strip() if op.summary else ""

        lines.append(f"  - {op.action_name}({params_str}) [{op.method.upper()}]")
        if summary:
            lines.append(f"    {summary}")

    lines.append("")
    lines.append("Pass 'action' as one of the action names above.")
    lines.append("Pass 'params' as a dict matching the action's parameter list.")

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


def _register_group_tool(
    mcp: FastMCP,
    group: ToolGroup,
    dispatcher: Dispatcher,
) -> None:
    tool_name = group.name
    description = _build_description(group)
    valid_actions = frozenset(op.action_name for op in group.operations)

    # See module docstring: default args force value capture at definition time.
    async def tool_handler(
        action: str,
        params: dict[str, Any] | None = None,
        _valid: frozenset[str] = valid_actions,
        _name: str = tool_name,
        _dispatcher: Dispatcher = dispatcher,
    ) -> DispatchResult:
        if action not in _valid:
            return {
                "error": True,
                "message": (
                    f"Unknown action '{action}' for tool '{_name}'. Valid actions: {sorted(_valid)}"
                ),
            }
        return await _dispatcher.call(action, params or {})

    tool_handler.__name__ = tool_name
    tool_handler.__doc__ = description

    mcp.tool(name=tool_name, description=description)(tool_handler)
