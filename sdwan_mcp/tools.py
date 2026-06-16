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
from .loader import ParameterSpec, SpecIndex, ToolGroup

# ---------------------------------------------------------------------------
# Description builder
# ---------------------------------------------------------------------------


def _format_param(p: ParameterSpec) -> str:
    req = "" if p.required else "?"
    desc = f" — {p.description}" if p.description else ""
    default = f" (default: {p.default})" if p.default is not None else ""
    return f"{p.name}{req}: {p.type}{desc}{default}"


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
