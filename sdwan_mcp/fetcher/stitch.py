"""Stitch downloaded fragments into a single OpenAPI document.

Each operation fragment looks like ``{type, title, meta, spec: {...}}`` where
``spec`` already contains a standard OpenAPI 3.x operation object, plus two
extra keys ``method`` and ``path`` that locate it in the merged ``paths`` dict.

Each model fragment has ``spec`` set to a JSON schema object. The schema name
comes from the fragment's filename (e.g. ``Device.json`` -> ``Device``).

``$ref`` strings inside fragments already point at ``#/components/schemas/X``,
so no rewriting is required — see ``docs/dev/issue-31-plan.md``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .discover import FragmentRef

JsonObj = dict[str, Any]


def stitch(
    *,
    version: str,
    op_fragments: Iterable[tuple[FragmentRef, JsonObj]],
    model_fragments: Iterable[tuple[FragmentRef, JsonObj]],
) -> JsonObj:
    """Build a single OpenAPI 3.1.0 document from fetched fragment bodies.

    Each argument is an iterable of ``(ref, fragment_body)`` tuples; the body
    is the parsed JSON of the fragment file. The fragments' ``meta`` is
    inspected for ``openapi`` version and ``servers``; the first non-empty
    value wins.
    """
    paths: dict[str, dict[str, JsonObj]] = {}
    schemas: dict[str, JsonObj] = {}
    tag_seen: dict[str, None] = {}  # insertion-ordered set
    openapi_version: str | None = None
    servers: list[JsonObj] | None = None

    for ref, body in op_fragments:
        spec = body.get("spec")
        if not isinstance(spec, dict):
            raise StitchError(f"Operation fragment {ref.rest} has no 'spec' object")
        op = dict(spec)
        method = op.pop("method", None)
        path = op.pop("path", None)
        if not isinstance(method, str) or not isinstance(path, str):
            raise StitchError(f"Operation fragment {ref.rest} missing method/path in spec")
        method_lower = method.lower()
        path_bucket = paths.setdefault(path, {})
        if method_lower in path_bucket:
            # Same operation declared twice; keep first, drop duplicate quietly.
            continue
        path_bucket[method_lower] = op
        for tag in op.get("tags", []) or []:
            if isinstance(tag, str):
                tag_seen.setdefault(tag, None)

        if openapi_version is None:
            openapi_version = _extract_openapi_version(body)
        if servers is None:
            servers = _extract_servers(body)

    for ref, body in model_fragments:
        spec = body.get("spec")
        if not isinstance(spec, dict):
            raise StitchError(f"Model fragment {ref.rest} has no 'spec' object")
        # Schema name = file basename minus '.json'. Some real fragments also
        # carry a 'title' inside spec that matches the filename; we trust the
        # filename because it is the value targeted by '#/components/schemas/X'
        # references inside operations.
        schemas[ref.name] = spec

    doc: JsonObj = {
        "openapi": openapi_version or "3.1.0",
        "info": {
            "title": "Cisco Catalyst SD-WAN Manager API",
            "description": (
                "Reassembled from the split-spec fragments published on "
                f"developer.cisco.com for vManage {version}."
            ),
            "version": version,
        },
        "servers": servers
        or [
            {
                "url": "https://{host}:{port}/dataservice",
                "variables": {
                    "host": {"default": "vmanage"},
                    "port": {"default": "443"},
                },
            }
        ],
        "tags": [{"name": name} for name in tag_seen],
        "paths": _sort_dict(paths),
        "components": {"schemas": _sort_dict(schemas)},
    }
    return doc


class StitchError(RuntimeError):
    """Raised when fragments cannot be stitched into a valid document."""


def _extract_openapi_version(body: Mapping[str, Any]) -> str | None:
    meta = body.get("meta")
    if isinstance(meta, Mapping):
        v = meta.get("openapi")
        if isinstance(v, str):
            return v
    return None


def _extract_servers(body: Mapping[str, Any]) -> list[JsonObj] | None:
    meta = body.get("meta")
    if not isinstance(meta, Mapping):
        return None
    s = meta.get("servers")
    if isinstance(s, list) and s and all(isinstance(item, dict) for item in s):
        return [dict(item) for item in s]
    return None


def _sort_dict(d: Mapping[str, Any]) -> dict[str, Any]:
    """Stable, deterministic key order — keeps the YAML diff-friendly."""
    return {k: d[k] for k in sorted(d)}
