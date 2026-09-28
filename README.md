# Migration Assistant

Migration Assistant is deterministic data migration planning and fixture validation. It inspects fictional source schemas and CSV data, proposes field mappings with rule-based confidence, creates an ordered review plan, tests conversions in memory, flags incompatibilities, and validates fixture-shaped output.

**Rule-based planning and fixture validation only.** The tool connects to no real system, reads no live data, performs no real migration, and writes nothing to any live database.

## Quickstart

Requirements: Python 3.10 or later and pytest for the test suite. Runtime code uses the Python standard library. No GPU packages or external services are used.

```powershell
python -m pip install -e .
python -m pip install pytest
ma fixtures --out .\demo-fixtures
ma inspect .\demo-fixtures\source --out .\profile.json
ma map --source .\demo-fixtures\source --target .\demo-fixtures\target-schema.json --out .\mapping.json
ma plan --mapping .\mapping.json --out .\plan.json
ma test-transforms --mapping .\mapping.json --fixtures .\demo-fixtures --out .\demo-result
ma flag --source .\demo-fixtures\source --mapping .\mapping.json --out .\flags.json
ma validate --result .\demo-result --out .\validation.json
python -m pytest
```

Commands print deterministic JSON documents containing the honesty boundary. The `--out` option writes the same document to a file when available. The transform output directory contains target-shaped fixture CSV files, a quarantine ledger, a manifest, and a transform report. Rows rejected by a rule are quarantined and remain included in source-to-result row accounting. The transform tester writes only fixture result artifacts and its audit JSONL entry to the chosen result directory.

Every CLI invocation appends one record to `ma-audit.jsonl` in the working directory. For `test-transforms --out`, its audit record is placed in that result directory. Use the root `--audit <path>` option before the subcommand to choose another location. Audit timestamps are operational metadata and are not included in deterministic reports.

The generated fixtures carry seed `20260928`. Repeated generation with the same version and seed produces byte-identical schema and CSV files. The fixture set includes a duplicate key, an orphan foreign key, an invalid currency value, an invalid date value, mixed valid date formats, a missing required target field with a target default, source-to-target type conflicts, and a currency rounding loss.

## Architecture

```text
synthetic schemas + CSV bytes
             |
             v
       schema inspector -----> profile JSON
             |
             v
       mapping rules --------> mapping JSON + evidence-backed flags
             |
             +---------------> ordered plan JSON
             |
             v
  in-memory transform -------> optional result directory + quarantine ledger
             |
             v
       result validator -----> pass/fail checks with evidence
             |
             v
       ma-audit.jsonl
```

The fixture generator, inspector, mapper, planner, transformer, flagger, validator, and CLI are separate modules under `migration_assistant/`. JSON output uses sorted keys and stable formatting. Flags cite a source table, source field, rule name, measured values, and optional target field. The mapping version includes schema and source-data fingerprints.

## HONESTY BOUNDARY

Migration plans are generated from rule-based mapping over synthetic source schemas. The tool validates on fixture datasets only. It connects to no real system, performs no real migration, and writes nothing to any live database. CLI output, plan headers, generated fixture notices, and this README state this plainly. The result CSV files are local fixture artifacts. They are not a loaded target system, and no claim is made that production data moved.

This project is planning and validation tooling, not a trained model and not a live migration runner. It uses no external downloads and does not connect to a real source or target.

## Mapping rule catalogue

Mappings are table-local and one-to-one. Every mapped source field includes the rule names and evidence used to produce its confidence score. Mappings are sorted by confidence, highest first. Unmatched source fields appear in `unmapped_fields`; target fields without a source are listed in `missing_required_targets` when required.

| Rule | Confidence effect | Evidence |
| --- | --- | --- |
| `exact_name_match` | Base score `0.74` | Source and target names match without regard to case. |
| `normalized_name_match` | Base score `0.64` | Names match after lowercasing and removing punctuation, spaces, and underscores. |
| `type_compatibility` | Add `0.15` when compatible, subtract `0.08` when incompatible | Declared source and target types. Integer to decimal is compatible. |
| `value_pattern_evidence` | Add `0.08` when at least half of non-empty values parse as the target type | Measured target-type support and sample count. |

Scores are capped at `0.99` and floored at `0.00`. These scores describe the strength of rule evidence; they are not probabilities and do not come from a trained model. A matching field name is required to propose a mapping in this version. Value patterns strengthen or weaken confidence evidence without silently mapping unrelated fields.

## Flag catalogue

| Rule | Meaning | Default severity |
| --- | --- | --- |
| `TYPE_CONFLICT` | Declared source and target types need coercion. | high |
| `MISSING_REQUIRED_TARGET_FIELD` | A required target field has no source mapping. A target default can still populate it during the fixture transform. | high |
| `DUPLICATE_KEY` | A source primary key repeats. Repeated rows are quarantined. | critical |
| `ORPHAN_REFERENCE` | A foreign key has no matching parent key. The row is quarantined. | high |
| `UNPARSEABLE_CURRENCY` | A mapped currency value cannot be parsed. The row is quarantined. | high |
| `UNPARSEABLE_DATE` | A mapped date value cannot be parsed. The row is quarantined. | high |
| `MIXED_DATE_FORMATS` | More than one supported valid date format occurs in one mapped date field. | medium |
| `LOSSY_TRANSFORM` | Currency rounding changes a value. The measured loss is reported. | medium |

Severity semantics: `critical` blocks safe loading until resolved or explicitly quarantined; `high` needs a documented correction, default, or quarantine decision; `medium` needs review and acceptance or reduction of measured loss; `low` is informational evidence for planning review. Flags are findings, not automated approvals.

## Determinism and scope

Profiles, mappings, plans, transform reports, flags, and validation reports contain no wall-clock timestamps. With the same fixture bytes and mapping, their JSON bytes are stable. Audit timestamps intentionally record when CLI commands ran. Transform execution is in memory; optional output files are generated local fixture artifacts only. There is no database adapter, network client, or live migration executor.\n