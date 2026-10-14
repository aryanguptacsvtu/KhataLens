import json
import sys
from datetime import date
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from khatalens import db
from khatalens.preprocess import preprocess
from khatalens.reminders import clean_phone, reminder_text, whatsapp_link
from khatalens.schema import (Entry, normalize_digits, parse_amount, parse_date,
                              validate_entries)


def test_devanagari_digits_and_amount_formats():
    assert normalize_digits("३४०") == "340"
    assert parse_amount("३४०/-") == 340
    assert parse_amount("₹1,250.50") == 1250.5
    assert parse_amount("100 रु") == 100
    assert parse_amount("abc") is None
    assert parse_amount(None) is None


def test_date_parsing():
    assert parse_date("04/10", 2026) == "2026-10-04"
    assert parse_date("०४/१०", 2026) == "2026-10-04"
    assert parse_date("04/10/2026") == "2026-10-04"
    assert parse_date("31/02", 2026) is None
    assert parse_date("") is None


def test_validation_flags_instead_of_dropping():
    raw = {"entries": [
        {"customer": "रमेश  वर्मा ", "amount": "३४०", "type": "उधार", "date": "04/10"},
        {"customer": "सीता", "amount": "??", "type": "जमा"},
        {"customer": "", "amount": 50, "type": "udhaar"},
        {"customer": "x", "amount": 5, "type": "weird"},
        "garbage",
    ]}
    rows = validate_entries(raw, default_year=2026)
    assert len(rows) == 4
    assert rows[0].customer == "रमेश वर्मा" and rows[0].amount == 340 and rows[0].type == "udhaar"
    assert rows[0].date == "2026-10-04" and rows[0].flags == []
    assert "unreadable amount" in rows[1].flags and rows[1].type == "payment"
    assert "missing name" in rows[2].flags
    assert "unknown type" in rows[3].flags


def test_validation_survives_bad_json():
    assert validate_entries("not json") == []
    assert validate_entries({"entries": "nope"}) == []
    assert len(validate_entries(json.dumps({"entries": [{"customer": "a", "amount": 1, "type": "payment"}]}))) == 1


def test_db_balances(tmp_path):
    p = tmp_path / "t.db"
    with db.connect(p) as c:
        db.add_entries(c, [
            Entry("रमेश", 500, "udhaar", "2026-08-01"),
            Entry("रमेश", 200, "payment", "2026-08-05"),
            Entry("सीता", 100, "udhaar", "2026-10-10"),
            Entry("गीता", 300, "udhaar", "2026-09-01"),
            Entry("गीता", 300, "payment", "2026-09-02"),   # settled -> must not appear
        ])
        b = db.balances(c)
        assert [(r["name"], r["balance"]) for r in b] == [("रमेश", 300), ("सीता", 100)]
        overdue = db.balances(c, overdue_days=30, today=date(2026, 10, 14))
        assert [r["name"] for r in overdue] == ["रमेश"]
        assert len(db.customer_history(c, "रमेश")) == 2


def test_reminders():
    assert clean_phone("98765 43210") == "919876543210"
    assert clean_phone("+91 98765-43210") == "919876543210"
    assert clean_phone("123") is None
    msg = reminder_text("रमेश", 1250, shop="वर्मा किराना")
    assert "₹1,250" in msg and "रमेश" in msg
    link = whatsapp_link("9876543210", msg)
    assert link.startswith("https://wa.me/919876543210?text=%E0%A4")
    assert whatsapp_link("bad", msg) is None


def test_preprocess_runs_and_limits_size():
    img = Image.new("RGB", (4000, 3000), (240, 235, 220))
    out = preprocess(img)
    assert max(out.size) <= 1600


def test_extract_entries_with_fake_ollama(monkeypatch):
    """No model needed: check we send image + JSON schema, and parse what comes back."""
    from khatalens import extract

    seen = {}

    def fake_chat(payload, timeout=600):
        seen.update(payload)
        body = {"entries": [{"date": "04/10", "customer": "रमेश वर्मा", "items": "चावल",
                             "amount": 340, "type": "udhaar"}]}
        return {"message": {"content": json.dumps(body, ensure_ascii=False)}}

    monkeypatch.setattr(extract, "_chat", fake_chat)
    entries, raw = extract.extract_entries(Image.new("RGB", (800, 1000), (240, 235, 220)),
                                           model="test-model", default_year=2026)
    assert seen["model"] == "test-model"
    assert seen["format"]["required"] == ["entries"]
    assert seen["options"]["temperature"] == 0
    assert len(seen["messages"][0]["images"]) == 1
    assert entries[0].date == "2026-10-04" and entries[0].amount == 340


def test_extract_reports_unreachable_ollama(monkeypatch):
    import pytest
    from khatalens import extract
    monkeypatch.setattr(extract, "OLLAMA_HOST", "http://127.0.0.1:9")  # nothing listens here
    with pytest.raises(extract.ExtractionError, match="ollama"):
        extract.extract_entries(Image.new("RGB", (100, 100)))


def test_scorer_penalises_missing_rows():
    from scripts.evaluate import score_page
    truth = validate_entries({"entries": [
        {"customer": "a", "amount": 1, "type": "udhaar", "date": "01/01"},
        {"customer": "b", "amount": 2, "type": "payment", "date": "02/01"}]}, 2026)
    pred = truth[:1]  # model dropped the second line
    s = score_page(pred, truth)
    assert s["amount"] == (1, 2) and s["customer"] == (1, 2)
