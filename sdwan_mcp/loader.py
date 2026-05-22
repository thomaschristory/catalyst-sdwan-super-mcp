"""
loader.py — loads Cisco SD-WAN OpenAPI sub-specs for a given version,
merges them, groups operations by tag, filters by RO/RW mode, and builds
a flat spec index for O(1) dispatch lookup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RO_METHODS = frozenset({"get"})
RW_METHODS = frozenset({"get", "post", "put", "delete", "patch"})

SKIP_METHODS = frozenset({"head", "options", "trace"})

# Tag-grouping granularity:
#   "section" — group by the leading section of a tag, e.g. "Configuration"
#               (from "Configuration - Feature Profile (SDWAN)"). ~30-40 tools.
#   "tag"     — group by the full tag, e.g. "Configuration - Feature Profile (SDWAN)".
#               300+ tools on a typical vManage spec — only use if your LLM client
#               can handle that many.
GRANULARITY_SECTION = "section"
GRANULARITY_TAG = "tag"
VALID_GRANULARITIES = frozenset({GRANULARITY_SECTION, GRANULARITY_TAG})


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ParameterSpec:
    name: str
    location: str  # "path" | "query" | "header" | "cookie"
    required: bool = False
    type: str = "string"
    description: str = ""
    default: object = None


@dataclass
class OperationSpec:
    operation_id: str
    summary: str
    method: str  # lowercase: get | post | put | delete | patch
    path: str
    tag: str
    parameters: list[ParameterSpec] = field(default_factory=list)
    has_body: bool = False
    body_description: str = ""


@dataclass
class TagGroup:
    tag: str
    slug: str  # snake_case tool name, e.g. "monitoring_device_details"
    operations: list[OperationSpec] = field(default_factory=list)


@dataclass
class SpecIndex:
    """Flat lookup: operationId → OperationSpec, built for O(1) dispatch."""

    by_operation_id: dict[str, OperationSpec] = field(default_factory=dict)
    groups: list[TagGroup] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(tag: str) -> str:
    """
    'Monitoring - Device Details' → 'monitoring_device_details'
    """
    return (
        tag.lower()
        .replace(" - ", "_")
        .replace(" ", "_")
        .replace("-", "_")
        .replace("/", "_")
        .replace("(", "")
        .replace(")", "")
        .replace(",", "")
        .replace(".", "")
    )


def _extract_type(schema: dict) -> str:
    if not schema:
        return "string"
    if "$ref" in schema:
        return "object"
    return schema.get("type", "string")


def _parse_parameters(raw_params: list[dict]) -> list[ParameterSpec]:
    result = []
    for p in raw_params or []:
        schema = p.get("schema", {})
        result.append(
            ParameterSpec(
                name=p.get("name", ""),
                location=p.get("in", "query"),
                required=p.get("required", False),
                type=_extract_type(schema),
                description=p.get("description", ""),
                default=schema.get("default"),
            )
        )
    return result


def _parse_operation(
    path: str,
    method: str,
    operation: dict,
    tag: str,
) -> OperationSpec:
    has_body = "requestBody" in operation
    body_desc = ""
    if has_body:
        body_desc = operation["requestBody"].get("description", "Request body (JSON)")

    return OperationSpec(
        operation_id=operation.get("operationId", f"{method}_{path}"),
        summary=operation.get("summary") or operation.get("description", ""),
        method=method,
        path=path,
        tag=tag,
        parameters=_parse_parameters(operation.get("parameters", [])),
        has_body=has_body,
        body_description=body_desc,
    )


# ---------------------------------------------------------------------------
# Main loader
# ---------------------------------------------------------------------------


class SpecLoader:
    def __init__(
        self,
        specs_dir: str,
        version: str,
        read_write: bool = False,
        granularity: str = GRANULARITY_SECTION,
    ):
        self.version_dir = Path(specs_dir) / version
        self.version = version
        self.allowed_methods = RW_METHODS if read_write else RO_METHODS

        if granularity not in VALID_GRANULARITIES:
            raise ValueError(
                f"Invalid granularity '{granularity}'. "
                f"Expected one of {sorted(VALID_GRANULARITIES)}."
            )
        self.granularity = granularity

        if not self.version_dir.exists():
            raise FileNotFoundError(
                f"Spec directory not found: {self.version_dir}\n"
                f"Download the OpenAPI YAMLs from Cisco DevNet and place them in "
                f"{self.version_dir}/"
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> SpecIndex:
        """Full pipeline: load → merge → group → filter → index."""
        merged = self._load_and_merge()
        groups = self._group_by_tag(merged)
        groups = self._filter_by_mode(groups)
        return self._build_index(groups)

    # ------------------------------------------------------------------
    # Step 1: load all sub-spec YAMLs and merge into one dict
    # ------------------------------------------------------------------

    def _load_and_merge(self) -> dict:
        spec_files = sorted(
            list(self.version_dir.glob("*.yaml"))
            + list(self.version_dir.glob("*.yml"))
            + list(self.version_dir.glob("*.json"))
        )
        if not spec_files:
            raise FileNotFoundError(
                f"No spec files (*.yaml | *.yml | *.json) found in {self.version_dir}"
            )

        merged: dict = {
            "paths": {},
            "components": {"schemas": {}},
        }

        for spec_file in spec_files:
            print(f"[loader] Loading {spec_file.name}")
            try:
                if spec_file.suffix == ".json":
                    import json

                    spec = json.loads(spec_file.read_text())
                else:
                    spec = yaml.safe_load(spec_file.read_text())
            except (yaml.YAMLError, ValueError) as e:
                print(f"[loader] WARNING: Failed to parse {spec_file.name}: {e}")
                continue

            # Last-writer-wins on conflict (Cisco sub-specs are non-overlapping)
            merged["paths"].update(spec.get("paths", {}))

            components = spec.get("components", {})
            merged["components"]["schemas"].update(components.get("schemas", {}))

        print(f"[loader] Loaded {len(spec_files)} spec file(s), {len(merged['paths'])} total paths")
        return merged

    # ------------------------------------------------------------------
    # Step 2: group operations by their first tag
    # ------------------------------------------------------------------

    def _group_by_tag(self, spec: dict) -> list[TagGroup]:
        groups: dict[str, TagGroup] = {}

        for path, path_item in spec.get("paths", {}).items():
            for method, operation in path_item.items():
                if method.lower() in SKIP_METHODS:
                    continue
                if not isinstance(operation, dict):
                    continue

                tags = operation.get("tags", ["untagged"])
                full_tag = tags[0]
                group_key = self._group_key(full_tag)

                if group_key not in groups:
                    groups[group_key] = TagGroup(tag=group_key, slug=_slugify(group_key))

                op = _parse_operation(path, method.lower(), operation, full_tag)
                groups[group_key].operations.append(op)

        result = list(groups.values())
        print(f"[loader] Granularity={self.granularity} -> {len(result)} tool group(s)")
        return result

    def _group_key(self, full_tag: str) -> str:
        """Reduce a tag to its grouping key based on the configured granularity."""
        if self.granularity == GRANULARITY_SECTION:
            # 'Configuration - Feature Profile (SDWAN)' -> 'Configuration'
            return full_tag.split(" - ", 1)[0]
        return full_tag

    # ------------------------------------------------------------------
    # Step 3: filter by RO / RW mode
    # ------------------------------------------------------------------

    def _filter_by_mode(self, groups: list[TagGroup]) -> list[TagGroup]:
        filtered = []
        removed_total = 0

        for group in groups:
            kept = [op for op in group.operations if op.method in self.allowed_methods]
            removed = len(group.operations) - len(kept)
            removed_total += removed

            if kept:
                filtered.append(
                    TagGroup(
                        tag=group.tag,
                        slug=group.slug,
                        operations=kept,
                    )
                )

        mode = "RW" if self.allowed_methods == RW_METHODS else "RO"
        print(
            f"[loader] Mode={mode}: kept {sum(len(g.operations) for g in filtered)} "
            f"operations, filtered out {removed_total} write operations"
        )
        return filtered

    # ------------------------------------------------------------------
    # Step 4: build flat index for O(1) dispatcher lookup
    # ------------------------------------------------------------------

    def _build_index(self, groups: list[TagGroup]) -> SpecIndex:
        index = SpecIndex(groups=groups)
        for group in groups:
            for op in group.operations:
                if op.operation_id in index.by_operation_id:
                    print(
                        f"[loader] WARNING: duplicate operationId '{op.operation_id}' "
                        f"— keeping first occurrence"
                    )
                    continue
                index.by_operation_id[op.operation_id] = op

        print(
            f"[loader] Index built: {len(index.by_operation_id)} unique operations "
            f"across {len(groups)} tools"
        )
        return index
