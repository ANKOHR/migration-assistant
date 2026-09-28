"""Named, evidence-backed incompatibility rules over the synthetic datasets."""

from __future__ import annotations

import re
from typing import Any

from .common import HONESTY_BOUNDARY, load_source, source_data_fingerprint
from .typing_rules import parse_currency, parse_date, quantize_currency, type_compatible


SEVERITY = {
    "TYPE_CONFLICT": "high",
    "MISSING_REQUIRED_TARGET_FIELD": "high",
    "DUPLICATE_KEY": "critical",
    "ORPHAN_REFERENCE": "high",
    "UNPARSEABLE_CURRENCY": "high",
    "UNPARSEABLE_DATE": "high",
    "MIXED_DATE_FORMATS": "medium",
    "LOSSY_TRANSFORM": "medium",
}


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def collect_flags(source_dir, mapping: dict[str, Any]) -> list[dict[str, Any]]:
    source_schema, data = load_source(source_dir)
    if source_schema != mapping["source_schema"]:
        raise ValueError("Source schema does not match the mapping")
    if source_data_fingerprint(data) != mapping["source_data_fingerprint"]:
        raise ValueError("Source data does not match the mapping fingerprint")
    target_schema = mapping["target_schema"]
    target_tables = {table["name"]: table for table in target_schema["tables"]}
    mapping_tables = {table["source_table"]: table for table in mapping["tables"]}
    findings: list[dict[str, Any]] = []

    def add(rule: str, table: str, field: str, measured: dict[str, Any], target_field: str | None = None) -> None:
        evidence = {
            "source_table": table,
            "source_field": field,
            "rule_name": rule,
            "measured_values": measured,
        }
        if target_field is not None:
            evidence["target_field"] = target_field
        findings.append({"rule": rule, "severity": SEVERITY[rule], "evidence": evidence})

    for source_table in source_schema["tables"]:
        table_name = source_table["name"]
        target_table = target_tables.get(table_name, {"fields": []})
        target_fields = {field["name"]: field for field in target_table.get("fields", [])}
        map_table = mapping_tables.get(table_name, {"mappings": [], "unmapped_fields": []})
        by_source = {item["source_field"]: item for item in map_table["mappings"]}
        rows = data[table_name]

        mapped_targets = {item["target_field"] for item in map_table["mappings"]}
        for target_field in target_table.get("fields", []):
            if target_field.get("required") and target_field["name"] not in mapped_targets:
                add("MISSING_REQUIRED_TARGET_FIELD", table_name, target_field["name"], {
                    "required": True,
                    "mapped": False,
                    "default_available": "default" in target_field,
                }, target_field["name"])

        for field in source_table["fields"]:
            name = field["name"]
            item = by_source.get(name)
            if item:
                target = target_fields[item["target_field"]]
                if not type_compatible(field.get("type", "unknown"), target.get("type", "unknown")):
                    add("TYPE_CONFLICT", table_name, name, {
                        "source_type": field.get("type", "unknown"),
                        "target_type": target.get("type", "unknown"),
                    }, target["name"])
                target_type = target.get("type", "")
                values = [row.get(name, "") for row in rows]
                if target_type == "date":
                    formats: dict[str, list[int]] = {}
                    invalid_rows = []
                    for index, value in enumerate(values, 1):
                        try:
                            _, fmt = parse_date(value)
                            formats.setdefault(fmt, []).append(index)
                        except ValueError:
                            if value.strip():
                                invalid_rows.append(index)
                    if invalid_rows:
                        add("UNPARSEABLE_DATE", table_name, name, {
                            "invalid_count": len(invalid_rows),
                            "row_numbers": invalid_rows,
                            "samples": [values[index - 1] for index in invalid_rows[:3]],
                        }, target["name"])
                    if len(formats) > 1:
                        add("MIXED_DATE_FORMATS", table_name, name, {
                            "format_names": sorted(formats),
                            "format_counts": {key: len(formats[key]) for key in sorted(formats)},
                        }, target["name"])
                if target_type in {"decimal", "numeric", "number", "money"}:
                    invalid_rows = []
                    lossy_rows = []
                    total_loss = 0
                    for index, value in enumerate(values, 1):
                        if not value.strip():
                            continue
                        try:
                            amount = parse_currency(value)
                            rounded, loss = quantize_currency(amount)
                            if loss:
                                lossy_rows.append(index)
                                total_loss += int(loss * 1000)
                        except ValueError:
                            invalid_rows.append(index)
                    if invalid_rows:
                        add("UNPARSEABLE_CURRENCY", table_name, name, {
                            "invalid_count": len(invalid_rows),
                            "row_numbers": invalid_rows,
                            "samples": [values[index - 1] for index in invalid_rows[:3]],
                        }, target["name"])
                    if lossy_rows:
                        add("LOSSY_TRANSFORM", table_name, name, {
                            "row_numbers": lossy_rows,
                            "rounded_precision": 2,
                            "total_absolute_loss": f"{total_loss / 1000:.3f}",
                        }, target["name"])

            if field.get("primary_key"):
                occurrences: dict[str, list[int]] = {}
                for index, row in enumerate(rows, 1):
                    occurrences.setdefault(row.get(name, ""), []).append(index)
                duplicate_groups = {key: indexes for key, indexes in occurrences.items() if key and len(indexes) > 1}
                if duplicate_groups:
                    add("DUPLICATE_KEY", table_name, name, {
                        "duplicate_group_count": len(duplicate_groups),
                        "duplicate_row_count": sum(len(indexes) - 1 for indexes in duplicate_groups.values()),
                        "duplicate_values": sorted(duplicate_groups),
                        "row_numbers": sorted(index for indexes in duplicate_groups.values() for index in indexes[1:]),
                    })

        for foreign_key in source_table.get("foreign_keys", []):
            parent_rows = data.get(foreign_key["table"], [])
            parent_values = {row.get(foreign_key["target_field"], "") for row in parent_rows}
            orphans = [(index, row.get(foreign_key["field"], "")) for index, row in enumerate(rows, 1)
                       if row.get(foreign_key["field"], "") and row.get(foreign_key["field"], "") not in parent_values]
            if orphans:
                add("ORPHAN_REFERENCE", table_name, foreign_key["field"], {
                    "orphan_count": len(orphans),
                    "row_numbers": [index for index, _ in orphans],
                    "orphan_values": sorted({value for _, value in orphans}),
                    "target_table": foreign_key["table"],
                    "target_field": foreign_key["target_field"],
                })

    rule_order = {rule: index for index, rule in enumerate(SEVERITY)}
    table_order = {table["name"]: index for index, table in enumerate(source_schema["tables"])}
    findings.sort(key=lambda item: (
        table_order[item["evidence"]["source_table"]],
        item["evidence"]["source_field"],
        rule_order[item["rule"]],
    ))
    for index, finding in enumerate(findings, 1):
        finding["flag_id"] = f"FLAG-{index:03d}"
    return findings


def flag_report(source_dir, mapping: dict[str, Any]) -> dict[str, Any]:
    return {
        "honesty_boundary": HONESTY_BOUNDARY,
        "report_version": 1,
        "mapping_version": mapping["mapping_version"],
        "flags": collect_flags(source_dir, mapping),
        "severity_semantics": {
            "critical": "Blocks safe loading until resolved or explicitly quarantined.",
            "high": "Requires a documented correction, default, or quarantine decision.",
            "medium": "Review the conversion and accept or reduce measurable loss.",
            "low": "Informational evidence that should be reviewed during planning.",
        },
    }\n