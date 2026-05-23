"""
server.py — entrypoint for the SD-WAN MCP server.

Usage:
  sdwan-mcp                                          # stdio, RO, version from config
  sdwan-mcp --transport sse --port 8000              # SSE transport
  sdwan-mcp --transport streamable-http              # streamable HTTP
  sdwan-mcp --read-write                             # enable mutations
  sdwan-mcp --version 20.18                          # override spec version
  sdwan-mcp --diff 20.15 20.18                       # diff two versions and exit
  sdwan-mcp --config path/to/config.yaml             # custom config file
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Literal, cast

from dotenv import load_dotenv
from fastmcp import FastMCP

from . import __version__
from .auth import VManageAuth
from .config import load_config
from .diff import diff_versions, print_diff
from .dispatcher import Dispatcher
from .loader import SpecLoader
from .tools import register_tools

TransportMode = Literal["stdio", "sse", "streamable-http"]

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="sdwan-mcp",
        description="SD-WAN Super MCP — Cisco Catalyst SD-WAN Manager (vManage) MCP server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--version-info", action="version", version=f"sdwan-mcp {__version__}")
    parser.add_argument(
        "--config",
        default="config.yaml",
        metavar="PATH",
        help="Path to config.yaml (default: ./config.yaml)",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default=None,
        help="Transport mode — overrides config.yaml (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Bind host for HTTP transports (default from config)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind port for HTTP transports (default from config)",
    )
    parser.add_argument(
        "--read-write",
        action="store_true",
        default=False,
        help="Enable write operations (POST/PUT/DELETE/PATCH). Default is read-only.",
    )
    parser.add_argument(
        "--version",
        default=None,
        metavar="VERSION",
        help="Spec version to load, e.g. '20.18' — overrides config.yaml",
    )
    parser.add_argument(
        "--diff",
        nargs=2,
        metavar=("OLD", "NEW"),
        help="Compare two spec versions and exit. Example: --diff 20.15 20.18",
    )
    parser.add_argument(
        "--granularity",
        choices=["section", "tag"],
        default=None,
        help=(
            "Tool grouping granularity: 'section' (~30-40 tools, default) "
            "or 'tag' (300+ tools). Overrides config.yaml."
        ),
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Diff mode
# ---------------------------------------------------------------------------


def run_diff(specs_dir: str, old_version: str, new_version: str) -> None:
    print(f"Comparing specs: {old_version} -> {new_version}")
    diff = diff_versions(specs_dir, old_version, new_version)
    print_diff(diff)


# ---------------------------------------------------------------------------
# Server startup
# ---------------------------------------------------------------------------


async def _connect_and_register(
    args: argparse.Namespace,
) -> tuple[FastMCP, Dispatcher, TransportMode, str, int]:
    """Async pre-flight: load config, log in to vManage, register tools."""
    load_dotenv()
    config = load_config(args.config)

    version = args.version or config.sdwan.active_version
    transport = args.transport or config.transport.mode
    if transport not in {"stdio", "sse", "streamable-http"}:
        raise ValueError(f"Unsupported transport: {transport}")
    transport_mode = cast(TransportMode, transport)
    host = args.host or config.transport.host
    port = args.port or config.transport.port
    read_write = args.read_write

    print("[server] SD-WAN Super MCP")
    print(f"[server] Spec version : {version}")
    print(f"[server] Mode         : {'READ-WRITE' if read_write else 'READ-ONLY'}")
    print(f"[server] Transport    : {transport_mode}")
    print(f"[server] Auth         : {'JWT' if config.vmanage.use_jwt else 'Session'}")
    if transport_mode != "stdio":
        print(f"[server] Listening on : {host}:{port}")
    print()

    granularity = args.granularity or config.sdwan.tag_granularity

    loader = SpecLoader(
        specs_dir=config.sdwan.specs_dir,
        version=version,
        read_write=read_write,
        granularity=granularity,
    )
    index = loader.load()

    auth = VManageAuth(
        host=config.vmanage.host,
        port=config.vmanage.port,
        username=config.vmanage.username,
        password=config.vmanage.password,
        verify_ssl=config.vmanage.verify_ssl,
        use_jwt=config.vmanage.use_jwt,
    )
    dispatcher = Dispatcher(
        base_url=config.vmanage.base_url,
        auth=auth,
        verify_ssl=config.vmanage.verify_ssl,
    )
    dispatcher.set_index(index)
    await dispatcher.connect()

    mcp = FastMCP(
        name="sdwan",
        instructions=(
            f"You are connected to a Cisco Catalyst SD-WAN Manager (vManage) "
            f"running API version {version}. "
            f"Mode: {'read-write' if read_write else 'read-only'}. "
            "Use the available tools to query and manage the SD-WAN overlay network."
        ),
    )

    tool_count = register_tools(mcp, index, dispatcher)
    print(f"[server] {tool_count} tools registered — starting {transport_mode} transport\n")

    return mcp, dispatcher, transport_mode, host, port


def build_and_run(args: argparse.Namespace) -> None:
    """FastMCP.run() owns its own event loop, so async pre-flight runs first."""
    mcp, dispatcher, transport, host, port = asyncio.run(_connect_and_register(args))

    try:
        if transport == "stdio":
            mcp.run()
        else:
            mcp.run(transport=transport, host=host, port=port)
    finally:
        try:
            asyncio.run(dispatcher.close())
        except Exception as exc:  # pragma: no cover - best effort
            print(f"[server] WARNING: shutdown error: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.diff:
        load_dotenv()
        config = load_config(args.config)
        run_diff(config.sdwan.specs_dir, args.diff[0], args.diff[1])
        return 0

    build_and_run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
