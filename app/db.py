from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()


def get_database_url() -> str:
    return os.getenv("DATABASE_URL", "sqlite:///./reconcile.db")


POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    hashed_password TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    otp_hash TEXT,
    otp_expires_at TEXT,
    google_id TEXT UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    transaction_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    run_id INTEGER NOT NULL REFERENCES runs(id),
    source TEXT NOT NULL,
    external_id TEXT NOT NULL UNIQUE,
    raw_payload TEXT NOT NULL,
    parsed_amount REAL,
    parsed_date TEXT,
    parsed_vendor_raw TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS match_decisions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    transaction_id INTEGER NOT NULL REFERENCES transactions(id),
    proposed_vendor TEXT NOT NULL,
    category TEXT NOT NULL,
    confidence_score REAL NOT NULL,
    reasoning TEXT NOT NULL,
    action TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vendor_rules_memory (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    vendor_raw_pattern TEXT NOT NULL,
    canonical_vendor TEXT NOT NULL,
    category TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CONSTRAINT uq_user_vendor_pattern UNIQUE (user_id, vendor_raw_pattern)
);

CREATE TABLE IF NOT EXISTS human_corrections (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    transaction_id INTEGER NOT NULL REFERENCES transactions(id),
    original_category TEXT,
    corrected_vendor TEXT,
    corrected_category TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger_entries (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    transaction_id INTEGER NOT NULL UNIQUE REFERENCES transactions(id),
    external_ledger_id TEXT NOT NULL,
    amount REAL NOT NULL,
    vendor TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    transaction_id INTEGER,
    event_type TEXT NOT NULL,
    detail_text TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    hashed_password TEXT,
    is_active INTEGER DEFAULT 1,
    otp_hash TEXT,
    otp_expires_at TEXT,
    google_id TEXT UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    transaction_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    run_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL UNIQUE,
    raw_payload TEXT NOT NULL,
    parsed_amount REAL,
    parsed_date TEXT,
    parsed_vendor_raw TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS match_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    transaction_id INTEGER NOT NULL,
    proposed_vendor TEXT NOT NULL,
    category TEXT NOT NULL,
    confidence_score REAL NOT NULL,
    reasoning TEXT NOT NULL,
    action TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(transaction_id) REFERENCES transactions(id)
);

CREATE TABLE IF NOT EXISTS vendor_rules_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    vendor_raw_pattern TEXT NOT NULL,
    canonical_vendor TEXT NOT NULL,
    category TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, vendor_raw_pattern),
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS human_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    transaction_id INTEGER NOT NULL,
    original_category TEXT,
    corrected_vendor TEXT,
    corrected_category TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(transaction_id) REFERENCES transactions(id)
);

CREATE TABLE IF NOT EXISTS ledger_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    transaction_id INTEGER NOT NULL UNIQUE,
    external_ledger_id TEXT NOT NULL,
    amount REAL NOT NULL,
    vendor TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(transaction_id) REFERENCES transactions(id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    transaction_id INTEGER,
    event_type TEXT NOT NULL,
    detail_text TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);
"""


def now() -> str:
    return datetime.now(UTC).isoformat()


def is_postgres() -> bool:
    db_url = get_database_url()
    return db_url.startswith("postgres://") or db_url.startswith("postgresql://")


def startup_checks() -> None:
    """Validate that required environment variables are set without fallback."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is required")

    secret_key = os.getenv("SECRET_KEY")
    if not secret_key:
        raise RuntimeError("SECRET_KEY environment variable is required and must not be empty")

    groq_api_key = os.getenv("GROQ_API_KEY")
    if not groq_api_key:
        raise RuntimeError("GROQ_API_KEY environment variable is required and must not be empty")


class SQLiteConnectionWrapper:
    """Wraps sqlite3.Connection to provide %s parameter style and DictCursor compatibility."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def __enter__(self) -> SQLiteConnectionWrapper:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.conn.commit()
        self.conn.close()

    def execute(self, query: str, params: tuple[Any, ...] | list[Any] = ()) -> sqlite3.Cursor:
        # Translate %s placeholder to ? for sqlite
        sqlite_query = query.replace("%s", "?")
        # Unescape psycopg2 %% literal escapes back to % for SQLite
        sqlite_query = sqlite_query.replace("%%", "%")
        return self.conn.execute(sqlite_query, tuple(params))

    def executescript(self, script: str) -> None:
        self.conn.executescript(script)

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


class PostgresConnectionWrapper:
    """Wraps psycopg2 connection with RealDictCursor."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def __enter__(self) -> PostgresConnectionWrapper:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.conn.commit()
        else:
            self.conn.rollback()
        self.conn.close()

    def execute(self, query: str, params: tuple[Any, ...] | list[Any] = ()) -> Any:
        cur = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(query, tuple(params))
        return cur

    def executescript(self, script: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(script)
        self.conn.commit()

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


@contextmanager
def connection() -> Iterator[Any]:
    db_url = get_database_url()
    if is_postgres():
        conn = psycopg2.connect(db_url)
        wrapper = PostgresConnectionWrapper(conn)
        try:
            yield wrapper
            wrapper.commit()
        finally:
            wrapper.close()
    else:
        db_path_str = db_url.removeprefix("sqlite:///")
        db_path = Path(db_path_str)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        wrapper = SQLiteConnectionWrapper(conn)
        try:
            yield wrapper
            wrapper.commit()
        finally:
            wrapper.close()


def seed_admin_user() -> None:
    """Seed the default administrator user from environment variables if not present.

    Password resolution order:
      1. ADMIN_PASSWORD env var  — use as-is if non-empty.
      2. Not set / empty         — generate a cryptographically random password,
                                   print it ONCE to stdout, and continue.

    The generated password is never stored in source code or config files.
    Rotate it immediately after first login via your user-management flow.
    """
    import secrets
    import string

    from app.auth import hash_password

    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com").strip()
    if not admin_email:
        return

    admin_password = os.getenv("ADMIN_PASSWORD", "").strip()
    generated = False
    if not admin_password:
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        admin_password = "".join(secrets.choice(alphabet) for _ in range(24))
        generated = True

    existing = one("SELECT id FROM users WHERE email = %s", (admin_email,))
    if not existing:
        hashed = hash_password(admin_password)
        with connection() as conn:
            conn.execute(
                "INSERT INTO users (email, hashed_password, created_at) VALUES (%s, %s, %s)",
                (admin_email, hashed, now()),
            )
        if generated:
            # Print once to stdout so the operator can capture it from the server log.
            # It is NOT stored anywhere else.
            print(
                f"\n{'='*60}\n"
                f"RECONCILE — ADMIN ACCOUNT CREATED\n"
                f"  Email:    {admin_email}\n"
                f"  Password: {admin_password}\n"
                f"  ⚠  This password will NOT be shown again.\n"
                f"  Set ADMIN_PASSWORD in .env to pin a specific value,\n"
                f"  or rotate the password immediately after first login.\n"
                f"{'='*60}\n",
                flush=True,
            )


def init_db() -> None:
    with connection() as conn:
        if is_postgres():
            conn.executescript(POSTGRES_SCHEMA)
            try:
                conn.executescript("""
                ALTER TABLE users ADD COLUMN is_active BOOLEAN DEFAULT TRUE;
                ALTER TABLE users ADD COLUMN otp_hash TEXT;
                ALTER TABLE users ADD COLUMN otp_expires_at TEXT;
                ALTER TABLE users ADD COLUMN google_id TEXT UNIQUE;
                ALTER TABLE users ALTER COLUMN hashed_password DROP NOT NULL;
                """)
            except Exception:
                pass
        else:
            conn.executescript(SQLITE_SCHEMA)
            try:
                conn.executescript("""
                ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1;
                ALTER TABLE users ADD COLUMN otp_hash TEXT;
                ALTER TABLE users ADD COLUMN otp_expires_at TEXT;
                ALTER TABLE users ADD COLUMN google_id TEXT UNIQUE;
                """)
            except Exception:
                pass
    seed_admin_user()


def add_audit(
    transaction_id: int | None,
    event_type: str,
    detail: Any,
    actor: str = "agent",
    user_id: int | None = None,
) -> None:
    detail_text = detail if isinstance(detail, str) else json.dumps(detail, default=str)
    with connection() as conn:
        conn.execute(
            "INSERT INTO audit_log(user_id, transaction_id, event_type, detail_text, actor, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
            (user_id, transaction_id, event_type, detail_text, actor, now()),
        )


def rows(query: str, params: tuple[Any, ...] | list[Any] = ()) -> list[dict[str, Any]]:
    with connection() as conn:
        cursor = conn.execute(query, params)
        fetched = cursor.fetchall()
        return [dict(row) for row in fetched]


def one(query: str, params: tuple[Any, ...] | list[Any] = ()) -> dict[str, Any] | None:
    result = rows(query, params)
    return result[0] if result else None


# ---------------------------------------------------------------------------
# Demo reset — clears all rows, resets auto-increment counters.
# ---------------------------------------------------------------------------

_DEMO_TABLES = [
    "match_decisions",
    "ledger_entries",
    "human_corrections",
    "audit_log",
    "transactions",
    "vendor_rules_memory",
    "runs",
]


def reset_demo_data(user_id: int | None = None) -> dict[str, int]:
    """Delete every row from every demo table and reset auto-increment counters."""
    deleted: dict[str, int] = {}
    with connection() as conn:
        if is_postgres():
            if user_id is None:
                for table in _DEMO_TABLES:
                    cursor = conn.execute(f"SELECT COUNT(*) AS c FROM {table}")
                    cnt = cursor.fetchone()["c"]
                    deleted[table] = cnt
                conn.execute(
                    "TRUNCATE TABLE match_decisions, ledger_entries, human_corrections, audit_log, transactions, vendor_rules_memory, runs RESTART IDENTITY CASCADE"
                )
            else:
                for table in _DEMO_TABLES:
                    cursor = conn.execute(f"DELETE FROM {table} WHERE user_id = %s", (user_id,))
                    deleted[table] = cursor.rowcount or 0
        else:
            if user_id is None:
                for table in _DEMO_TABLES:
                    cursor = conn.execute(f"DELETE FROM {table}")
                    deleted[table] = cursor.rowcount
                # Reset SQLite sequences if sequence table exists
                seq_table_exists = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
                ).fetchone()
                if seq_table_exists:
                    for table in _DEMO_TABLES:
                        conn.execute("DELETE FROM sqlite_sequence WHERE name = %s", (table,))
            else:
                for table in _DEMO_TABLES:
                    cursor = conn.execute(f"DELETE FROM {table} WHERE user_id = %s", (user_id,))
                    deleted[table] = cursor.rowcount

    return deleted
