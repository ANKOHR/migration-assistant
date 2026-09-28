"""Generate the versioned synthetic source and target schemas and CSV files."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .common import HONESTY_BOUNDARY, write_json

FIXTURE_SEED = 20260928


def fixture_schemas() -> tuple[dict[str, Any], dict[str, Any]]:
    source = {
        "schema_version": 1,
        "system_name": "Fictional Sample Shop",
        "fictional": True,
        "seed": FIXTURE_SEED,
        "tables": [
            {
                "name": "customers",
                "file": "customers.csv",
                "fields": [
                    {"name": "customer_id", "type": "integer", "nullable": False, "primary_key": True},
                    {"name": "customer-name", "type": "string", "nullable": False},
                    {"name": "signup_date", "type": "date", "nullable": False},
                    {"name": "email", "type": "string", "nullable": True},
                ],
                "foreign_keys": [],
            },
            {
                "name": "orders",
                "file": "orders.csv",
                "fields": [
                    {"name": "order_id", "type": "integer", "nullable": False, "primary_key": True},
                    {
                        "name": "customer_id",
                        "type": "integer",
                        "nullable": False,
                        "foreign_key": {"table": "customers", "field": "customer_id"},
                    },
                    {"name": "order_date", "type": "string", "nullable": False},
                    {"name": "amount", "type": "string", "nullable": False},
                    {"name": "legacy_note", "type": "string", "nullable": True},
                ],
                "foreign_keys": [
                    {"field": "customer_id", "table": "customers", "target_field": "customer_id"}
                ],
            },
        ],
    }
    target = {
        "schema_version": 1,
        "system_name": "Fictional Target Ledger",
        "fictional": True,
        "tables": [
            {
                "name": "customers",
                "fields": [
                    {"name": "customer_id", "type": "integer", "required": True, "primary_key": True},
                    {"name": "customer_name", "type": "string", "required": True},
                    {"name": "signup_date", "type": "date", "required": True},
                    {"name": "email", "type": "string", "required": False},
                ],
            },
            {
                "name": "orders",
                "fields": [
                    {"name": "order_id", "type": "integer", "required": True, "primary_key": True},
                    {
                        "name": "customer_id",
                        "type": "integer",
                        "required": True,
                        "foreign_key": {"table": "customers", "field": "customer_id"},
                    },
                    {"name": "order_date", "type": "date", "required": True},
                    {"name": "amount", "type": "decimal", "required": True},
                    {"name": "status", "type": "string", "required": True, "default": "pending"},
                ],
            },
        ],
    }
    return source, target


def fixture_rows() -> dict[str, list[dict[str, str]]]:
    return {
        "customers": [
            {"customer_id": "1", "customer-name": "Ada North", "signup_date": "2026-01-02", "email": "ada@example.test"},
            {"customer_id": "2", "customer-name": "Lin South", "signup_date": "2026-01-04", "email": ""},
        ],
        "orders": [
            {"order_id": "101", "customer_id": "1", "order_date": "2026-01-03", "amount": "$19.99", "legacy_note": "first"},
            {"order_id": "102", "customer_id": "2", "order_date": "01/04/2026", "amount": "12.50", "legacy_note": "second"},
            {"order_id": "102", "customer_id": "1", "order_date": "2026-01-05", "amount": "$8.00", "legacy_note": "duplicate key"},
            {"order_id": "103", "customer_id": "99", "order_date": "2026-01-06", "amount": "$4.00", "legacy_note": "orphan reference"},
            {"order_id": "104", "customer_id": "1", "order_date": "2026-01-07", "amount": "unknown", "legacy_note": "bad currency"},
            {"order_id": "105", "customer_id": "2", "order_date": "not-a-date", "amount": "$5.00", "legacy_note": "bad date"},
            {"order_id": "106", "customer_id": "1", "order_date": "2026-01-08", "amount": "€3.456", "legacy_note": "precision loss"},
        ],
    }


def generate_fixtures(out_dir: Path) -> dict[str, Any]:
    source_schema, target_schema = fixture_schemas()
    rows_by_table = fixture_rows()
    source_dir = out_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    write_json(source_dir / "source_schema.json", source_schema)
    write_json(out_dir / "target-schema.json", target_schema)
    for table in source_schema["tables"]:
        rows = rows_by_table[table["name"]]
        with (source_dir / table["file"]).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=[field["name"] for field in table["fields"]], lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    (out_dir / "FIXTURE-NOTICE.txt").write_text(
        "Synthetic fictional fixtures generated locally. No external downloads or real systems are used.\n"
        f"Deterministic seed: {FIXTURE_SEED}\n{HONESTY_BOUNDARY}\n",
        encoding="utf-8",
        newline="\n",
    )
    return {"source_dir": source_dir, "target_schema": out_dir / "target-schema.json", "seed": FIXTURE_SEED}\n