"""Deterministic parsing and type evidence used by inspection and transforms."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

DATE_FORMATS = (
    ("iso", "%Y-%m-%d"),
    ("us_slash", "%m/%d/%Y"),
    ("year_slash", "%Y/%m/%d"),
)
CENT = Decimal("0.01")


def parse_date(value: str) -> tuple[str, str]:
    for name, pattern in DATE_FORMATS:
        try:
            parsed = datetime.strptime(value.strip(), pattern).date()
            return parsed.isoformat(), name
        except ValueError:
            continue
    raise ValueError("unparseable date")


def parse_currency(value: str) -> Decimal:
    cleaned = value.strip().replace(",", "")
    cleaned = re.sub(r"^(?:USD|EUR|GBP)\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace("$", "").replace("€", "").replace("£", "").strip()
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError("unparseable currency") from exc
    if not amount.is_finite():
        raise ValueError("unparseable currency")
    return amount


def quantize_currency(value: Decimal) -> tuple[Decimal, Decimal]:
    rounded = value.quantize(CENT, rounding=ROUND_HALF_UP)
    return rounded, abs(value - rounded)


def normalize_type(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def type_compatible(source_type: str, target_type: str) -> bool:
    source_type = normalize_type(source_type)
    target_type = normalize_type(target_type)
    aliases = {
        "int": "integer",
        "number": "decimal",
        "numeric": "decimal",
        "float": "decimal",
        "money": "decimal",
        "varchar": "string",
        "text": "string",
        "datetime": "date",
        "timestamp": "date",
    }
    source_type = aliases.get(source_type, source_type)
    target_type = aliases.get(target_type, target_type)
    return source_type == target_type or (source_type == "integer" and target_type == "decimal")


def infer_type(values: list[str]) -> str:
    non_null = [value.strip() for value in values if value is not None and value.strip()]
    if not non_null:
        return "unknown"
    lowered = {value.lower() for value in non_null}
    if lowered <= {"true", "false", "yes", "no", "0", "1"}:
        return "boolean"
    try:
        for value in non_null:
            int(value)
        return "integer"
    except ValueError:
        pass
    try:
        for value in non_null:
            Decimal(value)
        return "decimal"
    except InvalidOperation:
        pass
    date_formats: set[str] = set()
    for value in non_null:
        try:
            _, date_format = parse_date(value)
            date_formats.add(date_format)
        except ValueError:
            break
    else:
        return "date" if len(date_formats) == 1 else "mixed_date"
    currency_parseable = 0
    for value in non_null:
        try:
            parse_currency(value)
            currency_parseable += 1
        except ValueError:
            continue
    if currency_parseable == len(non_null):
        return "currency"
    if currency_parseable / len(non_null) >= 0.7:
        return "mixed_currency"
    return "string"


def pattern_support(values: list[str], target_type: str) -> float:
    present = [value for value in values if value is not None and value.strip()]
    if not present:
        return 0.0
    target_type = normalize_type(target_type)
    okay = 0
    for value in present:
        try:
            if target_type in {"date", "datetime", "timestamp"}:
                parse_date(value)
            elif target_type in {"decimal", "numeric", "number", "money"}:
                parse_currency(value)
            elif target_type in {"integer", "int"}:
                int(value)
            else:
                okay += 1
                continue
            okay += 1
        except (ValueError, InvalidOperation):
            continue
    return okay / len(present)
