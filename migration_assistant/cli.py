"""Command line entry point for deterministic migration planning and validation."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .common import HONESTY_BOUNDARY, canonical_json, read_json, write_json
from .fixtures import FIXTURE_SEED, generate_fixtures
from .flagger import flag_report
from .inspector import inspect_source
from .mapper import build_mapping
from .planner import build_plan
from .transformer import run_transform
from .validator import validate_result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ma", description=HONESTY_BOUNDARY)
    parser.add_argument("--audit", help="Append the audit trail to this JSONL file.")
    sub = parser.add_subparsers(dest="command", required=True)

    fixtures = sub.add_parser("fixtures", help="Generate deterministic fictional fixture files.")
    fixtures.add_argument("--out", required=True)

    inspect = sub.add_parser("inspect", help="Profile a source schema and CSV datasets.")
    inspect.add_argument("source_dir")
    inspect.add_argument("--out")

    mapping = sub.add_parser("map", help="Build a rule-based field mapping.")
    mapping.add_argument("--source", required=True)
    mapping.add_argument("--target", required=True)
    mapping.add_argument("--out")

    plan = sub.add_parser("plan", help="Generate a dependency-ordered review plan.")
    plan.add_argument("--mapping", required=True)
    plan.add_argument("--out")

    transform = sub.add_parser("test-transforms", help="Run fixture rows through mapped coercions in memory.")
    transform.add_argument("--mapping", required=True)
    transform.add_argument("--fixtures", required=True)
    transform.add_argument("--out", help="Optional result directory for transformed CSV and reports.")

    flag = sub.add_parser("flag", help="List incompatibilities with evidence pointers.")
    flag.add_argument("--source", required=True)
    flag.add_argument("--mapping", required=True)
    flag.add_argument("--out")

    validate = sub.add_parser("validate", help="Validate a transformed fixture result directory.")
    validate.add_argument("--result", required=True)
    validate.add_argument("--out")
    return parser


def _audit(path: Path, command: str, inputs: dict[str, Any], mapping_version: str | None = None,
           flags: list[str] | None = None, outcomes: list[dict[str, Any]] | None = None,
           error: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "command": command,
        "inputs": inputs,
        "mapping_version": mapping_version,
        "flags_raised": flags or [],
        "check_outcomes": outcomes or [],
    }
    if error:
        entry["error"] = error
    with path.open("a", encoding="utf-8", newline="") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def _emit(document: dict[str, Any], out: str | None = None) -> None:
    if out:
        write_json(Path(out), document)
    sys.stdout.write(canonical_json(document))


def run(args: argparse.Namespace) -> tuple[dict[str, Any], str | None, list[str], list[dict[str, Any]]]:
    if args.command == "fixtures":
        generate_fixtures(Path(args.out))
        result = {
            "honesty_boundary": HONESTY_BOUNDARY,
            "status": "fixtures_generated",
            "out": str(Path(args.out)),
            "seed": FIXTURE_SEED,
            "source_schema": "source/source_schema.json",
            "target_schema": "target-schema.json",
        }
        return result, None, [], []
    if args.command == "inspect":
        result = inspect_source(Path(args.source_dir))
        return result, None, [], []
    if args.command == "map":
        result = build_mapping(Path(args.source), Path(args.target))
        return result, result["mapping_version"], [item["flag_id"] for item in result["known_flags"]], []
    if args.command == "plan":
        mapping = read_json(Path(args.mapping))
        result = build_plan(mapping)
        return result, mapping["mapping_version"], [], []
    if args.command == "test-transforms":
        mapping = read_json(Path(args.mapping))
        output_dir = Path(args.out) if args.out else None
        result = run_transform(mapping, Path(args.fixtures), output_dir)
        return result, mapping["mapping_version"], [item["flag_id"] for item in mapping.get("known_flags", [])], []
    if args.command == "flag":
        mapping = read_json(Path(args.mapping))
        result = flag_report(Path(args.source), mapping)
        return result, mapping["mapping_version"], [item["flag_id"] for item in result["flags"]], []
    if args.command == "validate":
        result = validate_result(Path(args.result))
        return result, result.get("mapping_version"), [], result["checks"]
    raise ValueError(f"Unknown command {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    output_arg = getattr(args, "out", None)
    if getattr(args, "command", None) == "test-transforms" and output_arg and not args.audit:
        audit_path = Path(output_arg) / "ma-audit.jsonl"
    else:
        audit_path = Path(args.audit) if args.audit else Path.cwd() / "ma-audit.jsonl"
    inputs = {key: str(value) for key, value in vars(args).items() if key not in {"command", "audit"} and value is not None}
    try:
        result, mapping_version, flags, outcomes = run(args)
        _audit(audit_path, args.command, inputs, mapping_version, flags, outcomes)
        _emit(result, output_arg if args.command not in {"fixtures", "test-transforms"} else None)
        if args.command == "validate" and result["status"] != "pass":
            return 1
        return 0
    except Exception as exc:
        _audit(audit_path, args.command, inputs, error=f"{type(exc).__name__}: {exc}")
        error_result = {
            "honesty_boundary": HONESTY_BOUNDARY,
            "status": "error",
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
        sys.stdout.write(canonical_json(error_result))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
