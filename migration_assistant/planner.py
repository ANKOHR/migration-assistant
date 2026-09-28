"""Create deterministic, dependency-ordered review plans from a mapping artifact."""

from __future__ import annotations

from typing import Any

from .common import HONESTY_BOUNDARY, digest


def _table_order(source_schema: dict[str, Any]) -> list[str]:
    names = [table["name"] for table in source_schema["tables"]]
    dependencies = {table["name"]: {ref["table"] for ref in table.get("foreign_keys", []) if ref["table"] in names}
                    for table in source_schema["tables"]}
    ordered: list[str] = []
    while len(ordered) < len(names):
        ready = [name for name in names if name not in ordered and dependencies[name] <= set(ordered)]
        if not ready:
            raise ValueError("Source table foreign keys contain a cycle")
        ordered.extend(ready)
    return ordered


def build_plan(mapping: dict[str, Any]) -> dict[str, Any]:
    flags = mapping.get("known_flags", [])
    steps = []
    last_step: dict[str, str] = {}
    first_load: dict[str, str] = {}
    for table_name in _table_order(mapping["source_schema"]):
        table_flags = [flag for flag in flags if flag["evidence"]["source_table"] == table_name]
        for action in ("extract", "transform", "load", "verify"):
            step_id = f"{table_name}.{action}"
            if action == "extract":
                dependencies = []
                for reference in next(t for t in mapping["source_schema"]["tables"] if t["name"] == table_name).get("foreign_keys", []):
                    if reference["table"] in first_load:
                        dependencies.append(first_load[reference["table"]])
            else:
                dependencies = [last_step[table_name]]
            action_flags = []
            for flag in table_flags:
                rule = flag["rule"]
                action_for_rule = {
                    "TYPE_CONFLICT": "transform", "MISSING_REQUIRED_TARGET_FIELD": "transform",
                    "UNPARSEABLE_CURRENCY": "transform", "UNPARSEABLE_DATE": "transform",
                    "MIXED_DATE_FORMATS": "transform", "LOSSY_TRANSFORM": "transform",
                    "DUPLICATE_KEY": "verify", "ORPHAN_REFERENCE": "verify",
                }.get(rule, "verify")
                if action == action_for_rule:
                    action_flags.append(flag["flag_id"])
            treatment = {
                "extract": "Read the synthetic fixture rows and retain source row identity.",
                "transform": "Apply the mapped coercions, configured defaults, and quarantine rules in memory.",
                "load": "Describe the target-shaped fixture output; this step writes no live system.",
                "verify": "Check parity, key uniqueness, required fields, references, and recorded spot checks.",
            }[action]
            steps.append({
                "step_id": step_id,
                "table": table_name,
                "action": action,
                "depends_on": dependencies,
                "flag_ids_to_handle": sorted(action_flags),
                "description": treatment,
            })
            last_step[table_name] = step_id
            if action == "load":
                first_load[table_name] = step_id
    referenced = {flag_id for step in steps for flag_id in step["flag_ids_to_handle"]}
    unassigned = sorted(flag["flag_id"] for flag in flags if flag["flag_id"] not in referenced)
    if unassigned:
        # Attach any unforeseen finding to that table's verification step.
        for flag_id in unassigned:
            flag = next(item for item in flags if item["flag_id"] == flag_id)
            next(step for step in steps if step["step_id"] == f"{flag['evidence']['source_table']}.verify")["flag_ids_to_handle"].append(flag_id)
    plan = {
        "honesty_boundary": HONESTY_BOUNDARY,
        "plan_header": "Rule-based plan generated from synthetic schemas; fixture validation only. No live migration is performed.",
        "plan_format_version": 1,
        "mapping_version": mapping["mapping_version"],
        "steps": steps,
        "flag_ids": [flag["flag_id"] for flag in flags],
    }
    plan["plan_version"] = digest(plan)
    return plan
