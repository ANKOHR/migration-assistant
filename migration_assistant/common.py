"""Shared deterministic serialization and filesystem helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

HONESTY_BOUNDARY = (
    "Rule-based planning and fixture validation only. No real system is connected, "
    "no live data is read, and no real migration or database write is performed."
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(value), encoding="utf-8", newline="\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def source_data_fingerprint(data: dict[str, list[dict[str, str]]]) -> str:
    return digest({name: data[name] for name in sorted(data)})


def source_dir_from(path: Path) -> Path:
    """Accept either the generated fixture root or its source subdirectory."""
    if (path / "source_schema.json").exists():
        return path
    if (path / "source" / "source_schema.json").exists():
        return path / "source"
    raise FileNotFoundError(f"No source_schema.json found under {path}")


def load_source(source_dir: Path) -> tuple[dict[str, Any], dict[str, list[dict[str, str]]]]:
    import csv

    source_dir = source_dir_from(source_dir)
    schema = read_json(source_dir / "source_schema.json")
    data: dict[str, list[dict[str, str]]] = {}
    for table in schema["tables"]:
        csv_path = source_dir / table["file"]
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            data[table["name"]] = list(csv.DictReader(handle))
    return schema, data


def is_null(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def field_by_name(table: dict[str, Any], name: str) -> dict[str, Any] | None:
    for field in table.get("fields", []):
        if field["name"] == name:
            return field
    return None\n