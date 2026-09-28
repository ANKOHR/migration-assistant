from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import pytest

from migration_assistant.common import canonical_json, read_json
from migration_assistant.fixtures import generate_fixtures
from migration_assistant.flagger import flag_report
from migration_assistant.inspector import inspect_source
from migration_assistant.mapper import build_mapping
from migration_assistant.planner import build_plan
from migration_assistant.transformer import run_transform
from migration_assistant.validator import validate_result


@pytest.fixture
def fixture_root(tmp_path: Path) -> Path:
    out = tmp_path / "fixtures"
    generate_fixtures(out)
    return out


@pytest.fixture
def mapped(fixture_root: Path):
    mapping = build_mapping(fixture_root / "source", fixture_root / "target-schema.json")
    return fixture_root, mapping


def test_fixture_generation_is_deterministic_and_fictional(tmp_path: Path) -> None:
    first = generate_fixtures(tmp_path / "first")
    second = generate_fixtures(tmp_path / "second")
    paths = ["source/source_schema.json", "source/customers.csv", "source/orders.csv", "target-schema.json", "FIXTURE-NOTICE.txt"]
    for relative in paths:
        assert (tmp_path / "first" / relative).read_bytes() == (tmp_path / "second" / relative).read_bytes()
    schema = read_json(first["source_dir"] / "source_schema.json")
    assert schema["fictional"] is True
    assert schema["seed"] == 20260928
    assert "No real system" in (tmp_path / "first" / "FIXTURE-NOTICE.txt").read_text()


def test_inspector_measures_null_duplicates_types_and_orphans(fixture_root: Path) -> None:
    profile = inspect_source(fixture_root / "source")
    assert canonical_json(profile).encode() == canonical_json(inspect_source(fixture_root / "source")).encode()
    tables = {table["name"]: table for table in profile["tables"]}
    customers = {field["name"]: field for field in tables["customers"]["fields"]}
    orders = {field["name"]: field for field in tables["orders"]["fields"]}
    assert tables["customers"]["row_count"] == 2
    assert customers["email"]["null_count"] == 1
    assert customers["email"]["null_rate"] == 0.5
    assert customers["customer_id"]["inferred_type"] == "integer"
    assert tables["orders"]["row_count"] == 7
    assert tables["orders"]["duplicate_primary_key_count"] == 1
    assert orders["order_id"]["duplicate_key_count"] == 1
    assert orders["order_date"]["inferred_type"] == "string"
    assert orders["amount"]["inferred_type"] == "mixed_currency"
    assert tables["orders"]["foreign_keys"][0]["orphan_count"] == 1


def test_mapping_confidence_rules_and_unmapped_fields(mapped) -> None:
    _, mapping = mapped
    order_map = next(table for table in mapping["tables"] if table["source_table"] == "orders")
    scores = [item["confidence"] for item in order_map["mappings"]]
    assert scores == sorted(scores, reverse=True)
    mapping_by_source = {item["source_field"]: item for item in order_map["mappings"]}
    assert mapping_by_source["order_id"]["confidence"] > mapping_by_source["order_date"]["confidence"]
    assert {rule["name"] for rule in mapping_by_source["order_id"]["rules"]} >= {
        "exact_name_match", "type_compatibility"
    }
    assert any(
        rule["name"] == "normalized_name_match"
        for table in mapping["tables"] for item in table["mappings"] for rule in item["rules"]
    )
    assert "legacy_note" in order_map["unmapped_fields"]
    assert {item["field"] for item in order_map["missing_required_targets"]} == {"status"}


def test_each_planted_defect_has_its_intended_flag_and_evidence(mapped) -> None:
    fixture_root, mapping = mapped
    report = flag_report(fixture_root / "source", mapping)
    rules = [finding["rule"] for finding in report["flags"]]
    assert set(rules) == {
        "TYPE_CONFLICT", "MISSING_REQUIRED_TARGET_FIELD", "DUPLICATE_KEY", "ORPHAN_REFERENCE",
        "UNPARSEABLE_CURRENCY", "UNPARSEABLE_DATE", "MIXED_DATE_FORMATS", "LOSSY_TRANSFORM",
    }
    assert rules.count("TYPE_CONFLICT") == 2
    assert len(report["flags"]) == 9
    assert len({item["flag_id"] for item in report["flags"]}) == len(report["flags"])
    severities = {item["rule"]: item["severity"] for item in report["flags"]}
    assert severities["DUPLICATE_KEY"] == "critical"
    assert severities["LOSSY_TRANSFORM"] == "medium"
    for finding in report["flags"]:
        evidence = finding["evidence"]
        assert evidence["source_table"]
        assert evidence["source_field"]
        assert evidence["rule_name"] == finding["rule"]
        assert isinstance(evidence["measured_values"], dict)


def test_mapping_rejects_changed_fixture_bytes(mapped, tmp_path: Path) -> None:
    fixture_root, mapping = mapped
    source_dir = tmp_path / "changed-source"
    shutil.copytree(fixture_root / "source", source_dir)
    orders_path = source_dir / "orders.csv"
    orders_path.write_text(orders_path.read_text(encoding="utf-8").replace("$19.99", "$29.99"), encoding="utf-8")
    with pytest.raises(ValueError, match="fingerprint"):
        run_transform(mapping, source_dir)


def test_plan_steps_are_topological_and_cover_flags(mapped) -> None:
    _, mapping = mapped
    plan = build_plan(mapping)
    positions = {step["step_id"]: index for index, step in enumerate(plan["steps"])}
    for index, step in enumerate(plan["steps"]):
        for dependency in step["depends_on"]:
            assert positions[dependency] < index
    step_flags = {flag_id for step in plan["steps"] for flag_id in step["flag_ids_to_handle"]}
    assert set(plan["flag_ids"]) <= step_flags
    assert plan["steps"][4]["step_id"] == "orders.extract"
    assert plan["steps"][4]["depends_on"] == ["customers.load"]
    assert "No live migration" in plan["plan_header"]


def test_transform_counts_coercions_quarantines_and_stays_in_memory(mapped, tmp_path: Path) -> None:
    fixture_root, mapping = mapped
    before = set(tmp_path.iterdir())
    report = run_transform(mapping, fixture_root)
    after = set(tmp_path.iterdir())
    assert before == after
    tables = {table["table"]: table for table in report["tables"]}
    assert (tables["customers"]["rows_in"], tables["customers"]["rows_out"]) == (2, 2)
    assert (tables["orders"]["rows_in"], tables["orders"]["rows_out"], tables["orders"]["rows_quarantined"]) == (7, 3, 4)
    assert report["rows_dropped"] == 0
    reasons = {rule for row in report["quarantined_row_reasons"] for rule in row["rules"]}
    assert reasons == {"DUPLICATE_KEY", "ORPHAN_REFERENCE", "UNPARSEABLE_CURRENCY", "UNPARSEABLE_DATE"}
    amount = tables["orders"]["per_field_coercions"]["amount"]
    assert amount["failed"] == 1
    assert amount["lossy"] == 1
    assert tables["orders"]["loss_statistics"]["amount"]["absolute_loss_values"] == ["0.004"]
    assert tables["orders"]["per_field_coercions"]["order_date"]["failed"] == 1


def test_clean_result_validates_and_doctored_rows_fail(mapped, tmp_path: Path) -> None:
    fixture_root, mapping = mapped
    result_dir = tmp_path / "result"
    run_transform(mapping, fixture_root, result_dir)
    clean = validate_result(result_dir)
    assert clean["status"] == "pass"
    assert {check["check_name"] for check in clean["checks"]} == {
        "row_count_parity", "key_uniqueness", "required_field_coverage", "sample_spot_checks"
    }

    dropped = tmp_path / "dropped"
    shutil.copytree(result_dir, dropped)
    rows = _read_csv(dropped / "orders.csv")
    _write_csv(dropped / "orders.csv", rows[:-1])
    failed = validate_result(dropped)
    assert failed["status"] == "fail"
    assert any(check["check_name"] == "row_count_parity" and check["status"] == "fail" for check in failed["checks"])

    broken = tmp_path / "broken"
    shutil.copytree(result_dir, broken)
    rows = _read_csv(broken / "orders.csv")
    rows[1]["order_id"] = rows[0]["order_id"]
    _write_csv(broken / "orders.csv", rows)
    failed = validate_result(broken)
    assert failed["status"] == "fail"
    assert any(check["check_name"] == "key_uniqueness" and check["status"] == "fail" for check in failed["checks"])

    required = tmp_path / "missing-required"
    shutil.copytree(result_dir, required)
    rows = _read_csv(required / "orders.csv")
    for row in rows:
        row.pop("status")
    _write_csv(required / "orders.csv", rows)
    failed = validate_result(required)
    coverage = next(check for check in failed["checks"] if check["check_name"] == "required_field_coverage" and check["evidence"]["source_table"] == "orders")
    assert coverage["status"] == "fail"


def test_full_pipeline_reports_are_byte_deterministic(tmp_path: Path) -> None:
    reports = []
    for label in ("one", "two"):
        root = tmp_path / label
        generate_fixtures(root / "fixtures")
        mapping = build_mapping(root / "fixtures" / "source", root / "fixtures" / "target-schema.json")
        plan = build_plan(mapping)
        flags = flag_report(root / "fixtures" / "source", mapping)
        result = root / "result"
        transform = run_transform(mapping, root / "fixtures", result)
        validation = validate_result(result)
        reports.append((mapping, plan, flags, transform, validation))
    for first, second in zip(reports[0], reports[1]):
        assert canonical_json(first).encode() == canonical_json(second).encode()
    assert (tmp_path / "one" / "result" / "manifest.json").read_bytes() == (tmp_path / "two" / "result" / "manifest.json").read_bytes()
    assert (tmp_path / "one" / "result" / "transform-report.json").read_bytes() == (tmp_path / "two" / "result" / "transform-report.json").read_bytes()


def test_cli_smoke_for_every_subcommand(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    fixture_path = tmp_path / "fixtures"
    result_path = tmp_path / "result"

    def cli(*args: str) -> dict:
        from migration_assistant.cli import main
        assert main(list(args)) == 0
        return json.loads(capsys.readouterr().out)

    generated = cli("fixtures", "--out", str(fixture_path))
    assert generated["status"] == "fixtures_generated"
    profile = cli("inspect", str(fixture_path / "source"))
    assert profile["profile_version"] == 1
    mapping = cli("map", "--source", str(fixture_path / "source"), "--target", str(fixture_path / "target-schema.json"), "--out", str(tmp_path / "mapping.json"))
    assert mapping["known_flags"]
    plan = cli("plan", "--mapping", str(tmp_path / "mapping.json"))
    assert plan["steps"]
    transform = cli("test-transforms", "--mapping", str(tmp_path / "mapping.json"), "--fixtures", str(fixture_path), "--out", str(result_path))
    assert transform["quarantined_rows"] == 4
    flags = cli("flag", "--source", str(fixture_path / "source"), "--mapping", str(tmp_path / "mapping.json"))
    assert len(flags["flags"]) == 9
    validation = cli("validate", "--result", str(result_path))
    assert validation["status"] == "pass"
    audit_lines = (result_path / "ma-audit.jsonl").read_text().splitlines()
    assert len(audit_lines) == 1
    audit = json.loads(audit_lines[0])
    assert audit["command"] == "test-transforms"
    assert audit["mapping_version"] == mapping["mapping_version"]
    assert len(audit["flags_raised"]) == 9
    root_audit = [json.loads(line) for line in (tmp_path / "ma-audit.jsonl").read_text().splitlines()]
    assert [entry["command"] for entry in root_audit] == ["fixtures", "inspect", "map", "plan", "flag", "validate"]


def test_cli_validate_returns_failure_for_invalid_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "fixtures"
    generate_fixtures(root)
    mapping = build_mapping(root / "source", root / "target-schema.json")
    result = tmp_path / "result"
    run_transform(mapping, root, result)
    rows = _read_csv(result / "orders.csv")
    rows.pop()
    _write_csv(result / "orders.csv", rows)
    from migration_assistant.cli import main
    assert main(["validate", "--result", str(result)]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "fail"


def test_reports_and_documentation_have_no_em_dash() -> None:
    root = Path(__file__).resolve().parents[1]
    for path in [*root.joinpath("migration_assistant").glob("*.py"), root / "README.md"]:
        assert chr(0x2014) not in path.read_text(encoding="utf-8"), path


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
