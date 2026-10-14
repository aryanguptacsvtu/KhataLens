"""SQLite ledger. Everything stays in one local file; nothing is uploaded anywhere."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Iterator

from .schema import Entry

DEFAULT_DB = Path("khatalens.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    phone TEXT
);
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    date TEXT,
    items TEXT DEFAULT '',
    amount REAL NOT NULL CHECK (amount >= 0),
    type TEXT NOT NULL CHECK (type IN ('udhaar','payment')),
    source TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_entries_customer ON entries(customer_id);
"""


@contextmanager
def connect(path: Path | str = DEFAULT_DB) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _customer_id(conn: sqlite3.Connection, name: str) -> int:
    conn.execute("INSERT OR IGNORE INTO customers(name) VALUES (?)", (name,))
    return conn.execute("SELECT id FROM customers WHERE name = ?", (name,)).fetchone()["id"]


def add_entries(conn: sqlite3.Connection, entries: Iterable[Entry], source: str = "") -> int:
    n = 0
    for e in entries:
        if not e.customer:
            continue
        cid = _customer_id(conn, e.customer)
        conn.execute(
            "INSERT INTO entries(customer_id, date, items, amount, type, source) VALUES (?,?,?,?,?,?)",
            (cid, e.date, e.items, e.amount, e.type, source),
        )
        n += 1
    return n


def set_phone(conn: sqlite3.Connection, name: str, phone: str) -> None:
    cid = _customer_id(conn, name)
    conn.execute("UPDATE customers SET phone = ? WHERE id = ?", (phone, cid))


def _days_since(last: str | None, today: date) -> int | None:
    try:
        return (today - date.fromisoformat(last)).days if last else None
    except ValueError:
        return None


def balances(conn: sqlite3.Connection, overdue_days: int | None = None,
             today: date | None = None, status: str = "pending"):
    """Per-customer balance = udhaar - payments.

    status: "pending" (balance > 0, largest first), "settled" (exactly zero),
            "check" (balance < 0, i.e. paid more than the ledger shows owed).
    overdue_days: only for pending - keep customers whose last entry is at least
    this many days old.
    Each row also has days_since (days since the last entry, or None).
    """
    having = {"pending": "balance > 0.005",
              "settled": "ABS(balance) <= 0.005",
              "check": "balance < -0.005"}[status]
    order = "balance DESC" if status == "pending" else ("balance ASC" if status == "check" else "c.name")
    rows = conn.execute(
        f"""
        SELECT c.name, c.phone,
               SUM(CASE WHEN e.type='udhaar'  THEN e.amount ELSE 0 END) AS total_udhaar,
               SUM(CASE WHEN e.type='payment' THEN e.amount ELSE 0 END) AS total_paid,
               SUM(CASE WHEN e.type='udhaar'  THEN e.amount ELSE -e.amount END) AS balance,
               MAX(e.date) AS last_activity
        FROM customers c JOIN entries e ON e.customer_id = c.id
        GROUP BY c.id
        HAVING {having}
        ORDER BY {order}
        """
    ).fetchall()
    today = today or date.today()
    result = [dict(r) for r in rows]
    for r in result:
        r["days_since"] = _days_since(r["last_activity"], today)
    if overdue_days is not None:
        result = [r for r in result if r["days_since"] is not None and r["days_since"] >= overdue_days]
    return result


def record_payment(conn: sqlite3.Connection, name: str, amount: float,
                   when: date | None = None) -> None:
    """Add a payment entry for a customer (full or partial)."""
    cid = _customer_id(conn, name)
    conn.execute(
        "INSERT INTO entries(customer_id, date, items, amount, type, source) VALUES (?,?,?,?,?,?)",
        (cid, (when or date.today()).isoformat(), "", float(amount), "payment", "manual"),
    )


def customer_history(conn: sqlite3.Connection, name: str):
    rows = conn.execute(
        """SELECT e.date, e.items, e.amount, e.type FROM entries e
           JOIN customers c ON c.id = e.customer_id
           WHERE c.name = ? ORDER BY e.date, e.id""",
        (name,),
    ).fetchall()
    return [dict(r) for r in rows]