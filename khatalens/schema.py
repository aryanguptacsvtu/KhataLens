"""Ledger entry schema, JSON schema for constrained decoding, and validation.

The model is asked to return one JSON object per page:
    {"entries": [{"date", "customer", "items", "amount", "type"}, ...]}
Everything the model returns is untrusted: we normalise and validate it here
before anything reaches the database.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import Any, Optional

ENTRY_TYPES = ("udhaar", "payment")

# JSON schema handed to Ollama's `format` parameter (structured outputs),
# so the model can only produce well-formed JSON of this shape.
LEDGER_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "date": {"type": "string"},
                    "customer": {"type": "string"},
                    "items": {"type": "string"},
                    "amount": {"type": "number"},
                    "type": {"type": "string", "enum": list(ENTRY_TYPES)},
                },
                "required": ["customer", "amount", "type"],
            },
        }
    },
    "required": ["entries"],
}

# Devanagari digits -> ASCII, so "३४०" becomes "340".
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def normalize_digits(text: str) -> str:
    return text.translate(_DEVANAGARI_DIGITS)


def parse_amount(value: Any) -> Optional[float]:
    """Accept 340, '340', '₹340', '३४०/-', '1,250.50'. Return None if unusable."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value == value else None  # NaN guard
    s = normalize_digits(str(value))
    s = s.replace(",", "").replace("₹", "").replace("Rs.", "").replace("Rs", "")
    s = s.replace("/-", "").strip()
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y", "%d.%m.%Y")


def parse_date(value: Any, default_year: Optional[int] = None) -> Optional[str]:
    """Return ISO date string or None. Handles '4/10' using default_year."""
    if not value:
        return None
    s = normalize_digits(str(value)).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    m = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})", s)  # day/month, no year
    if m:
        year = default_year or date.today().year
        try:
            return date(year, int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            return None
    return None


def normalize_type(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip().lower()
    udhaar = {"udhaar", "udhar", "उधार", "उधारी", "credit", "debit", "baki", "बाकी", "नाम", "naam"}
    payment = {"payment", "jama", "जमा", "paid", "received", "cash", "वापसी"}
    if s in udhaar:
        return "udhaar"
    if s in payment:
        return "payment"
    return None


def normalize_name(name: str) -> str:
    """Collapse whitespace so 'रमेश  वर्मा ' and 'रमेश वर्मा' match."""
    return re.sub(r"\s+", " ", name or "").strip()


@dataclass
class Entry:
    customer: str
    amount: float
    type: str
    date: Optional[str] = None
    items: str = ""
    flags: list[str] = field(default_factory=list)  # reasons a human should check this row

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_entries(raw: Any, default_year: Optional[int] = None) -> list[Entry]:
    """Turn untrusted model output (dict, JSON string, or list) into clean Entries.

    Rows are never silently dropped if they have a customer or amount; instead
    they get `flags` so the review UI can highlight them in yellow.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    rows = raw.get("entries", []) if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        return []

    out: list[Entry] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        flags: list[str] = []
        customer = normalize_name(str(row.get("customer", "")))
        amount = parse_amount(row.get("amount"))
        etype = normalize_type(row.get("type"))
        iso = parse_date(row.get("date"), default_year)

        if not customer:
            flags.append("missing name")
        if amount is None:
            flags.append("unreadable amount")
            amount = 0.0
        elif amount <= 0:
            flags.append("amount not positive")
        elif amount > 100_000:
            flags.append("unusually large amount")
        if etype is None:
            flags.append("unknown type")
            etype = "udhaar"
        if row.get("date") and iso is None:
            flags.append("unreadable date")
        if not customer and amount == 0.0:
            continue  # pure noise row

        out.append(Entry(customer=customer, amount=abs(amount), type=etype,
                         date=iso, items=str(row.get("items", "") or "").strip(),
                         flags=flags))
    return out
