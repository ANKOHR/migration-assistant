"""Rule-based source-to-target field matching with auditable confidence scores."""

from __future__ import annotations

import re
from typing import Any

from .common import HONESTY_BOUNDARY, digest, load_source, read_json, source_data_fingerprint, source_dir_from
from .typing_rules import pattern_support, type_compatible


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def build_mapping(source_dir, target_schema_path) -> dict[str, Any]:
    source_dir = source_dir_from(source_dir)
    source_schema, rows = load_source(source_dir)
    target_schema = read_json(target_schema_path)
    target_by_name = {table["name"]: table for table in target_schema["tables"]}
    result_tables = []
    for source_table in source_schema["tables"]:
        table_name = source_table["name"]
        target_table = target_by_name.get(table_name, {"name": table_name, "fields": []})
        target_fields = target_table.get("fields", [])
        mappings = []
        unmapped = []
        matched_target_fields: set[str] = set()
        for source_field in source_table["fields"]:
            source_name = source_field["name"]
            values = [row.get(source_name, "") for row in rows[table_name]]
            candidates = []
            for target_field in target_fields:
                target_name = target_field["name"]
                if target_name in matched_target_fields:
                    continue
                exact = source_name.lower() == target_name.lower()
                normalized = _normalize(source_name) == _normalize(target_name)
                compatible = type_compatible(source_field.get("type", "unknown"), target_field.get("type", "unknown"))
                support = pattern_support(values, target_field.get("type", "unknown"))
                if not exact and not normalized:
                    continue
                score = 0.74 if exact else 0.64
                rules = []
                if exact:
                    rules.append({"name": "exact_name_match", "effect": 0.74, "evidence": {"source": source_name, "target": target_name}})
                else:
                    rules.append({"name": "normalized_name_match", "effect": 0.64, "evidence": {"source_normalized": _normalize(source_name), "target_normalized": _normalize(target_name)}})
                if compatible:
                    score += 0.15
                    rules.append({"name": "type_compatibility", "effect": 0.15, "evidence": {"source_type": source_field.get("type", "unknown"), "target_type": target_field.get("type", "unknown"), "compatible": True}})
                else:
                    score -= 0.08
                    rules.append({"name": "type_compatibility", "effect": -0.08, "evidence": {"source_type": source_field.get("type", "unknown"), "target_type": target_field.get("type", "unknown"), "compatible": False}})
                if support >= 0.5:
                    score += 0.08
                    rules.append({"name": "value_pattern_evidence", "effect": 0.08, "evidence": {"target_type_support": round(support, 6), "sample_count": len([value for value in values if value.strip()])}})
                candidates.append({
                    "source_field": source_name,
                    "target_field": target_name,
                    "target_type": target_field.get("type", "unknown"),
                    "confidence": round(min(0.99, max(0.0, score)), 3),
                    "rules": rules,
                })
            if candidates:
                selected = max(candidates, key=lambda item: (item["confidence"], item["target_field"]))
                matched_target_fields.add(selected["target_field"])
                mappings.append(selected)
            else:
                unmapped.append(source_name)
        mappings.sort(key=lambda item: (-item["confidence"], item["source_field"], item["target_field"]))
        missing_required = [
            {"field": field["name"], "default_available": "default" in field}
            for field in target_fields if field.get("required") and field["name"] not in matched_target_fields
        ]
        result_tables.append({
            "source_table": table_name,
            "target_table": target_table["name"],
            "mappings": mappings,
            "unmapped_fields": sorted(unmapped),
            "missing_required_targets": missing_required,
        })
    mapping = {
        "honesty_boundary": HONESTY_BOUNDARY,
        "mapping_format_version": 1,
        "source_schema": source_schema,
        "source_data_fingerprint": source_data_fingerprint(rows),
        "target_schema_fingerprint": digest(target_schema),
        "target_schema": target_schema,
        "tables": result_tables,
    }
    mapping["mapping_version"] = digest(mapping)
    from .flagger import collect_flags
    mapping["known_flags"] = collect_flags(source_dir, mapping)
    return mapping
