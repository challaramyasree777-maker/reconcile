from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./reconcile.db")
DB_PATH = Path(DATABASE_URL.removeprefix("sqlite:///"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS vendor_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS human_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL,
    original_category TEXT,
    corrected_category TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(transaction_id) REFERENCES transactions(id)
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    transaction_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL UNIQUE,
    raw_payload TEXT NOT NULL,
    parsed_amount REAL,
    parsed_date TEXT,
    parsed_vendor_raw TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(id)
);
CREATE TABLE IF NOT EXISTS ledger_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL UNIQUE,
    external_ledger_id TEXT NOT NULL,
    amount REAL NOT NULL,
    vendor TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(transaction_id) REFERENCES transactions(id)
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER,
    event_type TEXT NOT NULL,
    detail_text TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(UTC).isoformat()


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connection() as conn:
        conn.executescript(SCHEMA)


def add_audit(transaction_id: int | None, event_type: str, detail: Any, actor: str = "agent") -> None:
    detail_text = detail if isinstance(detail, str) else json.dumps(detail, default=str)
    with connection() as conn:
        conn.execute(
            "INSERT INTO audit_log(transaction_id, event_type, detail_text, actor, created_at) VALUES (?, ?, ?, ?, ?)",
            (transaction_id, event_type, detail_text, actor, now()),
        )


def rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connection() as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def one(query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    result = rows(query, params)
    return result[0] if result else None
