"""Sanity checks on a stitched OpenAPI document.

Rejects garbage early so we never write a partial or broken spec to disk.
The checks are deliberately conservative — they target known failure modes
(empty paths, missing schemas, dangling $refs) rather than trying to be a
full OpenAPI validator.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

MIN_PATHS = 100
MIN_YAML_BYTES = 1_000_000  # 1 MB; 20.18 is ~21 MB

_SCHEMA_REF_PREFIX = "#/components/schemas/"
_EXAMPLE_REF_PREFIX = "#/components/examples/"


class FetcherValidationError(RuntimeError):
    """Raised when the stitched document fails sanity checks."""


def validate(
    doc: Mapping[str, Any],
    *,
    yaml_bytes: int | None = None,
    min_paths: int = MIN_PATHS,
    min_yaml_bytes: int = MIN_YAML_BYTES,
) -> list[str]:
    """Validate a stitched OpenAPI doc.

    Returns a list of WARNING messages (e.g. unresolved example refs).
    Raises ``FetcherValidationError`` on any fatal problem.
    """
    if "openapi" not in doc:
        raise FetcherValidationError("Missing top-level 'openapi' key")

    paths = doc.get("paths")
    if not isinstance(paths, Mapping) or len(paths) < min_paths:
        raise FetcherValidationError(
            f"Too few paths in stitched doc: {len(paths) if isinstance(paths, Mapping) else 0} < {min_paths}"
        )

    components = doc.get("components")
    schemas: Mapping[str, Any] = {}
    if isinstance(components, Mapping):
        s = components.get("schemas")
        if isinstance(s, Mapping):
            schemas = s
    if not schemas:
        raise FetcherValidationError("components.schemas is empty or missing")

    if yaml_bytes is not None and yaml_bytes < min_yaml_bytes:
        raise FetcherValidationError(
            f"Stitched YAML is suspiciously small: {yaml_bytes} bytes < {min_yaml_bytes}"
        )

    warnings: list[str] = []
    schema_names = set(schemas.keys())
    unresolved_schemas: set[str] = set()
    unresolved_examples: set[str] = set()
    for ref in _collect_refs(doc):
        if ref.startswith(_SCHEMA_REF_PREFIX):
            name = ref.removeprefix(_SCHEMA_REF_PREFIX)
            if name not in schema_names:
                unresolved_schemas.add(name)
        elif ref.startswith(_EXAMPLE_REF_PREFIX):
            unresolved_examples.add(ref.removeprefix(_EXAMPLE_REF_PREFIX))

    if unresolved_schemas:
        sample = sorted(unresolved_schemas)[:5]
        raise FetcherValidationError(
            f"Unresolved schema $refs in stitched doc "
            f"({len(unresolved_schemas)} total, sample: {sample})"
        )
    if unresolved_examples:
        warnings.append(
            f"{len(unresolved_examples)} unresolved $ref(s) into components.examples — "
            "Cisco does not publish example fragments; this is expected and tolerated."
        )

    return warnings


def _collect_refs(node: Any) -> Iterable[str]:
    """Yield every ``$ref`` string anywhere in the document.

    Iterative (BFS) rather than recursive — a real OpenAPI doc nests deep
    enough through ``allOf`` chains that the default 1000-frame recursion
    limit can fire on a pathological fragment.
    """
    stack: list[Any] = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k == "$ref" and isinstance(v, str):
                    yield v
                else:
                    stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
