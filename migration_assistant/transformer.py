"""Run target coercions in memory and optionally write an isolated result set."""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path
from typing import Any

from .common import HONESTY_BOUNDARY, load_source, source_data_fingerprint, source_dir_from, write_json
from .typing_rules import parse_currency, parse_date, quantize_currency


def _coerce(value: str, target_type: str) -> tuple[Any, bool, str | None, str | None]:
    if value is None or value.strip() == "":
        return "", False, None, None
    if target_type == "string":
        return value, False, None, None
    if target_type == "integer":
        return int(value), False, None, None
    if target_type in {"decimal", "numeric", "number", "money"}:
        parsed = parse_currency(value)
        rounded, loss = quantize_currency(parsed)
        return f"{rounded:.2f}", True, "LOSSY_TRANSFORM" if loss else None, f"{loss:.3f}" if loss else None
    if target_type in {"date", "datetime", "timestamp"}:
        normalized, _ = parse_date(value)
        return normalized, True, None, None
    return value, False, None, None


def _ordered_tables(source_schema: dict[str, Any]) -> list[str]:
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


def run_transform(mapping: dict[str, Any], fixtures_dir: Path, out_dir: Path | None = None) -> dict[str, Any]:
    source_dir = source_dir_from(fixtures_dir)
    source_schema, data = load_source(source_dir)
    if source_schema != mapping["source_schema"]:
        raise ValueError("Fixture source schema does not match the mapping")
    if source_data_fingerprint(data) != mapping["source_data_fingerprint"]:
        raise ValueError("Fixture data does not match the mapping fingerprint")
    target_schema = mapping["target_schema"]
    target_tables = {table["name"]: table for table in target_schema["tables"]}
    mapping_tables = {table["source_table"]: table for table in mapping["tables"]}
    source_tables = {table["name"]: table for table in source_schema["tables"]}
    loaded: dict[str, list[dict[str, Any]]] = {}
    quarantined: list[dict[str, Any]] = []
    table_reports = []
    source_counts = {name: len(rows) for name, rows in data.items()}

    for table_name in _ordered_tables(source_schema):
        source_table = source_tables[table_name]
        target_table = target_tables.get(table_name, {"name": table_name, "fields": []})
        target_fields = target_table.get("fields", [])
        map_table = mapping_tables.get(table_name, {"mappings": []})
        map_by_source = {item["source_field"]: item for item in map_table["mappings"]}
        target_to_source = {item["target_field"]: item["source_field"] for item in map_table["mappings"]}
        key_fields = [field["name"] for field in target_fields if field.get("primary_key")]
        seen_keys: set[tuple[Any, ...]] = set()
        table_loaded: list[dict[str, Any]] = []
        field_stats: dict[str, dict[str, Any]] = {
            field["name"]: {"values_seen": 0, "converted": 0, "unchanged": 0, "failed": 0, "lossy": 0, "loss_amounts": []}
            for field in target_fields
        }
        for row_number, source_row in enumerate(data[table_name], 1):
            output: dict[str, Any] = {}
            reasons: list[dict[str, Any]] = []
            failed_targets: set[str] = set()
            for target_field in target_fields:
                target_name = target_field["name"]
                source_name = target_to_source.get(target_name)
                stats = field_stats[target_name]
                if source_name is None:
                    output[target_name] = target_field.get("default", "")
                    if target_field.get("required") and (output[target_name] is None or str(output[target_name]).strip() == ""):
                        reasons.append({"rule_name": "MISSING_REQUIRED_TARGET_FIELD", "field": target_name})
                    continue
                raw_value = source_row.get(source_name, "")
                if raw_value.strip():
                    stats["values_seen"] += 1
                try:
                    value, converted, loss_rule, loss_amount = _coerce(raw_value, target_field.get("type", "string"))
                    output[target_name] = value
                    if raw_value.strip():
                        stats["converted" if converted else "unchanged"] += 1
                    if loss_rule:
                        stats["lossy"] += 1
                        stats["loss_amounts"].append(loss_amount)
                except ValueError as exc:
                    output[target_name] = ""
                    stats["failed"] += 1
                    failed_targets.add(target_name)
                    rule = "UNPARSEABLE_DATE" if target_field.get("type") == "date" else "UNPARSEABLE_CURRENCY"
                    reasons.append({"rule_name": rule, "field": source_name, "message": str(exc)})
                except (TypeError, OverflowError) as exc:
                    output[target_name] = ""
                    stats["failed"] += 1
                    failed_targets.add(target_name)
                    reasons.append({"rule_name": "TYPE_CONFLICT", "field": source_name, "message": str(exc)})

            for target_field in target_fields:
                target_name = target_field["name"]
                if target_field.get("required") and target_name not in failed_targets and (output.get(target_name) is None or str(output.get(target_name)).strip() == ""):
                    if not any(reason["rule_name"] == "MISSING_REQUIRED_TARGET_FIELD" and reason.get("field") == target_name for reason in reasons):
                        reasons.append({"rule_name": "MISSING_REQUIRED_TARGET_FIELD", "field": target_name})

            key = tuple(str(output.get(name, "")) for name in key_fields)
            if key_fields and key in seen_keys:
                reasons.append({"rule_name": "DUPLICATE_KEY", "field": ",".join(key_fields)})
            elif key_fields:
                seen_keys.add(key)

            for reference in source_table.get("foreign_keys", []):
                source_fk = reference["field"]
                mapped_fk = map_by_source.get(source_fk)
                target_fk = mapped_fk["target_field"] if mapped_fk else source_fk
                parent_table_name = reference["table"]
                parent_target = target_tables[parent_table_name]
                parent_key = next(field["name"] for field in parent_target["fields"] if field.get("primary_key"))
                parent_values = {str(parent.get(parent_key, "")) for parent in loaded.get(parent_table_name, [])}
                if str(output.get(target_fk, "")) not in parent_values:
                    reasons.append({"rule_name": "ORPHAN_REFERENCE", "field": source_fk, "target_table": parent_table_name})

            if reasons:
                quarantined.append({
                    "table": table_name,
                    "source_row_number": row_number,
                    "source_row": source_row,
                    "reasons": reasons,
                })
            else:
                table_loaded.append(output)
        loaded[table_name] = table_loaded
        table_reports.append({
            "table": table_name,
            "rows_in": len(data[table_name]),
            "rows_out": len(table_loaded),
            "rows_quarantined": sum(1 for item in quarantined if item["table"] == table_name),
            "per_field_coercions": {
                name: {
                    "values_seen": stat["values_seen"],
                    "converted": stat["converted"],
                    "unchanged": stat["unchanged"],
                    "failed": stat["failed"],
                    "lossy": stat["lossy"],
                }
                for name, stat in field_stats.items()
            },
            "loss_statistics": {
                name: {"lossy_value_count": stat["lossy"], "absolute_loss_values": stat["loss_amounts"]}
                for name, stat in field_stats.items() if stat["lossy"]
            },
        })

    report = {
        "honesty_boundary": HONESTY_BOUNDARY,
        "report_version": 1,
        "mapping_version": mapping["mapping_version"],
        "execution_mode": "in_memory_fixture_transform",
        "tables": table_reports,
        "quarantined_rows": len(quarantined),
        "quarantined_row_reasons": [
            {"table": item["table"], "source_row_number": item["source_row_number"], "rules": [r["rule_name"] for r in item["reasons"]]}
            for item in quarantined
        ],
        "rows_dropped": 0,
    }
    if out_dir is not None:
        _write_results(out_dir, mapping, loaded, quarantined, report, source_counts, target_schema)
    return report


def _write_results(out_dir: Path, mapping: dict[str, Any], loaded: dict[str, list[dict[str, Any]]],
                   quarantined: list[dict[str, Any]], report: dict[str, Any],
                   source_counts: dict[str, int], target_schema: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    target_tables = {table["name"]: table for table in target_schema["tables"]}
    for table_name, rows in loaded.items():
        target_fields = [field["name"] for field in target_tables.get(table_name, {"fields": []})["fields"]]
        with (out_dir / f"{table_name}.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=target_fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    write_json(out_dir / "quarantine.json", quarantined)
    expected_samples = {
        table: [{name: "" if value is None else str(value) for name, value in row.items()} for row in rows[:3]]
        for table, rows in loaded.items()
    }
    manifest = {
        "honesty_boundary": HONESTY_BOUNDARY,
        "result_format_version": 1,
        "mapping_version": mapping["mapping_version"],
        "source_row_counts": source_counts,
        "output_row_counts": {table: len(rows) for table, rows in loaded.items()},
        "required_fields": {
            table["name"]: [field["name"] for field in table["fields"] if field.get("required")]
            for table in target_schema["tables"]
        },
        "primary_keys": {
            table["name"]: [field["name"] for field in table["fields"] if field.get("primary_key")]
            for table in target_schema["tables"]
        },
        "expected_samples": expected_samples,
    }
    write_json(out_dir / "manifest.json", manifest)
    write_json(out_dir / "transform-report.json", report)
