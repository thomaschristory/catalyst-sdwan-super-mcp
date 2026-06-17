"""Tests for the OpenAPI spec loader and the adaptive splitter."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sdwan_mcp.loader import (
    DEFAULT_MAX_ACTIONS_PER_TOOL,
    OperationSpec,
    SpecLoader,
    ToolGroup,
    _derive_action_name,
    _parse_request_body,
    is_stats_query_body,
)

# ---------------------------------------------------------------------------
# Fixture helpers — build spec dirs with arbitrary tag/path layouts
# ---------------------------------------------------------------------------


def _make_spec(tmp_path: Path, version: str, ops: list[dict]) -> Path:
    """
    Write a minimal OpenAPI spec at tmp_path/specs/{version}/spec.yaml.

    Each op dict: {"path", "method", "tag", "op_id"} plus optional "params".
    Returns the specs/ root.
    """
    paths: dict = {}
    for op in ops:
        path = op["path"]
        method = op["method"].lower()
        operation = {
            "tags": [op["tag"]],
            "operationId": op["op_id"],
            "summary": op.get("summary", ""),
            "parameters": op.get("params", []),
        }
        paths.setdefault(path, {})[method] = operation

    spec = {
        "openapi": "3.0.0",
        "info": {"title": "t", "version": "1"},
        "paths": paths,
    }

    specs_root = tmp_path / "specs"
    version_dir = specs_root / version
    version_dir.mkdir(parents=True, exist_ok=True)
    (version_dir / "spec.yaml").write_text(yaml.safe_dump(spec))
    return specs_root


def _ops_for_subtag(
    section: str,
    subtag: str,
    base_path: str,
    leaf_names: list[str],
    count_per_leaf: int = 1,
    method: str = "get",
) -> list[dict]:
    """Generate count_per_leaf ops under each leaf segment."""
    tag = f"{section} - {subtag}"
    ops = []
    for leaf in leaf_names:
        for i in range(count_per_leaf):
            ops.append(
                {
                    "path": f"{base_path}/{leaf}/item{i}",
                    "method": method,
                    "tag": tag,
                    "op_id": f"{leaf}_{i}",
                }
            )
    return ops


# ---------------------------------------------------------------------------
# Smoke tests — existing minimal fixture
# ---------------------------------------------------------------------------


def test_loader_emits_a_tool_per_section_under_threshold(specs_dir: Path) -> None:
    index = SpecLoader(str(specs_dir), "20.99", read_write=True).load()

    names = {g.name for g in index.groups}
    # 4 ops total, well under default threshold -> one tool per section, no split.
    assert names == {"monitoring", "configuration"}


def test_loader_filters_writes_when_read_only(specs_dir: Path) -> None:
    index = SpecLoader(str(specs_dir), "20.99", read_write=False).load()

    # The POST-only `Configuration - Device Actions` section is removed in RO mode.
    names = {g.name for g in index.groups}
    assert "configuration" not in names
    assert "monitoring" in names
    # And the POST action_name is no longer in the index.
    assert "post_device_actions_config" not in index.by_action_name


def test_loader_keeps_writes_when_read_write(specs_dir: Path) -> None:
    index = SpecLoader(str(specs_dir), "20.99", read_write=True).load()

    assert "post_device_actions_config" in index.by_action_name
    op = index.by_action_name["post_device_actions_config"]
    assert op.method == "post"
    assert op.operation_id == "updateDevice"  # back-reference preserved


def test_loader_missing_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        SpecLoader(str(tmp_path), "does-not-exist", read_write=True)


# ---------------------------------------------------------------------------
# Cross-tool action-name collisions must not drop/misroute operations (#65)
# ---------------------------------------------------------------------------


def _bare_op(action_name: str, method: str, path: str) -> OperationSpec:
    return OperationSpec(
        operation_id=f"{method}{path}",
        action_name=action_name,
        summary="",
        method=method,
        path=path,
        tag="t",
    )


def test_build_index_is_tool_scoped_no_cross_tool_drop() -> None:
    """Two different tools can legitimately derive the same action_name (e.g. a
    sub-tag split across URL paths produces sibling tools that share a derived
    name). Per-group dedupe can't see across tools, so the dispatch index must
    be tool-scoped — otherwise the second op is dropped and the first is
    misrouted. Regression for #65 (the 3815-ops -> 3523-actions gap)."""
    group_a = ToolGroup(
        name="tool_a", display_tag="A", operations=[_bare_op("get_bgp", "get", "/a/bgp")]
    )
    group_b = ToolGroup(
        name="tool_b", display_tag="B", operations=[_bare_op("get_bgp", "get", "/b/bgp")]
    )

    index = SpecLoader._build_index([group_a, group_b])

    # Each tool resolves its OWN operation — no clobber, no misroute.
    assert index.by_tool["tool_a"]["get_bgp"].path == "/a/bgp"
    assert index.by_tool["tool_b"]["get_bgp"].path == "/b/bgp"
    # No operation silently dropped: every registered op is reachable.
    total_indexed = sum(len(actions) for actions in index.by_tool.values())
    assert total_indexed == 2


# ---------------------------------------------------------------------------
# Read-only mode admits non-mutating POST statistics-DB queries (#62)
# ---------------------------------------------------------------------------

_QUERY_PARAM = [{"name": "query", "in": "query", "required": False, "schema": {"type": "string"}}]


def _stats_twin_ops() -> list[dict]:
    """GET (broken `?query=`) + POST (working, query-in-body) twins for a stats index."""
    tag = "Monitoring - BFD"
    ops: list[dict] = []
    for leaf in ("", "/doccount", "/aggregation", "/page"):
        ops.append(
            {
                "path": f"/statistics/bfd{leaf}",
                "method": "get",
                "tag": tag,
                "op_id": f"getStatDataRawData_Bfd{leaf}",
                "params": _QUERY_PARAM,
            }
        )
        ops.append(
            {
                "path": f"/statistics/bfd{leaf}",
                "method": "post",
                "tag": tag,
                "op_id": f"getStatsRawData_Bfd{leaf}",
            }
        )
    return ops


def test_ro_mode_registers_readsafe_statistics_post(tmp_path: Path) -> None:
    specs_root = _make_spec(tmp_path, "20.99", _stats_twin_ops())
    index = SpecLoader(str(specs_root), "20.99", read_write=False).load()

    # The working POST query forms are exposed even in read-only mode.
    for action in ("post_bfd", "post_bfd_doccount", "post_bfd_aggregation", "post_bfd_page"):
        assert action in index.by_action_name, action
        assert index.by_action_name[action].method == "post"


def test_ro_mode_drops_broken_get_query_twin(tmp_path: Path) -> None:
    specs_root = _make_spec(tmp_path, "20.99", _stats_twin_ops())
    index = SpecLoader(str(specs_root), "20.99", read_write=False).load()

    # The broken GET raw-query form is superseded by its POST twin and removed.
    for action in ("get_bfd", "get_bfd_doccount", "get_bfd_aggregation", "get_bfd_page"):
        assert action not in index.by_action_name, action


def test_ro_mode_keeps_query_less_get_statistics(tmp_path: Path) -> None:
    # GET /statistics (no `query` param) is a working list read — keep it.
    ops = [
        {
            "path": "/statistics",
            "method": "get",
            "tag": "Monitoring - Stats",
            "op_id": "getStatsList",
        },
        {
            "path": "/statistics",
            "method": "post",
            "tag": "Monitoring - Stats",
            "op_id": "getStatsRawData",
        },
    ]
    specs_root = _make_spec(tmp_path, "20.99", ops)
    index = SpecLoader(str(specs_root), "20.99", read_write=False).load()

    assert "get_stats_statistics" in index.by_action_name
    assert "post_stats_statistics" in index.by_action_name


def test_ro_mode_excludes_mutating_statistics_post(tmp_path: Path) -> None:
    # POST endpoints under /statistics that name a write verb must stay out of RO mode.
    ops = [
        {
            "path": "/statistics/on-demand/queue",
            "method": "get",
            "tag": "Monitoring - OnDemand",
            "op_id": "getQueueEntries",
        },
        {
            "path": "/statistics/on-demand/queue",
            "method": "post",
            "tag": "Monitoring - OnDemand",
            "op_id": "createQueueEntry",
        },
        {
            "path": "/statistics/dynamic/collect",
            "method": "post",
            "tag": "Monitoring - Dynamic",
            "op_id": "setDynamicCollection",
        },
        {
            "path": "/statistics/download/{processType}/filelist",
            "method": "post",
            "tag": "Monitoring - Download",
            "op_id": "downloadList",
        },
    ]
    specs_root = _make_spec(tmp_path, "20.99", ops)
    index = SpecLoader(str(specs_root), "20.99", read_write=False).load()

    post_actions = [a for a, op in index.by_action_name.items() if op.method == "post"]
    assert post_actions == [], f"mutating POSTs leaked into RO mode: {post_actions}"
    # The genuine GET read on the same path is still available.
    assert "get_ondemand_queue" in index.by_action_name


def test_ro_mode_excludes_mutating_post_on_query_suffix_path(tmp_path: Path) -> None:
    # A write-verb POST that happens to sit on a query-suffix leaf (aggregation/
    # doccount/page) must NOT be admitted — the leaf-suffix fallback is guarded by
    # the operationId write-verb deny-list. (review of #62)
    ops = [
        {
            "path": "/statistics/bfd/aggregation",
            "method": "post",
            "tag": "Monitoring - BFD",
            "op_id": "createAggregation",
        },
        {
            "path": "/statistics/bfd/page",
            "method": "post",
            "tag": "Monitoring - BFD",
            "op_id": "setPageRollup",
        },
    ]
    specs_root = _make_spec(tmp_path, "20.99", ops)
    index = SpecLoader(str(specs_root), "20.99", read_write=False).load()

    post_actions = [a for a, op in index.by_action_name.items() if op.method == "post"]
    assert post_actions == [], f"mutating POSTs leaked into RO mode: {post_actions}"


def test_rw_mode_keeps_both_get_and_post_stats_twins(tmp_path: Path) -> None:
    specs_root = _make_spec(tmp_path, "20.99", _stats_twin_ops())
    index = SpecLoader(str(specs_root), "20.99", read_write=True).load()

    # RW mode is unchanged: the broken GET twin is NOT dropped.
    assert "get_bfd" in index.by_action_name
    assert "post_bfd" in index.by_action_name


# ---------------------------------------------------------------------------
# Adaptive splitter
# ---------------------------------------------------------------------------


def test_section_under_threshold_emits_one_tool(tmp_path: Path) -> None:
    ops = _ops_for_subtag(
        section="Monitoring",
        subtag="Device Details",
        base_path="/devices",
        leaf_names=["counters", "inventory", "status"],
        count_per_leaf=2,  # 6 ops total
    )
    specs_root = _make_spec(tmp_path, "20.99", ops)

    index = SpecLoader(str(specs_root), "20.99", read_write=True, max_actions_per_tool=50).load()

    assert [g.name for g in index.groups] == ["monitoring"]
    assert len(index.groups[0].operations) == 6


def test_section_over_threshold_splits_by_subtag(tmp_path: Path) -> None:
    """Two sub-tags, each under threshold, section total over -> split by sub-tag only."""
    ops_a = _ops_for_subtag("Configuration", "Devices", "/devices", ["a"], count_per_leaf=30)
    ops_b = _ops_for_subtag("Configuration", "Templates", "/templates", ["b"], count_per_leaf=30)
    specs_root = _make_spec(tmp_path, "20.99", ops_a + ops_b)

    index = SpecLoader(str(specs_root), "20.99", read_write=True, max_actions_per_tool=50).load()

    names = sorted(g.name for g in index.groups)
    assert names == ["configuration_devices", "configuration_templates"]
    # No path recursion happened — each leaf tool simply has the sub-tag's ops.
    by_name = {g.name: g for g in index.groups}
    assert len(by_name["configuration_devices"].operations) == 30
    assert len(by_name["configuration_templates"].operations) == 30


def test_subtag_over_threshold_recurses_on_url_path(tmp_path: Path) -> None:
    """
    Reproduces the NFVirtual example from issue #13: a 72-op sub-tag splits at
    depth 4 into three children (networks/system/cli), without going deeper.
    """
    base = "/v1/feature-profile/nfvirtual"
    leaves = {"networks": 34, "system": 29, "cli": 9}
    ops = []
    for leaf, count in leaves.items():
        for i in range(count):
            ops.append(
                {
                    "path": f"{base}/{leaf}/item{i}",
                    "method": "get",
                    "tag": "Configuration - Feature Profile (NFVirtual)",
                    "op_id": f"{leaf}_{i}",
                }
            )
    specs_root = _make_spec(tmp_path, "20.99", ops)

    index = SpecLoader(str(specs_root), "20.99", read_write=True, max_actions_per_tool=50).load()

    names = sorted(g.name for g in index.groups)
    assert names == [
        "configuration_feature_profile_nfvirtual_cli",
        "configuration_feature_profile_nfvirtual_networks",
        "configuration_feature_profile_nfvirtual_system",
    ]
    by_name = {g.name: g for g in index.groups}
    assert len(by_name["configuration_feature_profile_nfvirtual_networks"].operations) == 34
    assert len(by_name["configuration_feature_profile_nfvirtual_system"].operations) == 29
    assert len(by_name["configuration_feature_profile_nfvirtual_cli"].operations) == 9


def test_oversize_at_max_depth_emits_warning(tmp_path: Path, capsys) -> None:
    """All 70 ops share the same 5-deep URL prefix → no path split helps."""
    ops = []
    for i in range(70):
        ops.append(
            {
                "path": f"/v1/feature/sdwan/transport/wan/item{i}",
                "method": "get",
                "tag": "Configuration - Feature Profile (SDWAN)",
                "op_id": f"op_{i}",
            }
        )
    specs_root = _make_spec(tmp_path, "20.99", ops)

    index = SpecLoader(str(specs_root), "20.99", read_write=True, max_actions_per_tool=50).load()

    out = capsys.readouterr().out
    assert "WARNING" in out and "70 actions" in out

    # Exactly one tool comes out, and it's named after the parent sub-tag —
    # not the last-path-segment fallback. The path-split couldn't subdivide
    # further so we don't pretend it did.
    assert len(index.groups) == 1
    assert index.groups[0].name == "configuration_feature_profile_sdwan"
    assert len(index.groups[0].operations) == 70


def test_misc_collapse_boundary_at_three_vs_four_ops(tmp_path: Path) -> None:
    """
    Sub-tag with exactly 3 ops collapses to <section>_misc;
    sub-tag with exactly 4 ops gets its own tool.
    """
    big = _ops_for_subtag("Configuration", "Big", "/big", ["a"], count_per_leaf=51)
    three = _ops_for_subtag("Configuration", "Three", "/three", ["x"], count_per_leaf=3)
    four = _ops_for_subtag("Configuration", "Four", "/four", ["x"], count_per_leaf=4)
    specs_root = _make_spec(tmp_path, "20.99", big + three + four)

    index = SpecLoader(str(specs_root), "20.99", read_write=True, max_actions_per_tool=50).load()

    names = {g.name: g for g in index.groups}
    # 3-op sub-tag is below MISC_BUCKET_THRESHOLD=4 -> goes to misc.
    assert "configuration_three" not in names
    assert "configuration_misc" in names
    assert len(names["configuration_misc"].operations) == 3
    # 4-op sub-tag is at the threshold -> gets its own tool.
    assert "configuration_four" in names
    assert len(names["configuration_four"].operations) == 4


def test_small_sibling_subtags_collapse_to_misc(tmp_path: Path) -> None:
    """A 50+ section with many tiny sub-tags collapses them into misc."""
    big = _ops_for_subtag("Configuration", "Big", "/big", ["a"], count_per_leaf=51)
    # Three tiny sub-tags, each well below MISC_BUCKET_THRESHOLD=4.
    tinies = []
    for subtag in ["Tiny1", "Tiny2", "Tiny3"]:
        tinies.extend(
            _ops_for_subtag("Configuration", subtag, f"/{subtag.lower()}", ["x"], count_per_leaf=2)
        )
    specs_root = _make_spec(tmp_path, "20.99", big + tinies)

    index = SpecLoader(str(specs_root), "20.99", read_write=True, max_actions_per_tool=50).load()

    names = {g.name for g in index.groups}
    assert "configuration_misc" in names
    misc = next(g for g in index.groups if g.name == "configuration_misc")
    # Three tinies, 2 ops each = 6 collapsed into misc.
    assert len(misc.operations) == 6


def test_threshold_zero_disables_splitting(tmp_path: Path) -> None:
    """max_actions_per_tool=0 -> one tool per section regardless of size."""
    ops = _ops_for_subtag("Configuration", "A", "/a", ["x"], count_per_leaf=80) + _ops_for_subtag(
        "Configuration", "B", "/b", ["y"], count_per_leaf=80
    )
    specs_root = _make_spec(tmp_path, "20.99", ops)

    index = SpecLoader(str(specs_root), "20.99", read_write=True, max_actions_per_tool=0).load()

    assert [g.name for g in index.groups] == ["configuration"]
    assert len(index.groups[0].operations) == 160


# ---------------------------------------------------------------------------
# Stable action names
# ---------------------------------------------------------------------------


def test_action_name_is_independent_of_operation_id(tmp_path: Path) -> None:
    """
    Reproduces the Cisco 20.16 -> 20.18 rename: PUT /template/policy/list/site/{id}
    keeps the same (method, path, tag) so the derived action_name is stable, even
    though Cisco renamed the operationId.
    """
    path = "/template/policy/list/site/{id}"
    tag = "Configuration - Policy Site List Builder"
    method = "put"

    name_v15 = _derive_action_name(method, path, tag)
    name_v18 = _derive_action_name(method, path, tag)
    assert name_v15 == name_v18

    # Sanity-check the format itself: verb + tag-component + last-segment.
    assert name_v18 == "put_policy_site_list_builder_site"

    # And the operationId churn is not part of the derivation function signature
    # at all — confirms by construction that op_id can't influence action_name.
    spec_old = [
        {
            "path": path,
            "method": method,
            "tag": tag,
            "op_id": "editPolicyList_33",
            "params": [
                {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
            ],
        }
    ]
    spec_new = [
        {
            "path": path,
            "method": method,
            "tag": tag,
            "op_id": "editPolicyList_ConfigurationPolicySiteListBuilder_3103",
            "params": [
                {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
            ],
        }
    ]

    old_root = _make_spec(tmp_path, "20.16", spec_old)
    new_root = _make_spec(tmp_path, "20.18", spec_new)

    old_idx = SpecLoader(str(old_root), "20.16", read_write=True).load()
    new_idx = SpecLoader(str(new_root), "20.18", read_write=True).load()

    assert "put_policy_site_list_builder_site" in old_idx.by_action_name
    assert "put_policy_site_list_builder_site" in new_idx.by_action_name
    # operationIds remain the back-reference, and differ across versions.
    assert (
        old_idx.by_action_name["put_policy_site_list_builder_site"].operation_id
        == "editPolicyList_33"
    )
    assert (
        new_idx.by_action_name["put_policy_site_list_builder_site"].operation_id
        == "editPolicyList_ConfigurationPolicySiteListBuilder_3103"
    )


def test_action_names_are_deduplicated_within_a_tool(tmp_path: Path) -> None:
    """
    Two distinct ops in the same tool whose (verb, tag, last-segment) coincide
    must end up with different action_names (`..._2` suffix).
    """
    tag = "Monitoring - Device Details"
    ops = [
        {
            "path": "/devices/inventory",
            "method": "get",
            "tag": tag,
            "op_id": "listInventory",
        },
        {
            "path": "/other/inventory",
            "method": "get",
            "tag": tag,
            "op_id": "listInventory_2",
        },
    ]
    specs_root = _make_spec(tmp_path, "20.99", ops)

    index = SpecLoader(str(specs_root), "20.99", read_write=True).load()

    actions = sorted(index.by_action_name.keys())
    assert actions == [
        "get_device_details_inventory",
        "get_device_details_inventory_2",
    ]


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_default_max_actions_per_tool_is_150() -> None:
    assert DEFAULT_MAX_ACTIONS_PER_TOOL == 150


# ---------------------------------------------------------------------------
# Pagination style detection
# ---------------------------------------------------------------------------


def test_loader_detects_scroll_style(tmp_path):
    ops = [
        {
            "path": "/alarms",
            "method": "get",
            "tag": "Monitoring - Alarms",
            "op_id": "getAlarms",
            "params": [{"name": "scrollId", "in": "query", "schema": {"type": "string"}}],
        }
    ]
    idx = SpecLoader(str(_make_spec(tmp_path, "20.99", ops)), "20.99", read_write=False).load()
    op = next(iter(idx.by_action_name.values()))
    assert op.pagination == "scroll"


def test_loader_detects_offset_style(tmp_path):
    ops = [
        {
            "path": "/devices",
            "method": "get",
            "tag": "Configuration - Devices",
            "op_id": "listDevices",
            "params": [
                {"name": "page", "in": "query", "schema": {"type": "integer"}},
                {"name": "pageSize", "in": "query", "schema": {"type": "integer"}},
            ],
        }
    ]
    idx = SpecLoader(str(_make_spec(tmp_path, "20.99", ops)), "20.99", read_write=False).load()
    op = next(iter(idx.by_action_name.values()))
    assert op.pagination == "offset"


def test_loader_offset_with_count_or_limit(tmp_path):
    for size_param in ("count", "limit"):
        ops = [
            {
                "path": f"/items_{size_param}",
                "method": "get",
                "tag": "Misc - Items",
                "op_id": f"listItems_{size_param}",
                "params": [
                    {"name": "page", "in": "query", "schema": {"type": "integer"}},
                    {"name": size_param, "in": "query", "schema": {"type": "integer"}},
                ],
            }
        ]
        idx = SpecLoader(
            str(_make_spec(tmp_path / size_param, "20.99", ops)),
            "20.99",
            read_write=False,
        ).load()
        op = next(iter(idx.by_action_name.values()))
        assert op.pagination == "offset", f"failed for size param {size_param}"


def test_loader_no_pagination_when_only_page_param(tmp_path):
    ops = [
        {
            "path": "/x",
            "method": "get",
            "tag": "Misc - X",
            "op_id": "listX",
            "params": [{"name": "page", "in": "query", "schema": {"type": "integer"}}],
        }
    ]
    idx = SpecLoader(str(_make_spec(tmp_path, "20.99", ops)), "20.99", read_write=False).load()
    op = next(iter(idx.by_action_name.values()))
    assert op.pagination is None


def test_loader_no_pagination_for_plain_op(tmp_path):
    ops = [
        {
            "path": "/single",
            "method": "get",
            "tag": "Misc - Single",
            "op_id": "getSingle",
        }
    ]
    idx = SpecLoader(str(_make_spec(tmp_path, "20.99", ops)), "20.99", read_write=False).load()
    op = next(iter(idx.by_action_name.values()))
    assert op.pagination is None


# ---------------------------------------------------------------------------
# Request-body field extraction (#78)
# ---------------------------------------------------------------------------


def test_parse_request_body_resolves_ref_to_top_level_fields() -> None:
    """A $ref'd body schema is resolved one level so its top-level fields and
    required flags surface for the tool description."""
    schemas = {
        "StatsQuery": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "object", "description": "Query filter"},
                "size": {"type": "integer"},
                "aggregation": {"type": "object"},
            },
        }
    }
    operation = {
        "requestBody": {
            "description": "Query filter",
            "content": {
                "application/json": {"schema": {"$ref": "#/components/schemas/StatsQuery"}}
            },
        }
    }
    has_body, desc, fields = _parse_request_body(operation, schemas)
    assert has_body is True
    assert desc == "Query filter"
    by_name = {f.name: f for f in fields}
    assert set(by_name) == {"query", "size", "aggregation"}
    assert by_name["query"].required is True
    assert by_name["size"].required is False
    assert by_name["size"].type == "integer"
    assert by_name["query"].description == "Query filter"


def test_parse_request_body_bare_object_yields_no_fields() -> None:
    """vManage's stats bodies are bare {"type": "object"} in the spec — we must
    not invent fields, so the list stays empty but has_body is still True."""
    operation = {"requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}}}
    has_body, _desc, fields = _parse_request_body(operation, {})
    assert has_body is True
    assert fields == []


def test_parse_request_body_absent() -> None:
    has_body, desc, fields = _parse_request_body({}, {})
    assert has_body is False
    assert desc == ""
    assert fields == []


def test_parse_request_body_unresolvable_ref_degrades_to_empty() -> None:
    """A $ref with no matching component yields no fields rather than raising."""
    operation = {
        "requestBody": {
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Missing"}}}
        }
    }
    has_body, _desc, fields = _parse_request_body(operation, {})
    assert has_body is True
    assert fields == []


# ---------------------------------------------------------------------------
# Request-body robustness + composition (#78 review hardening)
# ---------------------------------------------------------------------------


def test_parse_request_body_merges_allof_and_nested_ref() -> None:
    """Feature-profile bodies are allOf:[{$ref Base}, {properties: real fields}].
    Both the $ref'd base and the inline member must contribute fields."""
    schemas = {
        "Base": {"type": "object", "properties": {"id": {"type": "string"}}},
        "Cellular": {
            "allOf": [
                {"$ref": "#/components/schemas/Base"},
                {
                    "type": "object",
                    "required": ["simSlot0"],
                    "properties": {
                        "simSlot0": {"type": "object"},
                        "primarySlot": {"type": "integer"},
                    },
                },
            ]
        },
    }
    operation = {
        "requestBody": {
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Cellular"}}}
        }
    }
    _has, _desc, fields = _parse_request_body(operation, schemas)
    by_name = {f.name: f for f in fields}
    assert set(by_name) == {"id", "simSlot0", "primarySlot"}
    assert by_name["simSlot0"].required is True
    assert by_name["id"].required is False


def test_parse_request_body_falls_back_to_star_star_media() -> None:
    """Some vManage bodies are declared only under */* — still extract fields."""
    operation = {
        "requestBody": {
            "content": {
                "*/*": {"schema": {"type": "object", "properties": {"x": {"type": "string"}}}}
            }
        }
    }
    _has, _desc, fields = _parse_request_body(operation, {})
    assert [f.name for f in fields] == ["x"]


@pytest.mark.parametrize(
    "operation",
    [
        {"requestBody": None},
        {"requestBody": "nonsense"},
        {"requestBody": {"content": "nonsense"}},
        {"requestBody": {"content": {"application/json": {"schema": "nonsense"}}}},
        {
            "requestBody": {
                "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/Missing"}}
                }
            }
        },
    ],
)
def test_parse_request_body_degrades_on_malformed_spec(operation) -> None:
    """A malformed requestBody must degrade to has_body=True, no fields — never
    raise (which would abort the whole loader at startup) (#78 review)."""
    has_body, _desc, fields = _parse_request_body(operation, {})
    assert has_body is True
    assert fields == []


def test_resolve_ref_truthy_non_dict_target_degrades() -> None:
    """A component stored as a truthy non-dict must not crash field extraction."""
    operation = {
        "requestBody": {
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/X"}}}
        }
    }
    has_body, _desc, fields = _parse_request_body(operation, {"X": "notadict"})
    assert has_body is True
    assert fields == []


def test_is_stats_query_body_detects_statistics_post() -> None:
    stats = OperationSpec(
        operation_id="x",
        action_name="post_iface_aggregation",
        summary="",
        method="post",
        path="/statistics/interface/aggregation",
        tag="t",
        has_body=True,
    )
    non_stats = OperationSpec(
        operation_id="x",
        action_name="post_thing",
        summary="",
        method="post",
        path="/devices/config",
        tag="t",
        has_body=True,
    )
    get_stats = OperationSpec(
        operation_id="x",
        action_name="get_iface",
        summary="",
        method="get",
        path="/statistics/interface",
        tag="t",
        has_body=False,
    )
    assert is_stats_query_body(stats) is True
    assert is_stats_query_body(non_stats) is False
    assert is_stats_query_body(get_stats) is False
