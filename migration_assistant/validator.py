"""Validate a written fixture result set using its source accounting manifest."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import Any

from .common import HONESTY_BOUNDARY, read_json


def validate_result(result_dir: Path) -> dict[str, Any]:
    manifest = read_json(result_dir / "manifest.json")
    quarantined = read_json(result_dir / "quarantine.json")
    quarantine_counts = Counter(item["table"] for item in quarantined)
    checks: list[dict[str, Any]] = []

    def add(name: str, table: str, passed: bool, evidence: dict[str, Any]) -> None:
        checks.append({
            "check_name": name,
            "status": "pass" if passed else "fail",
            "evidence": {"source_table": table, "source_field": "*", "rule_name": name, "measured_values": evidence},
        })

    tables = sorted(manifest["source_row_counts"])
    rows_by_table: dict[str, list[dict[str, str]]] = {}
    headers_by_table: dict[str, list[str]] = {}
    for table in tables:
        csv_path = result_dir / f"{table}.csv"
        if csv_path.exists():
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                headers_by_table[table] = list(reader.fieldnames or [])
                rows = list(reader)
        else:
            headers_by_table[table] = []
            rows = []
        rows_by_table[table] = rows
        expected_in = int(manifest["source_row_counts"][table])
        expected_out = int(manifest["output_row_counts"].get(table, 0))
        observed_out = len(rows)
        accounted = observed_out + quarantine_counts[table]
        add("row_count_parity", table, accounted == expected_in and observed_out == expected_out, {
            "source_rows": expected_in,
            "output_rows": observed_out,
            "manifest_output_rows": expected_out,
            "quarantined_rows": quarantine_counts[table],
            "accounted_rows": accounted,
        })

    for table in tables:
        rows = rows_by_table[table]
        key_fields = manifest["primary_keys"].get(table, [])
        duplicate_keys = []
        if key_fields:
            values = [tuple(row.get(name, "") for name in key_fields) for row in rows]
            duplicate_keys = [list(key) for key, count in Counter(values).items() if count > 1]
        add("key_uniqueness", table, not duplicate_keys, {
            "key_fields": key_fields,
            "duplicate_key_count": len(duplicate_keys),
            "duplicate_keys": duplicate_keys,
        })

        required = manifest["required_fields"].get(table, [])
        missing = [{"row_number": 0, "field": field, "reason": "column_missing"}
                   for field in required if field not in headers_by_table[table]]
        for row_number, row in enumerate(rows, 1):
            for field in required:
                if field not in row or row[field] is None or not row[field].strip():
                    missing.append({"row_number": row_number, "field": field})
        add("required_field_coverage", table, not missing, {
            "required_fields": required,
            "missing_values": missing,
        })

        expected_samples = manifest["expected_samples"].get(table, [])
        observed_samples = rows[:len(expected_samples)]
        samples_match = observed_samples == expected_samples
        add("sample_spot_checks", table, samples_match, {
            "expected_sample_count": len(expected_samples),
            "observed_sample_count": len(observed_samples),
            "matching": samples_match,
        })

    return {
        "honesty_boundary": HONESTY_BOUNDARY,
        "report_version": 1,
        "mapping_version": manifest["mapping_version"],
        "status": "pass" if all(check["status"] == "pass" for check in checks) else "fail",
        "checks": checks,
    }
