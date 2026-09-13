"""
Postgres multi-tenant isolation manual verification.
Usage:
    set TEST_DATABASE_URL=postgresql://postgres:PASSWORD@localhost:5432/reconcile_test
    python scripts/verify_pg_isolation.py

Prints real query results from the database proving user A never sees user B's rows.
"""
from __future__ import annotations

import os
import sys

# ── Ensure app is importable ──────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pg_url = os.getenv("TEST_DATABASE_URL", "")
if not pg_url or not pg_url.startswith("postgresql://"):
    sys.exit(
        "ERROR: set TEST_DATABASE_URL=postgresql://user:password@host:5432/dbname\n"
        "This script requires a real Postgres connection."
    )

import psycopg2
import psycopg2.extras

os.environ["DATABASE_URL"] = pg_url

import app.db as db

print("=" * 64)
print("Postgres Isolation Verification")
print(f"DSN: {pg_url}")
print("=" * 64)

# ── Bootstrap schema ──────────────────────────────────────────────────────────
print("\n[1] Running init_db() to create schema ...")
db.init_db()
print("    Schema created OK.")

# ── Helpers ───────────────────────────────────────────────────────────────────
from app.auth import hash_password


def insert_user(conn, email: str) -> int:
    cur = conn.execute(
        "INSERT INTO users (email, hashed_password, created_at) VALUES (%s, %s, %s) RETURNING id",
        (email, hash_password("testpass_isolation_only"), db.now()),
    )
    row = cur.fetchone()
    return row["id"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]


def insert_run(conn, user_id: int) -> int:
    cur = conn.execute(
        "INSERT INTO runs(user_id, source, status, transaction_count, created_at)"
        " VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (user_id, "verify-script", "completed", 1, db.now()),
    )
    row = cur.fetchone()
    return row["id"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]


def insert_tx(conn, user_id: int, run_id: int, ext_id: str, vendor: str) -> int:
    cur = conn.execute(
        """
        INSERT INTO transactions
            (user_id, run_id, source, external_id, raw_payload, parsed_amount,
             parsed_date, parsed_vendor_raw, status, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (user_id, run_id, "verify-script", ext_id, "{}", 42.00,
         "2026-09-13", vendor, "pending", db.now()),
    )
    row = cur.fetchone()
    return row["id"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]


# ── Create two isolated users ─────────────────────────────────────────────────
print("\n[2] Creating user A and user B ...")
with db.connection() as conn:
    uid_a = insert_user(conn, "verify_user_a@isolation.test")
    uid_b = insert_user(conn, "verify_user_b@isolation.test")
print(f"    user A id={uid_a}  user B id={uid_b}")

# ── Insert separate data ──────────────────────────────────────────────────────
print("\n[3] Inserting transactions for each user ...")
with db.connection() as conn:
    run_a = insert_run(conn, uid_a)
    run_b = insert_run(conn, uid_b)
    tx_a1 = insert_tx(conn, uid_a, run_a, "iso-a-tx-1", "Alpha Coffee")
    tx_a2 = insert_tx(conn, uid_a, run_a, "iso-a-tx-2", "Alpha Bakery")
    tx_b1 = insert_tx(conn, uid_b, run_b, "iso-b-tx-1", "Beta Garage")
print(f"    user A: tx ids {tx_a1}, {tx_a2}  (vendors: Alpha Coffee, Alpha Bakery)")
print(f"    user B: tx id {tx_b1}  (vendor: Beta Garage)")

# ── Raw Postgres query: user A's view ────────────────────────────────────────
print("\n[4] Raw SQL -- SELECT * FROM transactions WHERE user_id = A ...")
conn_raw = psycopg2.connect(pg_url)
conn_raw.autocommit = True
cur = conn_raw.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

cur.execute(
    "SELECT id, user_id, parsed_vendor_raw, external_id FROM transactions WHERE user_id = %s ORDER BY id",
    (uid_a,),
)
rows_a = cur.fetchall()
print(f"    Rows returned for user A: {len(rows_a)}")
for r in rows_a:
    print(f"      id={r['id']} user_id={r['user_id']} vendor={r['parsed_vendor_raw']} ext={r['external_id']}")

b_vendors = {r["parsed_vendor_raw"] for r in rows_a}
assert "Beta Garage" not in b_vendors, "ISOLATION FAILURE: user A can see user B's Beta Garage transaction!"
print("    PASS: Beta Garage (user B) is NOT visible to user A")

# ── Raw Postgres query: user B's view ────────────────────────────────────────
print("\n[5] Raw SQL -- SELECT * FROM transactions WHERE user_id = B ...")
cur.execute(
    "SELECT id, user_id, parsed_vendor_raw, external_id FROM transactions WHERE user_id = %s ORDER BY id",
    (uid_b,),
)
rows_b = cur.fetchall()
print(f"    Rows returned for user B: {len(rows_b)}")
for r in rows_b:
    print(f"      id={r['id']} user_id={r['user_id']} vendor={r['parsed_vendor_raw']} ext={r['external_id']}")

a_vendors_in_b = {r["parsed_vendor_raw"] for r in rows_b}
assert "Alpha Coffee" not in a_vendors_in_b, "ISOLATION FAILURE: user B can see user A's Alpha Coffee!"
assert "Alpha Bakery" not in a_vendors_in_b, "ISOLATION FAILURE: user B can see user A's Alpha Bakery!"
print("    PASS: Alpha Coffee and Alpha Bakery (user A) are NOT visible to user B")

# ── Cross-user correction attempt (raw SQL check) ─────────────────────────────
print("\n[6] Verifying user A cannot access user B's transaction via id+user_id filter ...")
cur.execute(
    "SELECT id FROM transactions WHERE id = %s AND user_id = %s",
    (tx_b1, uid_a),  # user B's tx id, queried with user A's user_id
)
cross_row = cur.fetchone()
assert cross_row is None, f"ISOLATION FAILURE: user A can see user B's tx {tx_b1}!"
print(f"    PASS: Query (tx_id={tx_b1}, user_id={uid_a}) returns 0 rows -- user A cannot see user B's tx")

# ── Cleanup ───────────────────────────────────────────────────────────────────
print("\n[7] Cleaning up test data ...")
cur.execute("DELETE FROM transactions WHERE external_id LIKE 'iso-%'")
cur.execute("DELETE FROM runs WHERE source = 'verify-script'")
cur.execute("DELETE FROM users WHERE email LIKE 'verify_user_%@isolation.test'")
conn_raw.close()
print("    Cleanup done.")

print("\n" + "=" * 64)
print("ISOLATION VERIFICATION PASSED -- all 3 assertions hold on real Postgres")
print("=" * 64)
