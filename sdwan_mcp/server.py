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

  sdwan-mcp fetch --version 20.19                    # download + stitch spec
  sdwan-mcp fetch --all-known                        # pre-warm every known version
  sdwan-mcp list-versions                            # show known versions + cache status
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Literal, cast

from dotenv import load_dotenv
from fastmcp import FastMCP
from starlette.middleware import Middleware

from . import __version__
from .auth import VManageAuth
from .config import AppConfig, load_config
from .diff import diff_versions, print_diff
from .dispatcher import Dispatcher
from .fetcher import (
    KNOWN_VERSIONS,
    FetchError,
    fetch_version_safe,
    list_known_versions,
)
from .loader import SpecLoader
from .tools import register_tools
from .transport_auth import BearerAuthMiddleware, decide_bind

TransportMode = Literal["stdio", "sse", "streamable-http"]
_VALID_TRANSPORTS: frozenset[str] = frozenset({"stdio", "sse", "streamable-http"})

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


_SUBCOMMANDS: frozenset[str] = frozenset({"fetch", "list-versions"})


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
        "--max-actions-per-tool",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Adaptive splitter cap: any tool with more than N actions is split further. "
            "0 disables splitting (one tool per section). Overrides config.yaml (default 150)."
        ),
    )
    parser.add_argument(
        "--insecure-allow-public",
        action="store_true",
        default=False,
        help=(
            "Allow binding to a non-loopback host with transport.auth.type=none. "
            "Without this flag, such a bind is auto-demoted to 127.0.0.1."
        ),
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Subcommand parsers (fetch, list-versions)
# ---------------------------------------------------------------------------


def _build_fetch_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sdwan-mcp fetch",
        description=(
            "Download the OpenAPI fragments for a vManage version from "
            "developer.cisco.com and stitch them into a single YAML under "
            "specs/<version>/."
        ),
    )
    p.add_argument(
        "--config",
        default="config.yaml",
        metavar="PATH",
        help="Path to config.yaml (default: ./config.yaml). Used only to "
        "resolve sdwan.specs_dir; vManage credentials are not required.",
    )
    p.add_argument(
        "--version",
        metavar="VERSION",
        help="Spec version to fetch, e.g. '20.19'.",
    )
    p.add_argument(
        "--all-known",
        action="store_true",
        help="Fetch every version in KNOWN_VERSIONS (skips ones already cached).",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Refetch even if specs/<version>/ already has a YAML.",
    )
    p.add_argument(
        "--no-fragment-cache",
        action="store_true",
        help="Do not write per-fragment JSONs to ~/.cache/sdwan-mcp/fragments/.",
    )
    return p


def _build_list_versions_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sdwan-mcp list-versions",
        description="Print known spec versions and on-disk cache status.",
    )
    p.add_argument(
        "--config",
        default="config.yaml",
        metavar="PATH",
        help="Path to config.yaml (default: ./config.yaml).",
    )
    return p


# ---------------------------------------------------------------------------
# Diff mode
# ---------------------------------------------------------------------------


def run_diff(specs_dir: str, old_version: str, new_version: str) -> None:
    print(f"Comparing specs: {old_version} -> {new_version}")
    diff = diff_versions(specs_dir, old_version, new_version)
    print_diff(diff)


# ---------------------------------------------------------------------------
# Fetch subcommand handlers
# ---------------------------------------------------------------------------


def _load_config_or_default(config_path: str) -> AppConfig:
    """Load config if present; otherwise return defaults (fetch needs no vManage creds)."""
    try:
        return load_config(config_path)
    except FileNotFoundError:
        return AppConfig()


def run_fetch(argv: list[str]) -> int:
    parser = _build_fetch_parser()
    args = parser.parse_args(argv)
    if not args.version and not args.all_known:
        parser.error("specify --version VERSION or --all-known")
    load_dotenv()
    config = _load_config_or_default(args.config)
    specs_dir = Path(config.sdwan.specs_dir)
    versions = list(KNOWN_VERSIONS) if args.all_known else [args.version]

    async def _runner() -> int:
        rc = 0
        for v in versions:
            try:
                target = await fetch_version_safe(
                    v,
                    specs_dir=specs_dir,
                    force=args.force,
                    use_cache=not args.no_fragment_cache,
                    log=True,
                    verify_ssl=True,
                )
                print(f"[fetch] OK  {v} -> {target}", file=sys.stderr)
            except FetchError as exc:
                print(f"[fetch] FAIL {v}: {exc}", file=sys.stderr)
                rc = 1
        return rc

    return asyncio.run(_runner())


def run_list_versions(argv: list[str]) -> int:
    parser = _build_list_versions_parser()
    args = parser.parse_args(argv)
    config = _load_config_or_default(args.config)
    rows = list_known_versions(Path(config.sdwan.specs_dir))
    width = max(len(r.version) for r in rows)
    for r in rows:
        cached = "cached    " if r.cached else "not cached"
        print(f"{r.version:<{width}}  {r.layout:<8}  {cached}")
    return 0


# ---------------------------------------------------------------------------
# Server startup
# ---------------------------------------------------------------------------


async def _connect_and_register(
    args: argparse.Namespace,
) -> tuple[FastMCP, Dispatcher, TransportMode, str, int, list[Middleware]]:
    """Async pre-flight: load config, log in to vManage, register tools."""
    load_dotenv()
    config = load_config(args.config)

    version = args.version or config.sdwan.active_version
    transport = args.transport or config.transport.mode
    if transport not in _VALID_TRANSPORTS:
        raise ValueError(
            f"Unsupported transport: {transport!r}. Choose one of {sorted(_VALID_TRANSPORTS)}."
        )
    transport_mode = cast(TransportMode, transport)
    host = args.host or config.transport.host
    port = args.port or config.transport.port
    read_write = args.read_write

    middleware_list: list[Middleware] = []
    if transport_mode != "stdio":
        effective_host, bind_warnings = decide_bind(
            host=host,
            auth_type=config.transport.auth.type,
            insecure_ok=args.insecure_allow_public,
        )
        for line in bind_warnings:
            print(f"[server] WARNING: {line}", file=sys.stderr)
        host = effective_host

        if config.transport.auth.type == "bearer":
            middleware_list.append(
                Middleware(
                    BearerAuthMiddleware,
                    expected_token=config.transport.auth.token,
                )
            )

    print("[server] SD-WAN Super MCP")
    print(f"[server] Spec version : {version}")
    print(f"[server] Mode         : {'READ-WRITE' if read_write else 'READ-ONLY'}")
    print(f"[server] Transport    : {transport_mode}")
    print(f"[server] vManage Auth : {'JWT' if config.vmanage.use_jwt else 'Session'}")
    if transport_mode != "stdio":
        print(f"[server] HTTP Auth    : {config.transport.auth.type}")
        print(f"[server] Listening on : {host}:{port}")
    print()

    max_actions = (
        args.max_actions_per_tool
        if args.max_actions_per_tool is not None
        else config.sdwan.max_actions_per_tool
    )

    try:
        loader = SpecLoader(
            specs_dir=config.sdwan.specs_dir,
            version=version,
            read_write=read_write,
            max_actions_per_tool=max_actions,
        )
    except FileNotFoundError:
        if not config.sdwan.auto_fetch:
            raise
        print(
            f"[server] specs/{version}/ not found — auto-fetching from "
            "developer.cisco.com (set sdwan.auto_fetch: false to disable)",
            file=sys.stderr,
        )
        await fetch_version_safe(
            version,
            specs_dir=Path(config.sdwan.specs_dir),
            use_cache=False,
            log=True,
            overall_timeout=300.0,
        )
        loader = SpecLoader(
            specs_dir=config.sdwan.specs_dir,
            version=version,
            read_write=read_write,
            max_actions_per_tool=max_actions,
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
        timeout=config.vmanage.timeout,
        pagination=config.sdwan.pagination,
        retry=config.vmanage.retries,
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

    return mcp, dispatcher, transport_mode, host, port, middleware_list


def build_and_run(args: argparse.Namespace) -> None:
    """FastMCP.run() owns its own event loop, so async pre-flight runs first."""
    mcp, dispatcher, transport, host, port, middleware = asyncio.run(_connect_and_register(args))

    try:
        if transport == "stdio":
            mcp.run()
        else:
            mcp.run(
                transport=transport,
                host=host,
                port=port,
                middleware=middleware or None,
            )
    finally:
        try:
            asyncio.run(dispatcher.close())
        except Exception as exc:  # pragma: no cover - best effort
            print(f"[server] WARNING: shutdown error: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    if raw and raw[0] in _SUBCOMMANDS:
        cmd, rest = raw[0], raw[1:]
        if cmd == "fetch":
            return run_fetch(rest)
        if cmd == "list-versions":
            return run_list_versions(rest)

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
