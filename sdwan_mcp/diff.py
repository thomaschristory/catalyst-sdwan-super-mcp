"""
diff.py — compares two SD-WAN spec versions and reports:
  - REMOVED operations (breaking)
  - ADDED operations (new tools available)
  - CHANGED operations (parameter drift)
"""

from __future__ import annotations

from dataclasses import dataclass

from .loader import OperationSpec, SpecLoader

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ParamDiff:
    name: str
    change: str  # "added" | "removed" | "type_changed" | "required_changed"
    detail: str = ""


@dataclass
class OperationDiff:
    operation_id: str
    tag: str
    param_diffs: list[ParamDiff]


@dataclass
class VersionDiff:
    old_version: str
    new_version: str
    removed: list[OperationSpec]
    added: list[OperationSpec]
    changed: list[OperationDiff]


# ---------------------------------------------------------------------------
# Diff engine
# ---------------------------------------------------------------------------


def diff_versions(
    specs_dir: str,
    old_version: str,
    new_version: str,
    read_write: bool = True,  # diff in RW mode to catch all operations
) -> VersionDiff:
    old_index = SpecLoader(specs_dir, old_version, read_write=read_write).load()
    new_index = SpecLoader(specs_dir, new_version, read_write=read_write).load()

    old_ops = old_index.by_operation_id
    new_ops = new_index.by_operation_id

    old_ids = set(old_ops)
    new_ids = set(new_ops)

    removed = [old_ops[op_id] for op_id in sorted(old_ids - new_ids)]
    added = [new_ops[op_id] for op_id in sorted(new_ids - old_ids)]
    changed = _find_changed(old_ops, new_ops, old_ids & new_ids)

    return VersionDiff(
        old_version=old_version,
        new_version=new_version,
        removed=removed,
        added=added,
        changed=changed,
    )


def _find_changed(
    old_ops: dict[str, OperationSpec],
    new_ops: dict[str, OperationSpec],
    common_ids: set[str],
) -> list[OperationDiff]:
    result = []

    for op_id in sorted(common_ids):
        old = old_ops[op_id]
        new = new_ops[op_id]
        param_diffs = _diff_params(old, new)

        # Also flag method or path changes
        if old.method != new.method:
            param_diffs.append(
                ParamDiff(
                    name="(method)",
                    change="type_changed",
                    detail=f"{old.method.upper()} → {new.method.upper()}",
                )
            )
        if old.path != new.path:
            param_diffs.append(
                ParamDiff(
                    name="(path)",
                    change="type_changed",
                    detail=f"{old.path} → {new.path}",
                )
            )

        if param_diffs:
            result.append(
                OperationDiff(
                    operation_id=op_id,
                    tag=new.tag,
                    param_diffs=param_diffs,
                )
            )

    return result


def _diff_params(old: OperationSpec, new: OperationSpec) -> list[ParamDiff]:
    diffs = []
    old_params = {p.name: p for p in old.parameters}
    new_params = {p.name: p for p in new.parameters}

    # Removed params
    for name in sorted(old_params.keys() - new_params.keys()):
        diffs.append(
            ParamDiff(
                name=name,
                change="removed",
                detail=f"was {old_params[name].location}, {old_params[name].type}",
            )
        )

    # Added params
    for name in sorted(new_params.keys() - old_params.keys()):
        p = new_params[name]
        req = "required" if p.required else "optional"
        diffs.append(
            ParamDiff(
                name=name,
                change="added",
                detail=f"{p.location}, {p.type}, {req}",
            )
        )

    # Changed params
    for name in sorted(old_params.keys() & new_params.keys()):
        old_p = old_params[name]
        new_p = new_params[name]
        if old_p.type != new_p.type:
            diffs.append(
                ParamDiff(
                    name=name,
                    change="type_changed",
                    detail=f"{old_p.type} → {new_p.type}",
                )
            )
        if old_p.required != new_p.required:
            diffs.append(
                ParamDiff(
                    name=name,
                    change="required_changed",
                    detail=f"required={old_p.required} → required={new_p.required}",
                )
            )

    return diffs


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------


def print_diff(diff: VersionDiff) -> None:
    print(f"\n=== SD-WAN API Diff: {diff.old_version} → {diff.new_version} ===\n")

    if diff.removed:
        print(f"REMOVED ({len(diff.removed)} operations — potentially breaking):")
        for op in diff.removed:
            print(f"  - {op.operation_id}  [{op.tag}]  {op.method.upper()} {op.path}")
    else:
        print("REMOVED: none")

    print()

    if diff.added:
        print(f"ADDED ({len(diff.added)} new operations):")
        for op in diff.added:
            print(f"  + {op.operation_id}  [{op.tag}]  {op.method.upper()} {op.path}")
    else:
        print("ADDED: none")

    print()

    if diff.changed:
        print(f"CHANGED ({len(diff.changed)} operations with parameter drift):")
        for op_diff in diff.changed:
            print(f"  ~ {op_diff.operation_id}  [{op_diff.tag}]")
            for pd in op_diff.param_diffs:
                print(f"      {pd.change}: '{pd.name}' — {pd.detail}")
    else:
        print("CHANGED: none")

    print()
    print(
        f"Summary: {len(diff.removed)} removed, "
        f"{len(diff.added)} added, "
        f"{len(diff.changed)} changed"
    )
