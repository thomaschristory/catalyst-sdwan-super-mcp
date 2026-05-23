"""Tests for the OpenAPI spec loader and the adaptive splitter."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sdwan_mcp.loader import (
    DEFAULT_MAX_ACTIONS_PER_TOOL,
    SpecLoader,
    _derive_action_name,
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
