"""Schema and CSV profiler with stable ordering and measured statistics."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .common import HONESTY_BOUNDARY, is_null, load_source, source_dir_from
from .typing_rules import infer_type


def inspect_source(path) -> dict[str, Any]:
    source_dir = source_dir_from(path)
    schema, data = load_source(source_dir)
    tables_out: list[dict[str, Any]] = []
    key_values: dict[tuple[str, str], set[str]] = {}
    for table in schema["tables"]:
        rows = data[table["name"]]
        for field in table["fields"]:
            if field.get("primary_key"):
                key_values[(table["name"], field["name"])] = {
                    row[field["name"]] for row in rows if not is_null(row.get(field["name"]))
                }
    for table in schema["tables"]:
        rows = data[table["name"]]
        fields_out = []
        for field in table["fields"]:
            name = field["name"]
            values = [row.get(name, "") for row in rows]
            present = [value for value in values if not is_null(value)]
            counts = Counter(present)
            duplicate_keys = sum(count - 1 for count in counts.values()) if field.get("primary_key") else 0
            unique = sorted(counts)
            fields_out.append({
                "name": name,
                "declared_type": field.get("type", "unknown"),
                "inferred_type": infer_type(values),
                "null_count": len(values) - len(present),
                "null_rate": (len(values) - len(present)) / len(values) if values else 0.0,
                "duplicate_key_count": duplicate_keys,
                "value_samples": unique[:3],
                "candidate_enum": unique if 0 < len(unique) <= 10 else [],
                "foreign_key": field.get("foreign_key"),
            })
        foreign_keys = []
        for reference in table.get("foreign_keys", []):
            target_values = key_values.get((reference["table"], reference["target_field"]), set())
            orphan_values = sorted({
                row.get(reference["field"], "") for row in rows
                if not is_null(row.get(reference["field"])) and row.get(reference["field"]) not in target_values
            })
            foreign_keys.append({
                "field": reference["field"],
                "target_table": reference["table"],
                "target_field": reference["target_field"],
                "orphan_count": sum(1 for row in rows if row.get(reference["field"], "") in orphan_values),
                "orphan_values": orphan_values,
            })
        tables_out.append({
            "name": table["name"],
            "row_count": len(rows),
            "duplicate_primary_key_count": sum(item["duplicate_key_count"] for item in fields_out),
            "fields": fields_out,
            "foreign_keys": foreign_keys,
        })
    return {
        "honesty_boundary": HONESTY_BOUNDARY,
        "profile_version": 1,
        "system_name": schema.get("system_name", "unknown"),
        "fictional": schema.get("fictional", False),
        "tables": tables_out,
    }\n