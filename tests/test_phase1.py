from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import app.db as db

    test_db_url = os.getenv("TEST_DATABASE_URL")
    if test_db_url:
        monkeypatch.setenv("DATABASE_URL", test_db_url)
        with db.connection() as conn:
            if db.is_postgres():
                conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    else:
        test_file = tmp_path / "test.db"
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{test_file}")

    db.init_db()
    return db


@pytest.fixture
def test_user(isolated_db):
    user = isolated_db.one("SELECT id, email FROM users WHERE email = %s", ("admin@example.com",))
    if not user:
        from app.auth import hash_password

        with isolated_db.connection() as conn:
            cur = conn.execute(
                "INSERT INTO users (email, hashed_password, created_at) VALUES (%s, %s, %s) RETURNING id",
                ("test@example.com", hash_password("testpass123"), isolated_db.now()),
            )
            row = cur.fetchone()
            uid = row["id"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]
            user = {"id": uid, "email": "test@example.com"}
    return user


@pytest.fixture
def fake_plaid(monkeypatch):
    from app.services import ingestion

    transactions = [
        {
            "transaction_id": "test-tx-1",
            "amount": 25.00,
            "date": "2026-09-01",
            "merchant_name": "Test Coffee",
            "name": "Test Coffee",
        },
        {
            "transaction_id": "test-tx-2",
            "amount": 50.00,
            "date": "2026-09-02",
            "merchant_name": "Test Store",
            "name": "Test Store",
        },
        {
            "transaction_id": "test-tx-3",
            "amount": 75.00,
            "date": "2026-09-03",
            "merchant_name": "Test Travel",
            "name": "Test Travel",
        },
    ]

    monkeypatch.setattr(
        ingestion,
        "pull_plaid_transactions",
        lambda: {"added": transactions, "next_cursor": "test-cursor"},
    )


def test_demo_run_ingests_and_syncs(isolated_db, test_user, fake_plaid):
    from app.services.ingestion import create_run
    from app.services.ledger import sync_pending_transactions

    run = create_run(user_id=test_user["id"])
    sync = sync_pending_transactions(user_id=test_user["id"])

    assert run["inserted"] == 3
    assert sync["posted"] == 3
    assert isolated_db.rows("SELECT status FROM transactions WHERE user_id = %s", (test_user["id"],)) == [
        {"status": "posted"}
    ] * 3
    assert isolated_db.one("SELECT COUNT(*) AS count FROM audit_log WHERE user_id = %s", (test_user["id"],))["count"] == 7


def test_second_run_skips_duplicates(isolated_db, test_user, fake_plaid):
    from app.services.ingestion import create_run

    first = create_run(user_id=test_user["id"])
    second = create_run(user_id=test_user["id"])

    assert first["inserted"] == 3
    assert second["inserted"] == 0
    assert second["duplicates"] == 3


def test_malformed_input_file(isolated_db, test_user, monkeypatch):
    """Test that malformed transactions are flagged, not crashed."""
    from app.services import ingestion

    # Malformed transactions: missing required fields
    malformed_transactions = [
        {
            "transaction_id": "malformed-1",
            "amount": 25.00,
            # Missing: date, merchant_name
        },
        {
            "transaction_id": "malformed-2",
            "date": "2026-09-02",
            # Missing: amount, merchant_name
        },
        {
            "transaction_id": "good-3",
            "amount": 75.00,
            "date": "2026-09-03",
            "merchant_name": "Test Travel",
            "name": "Test Travel",
        },
    ]

    monkeypatch.setattr(
        ingestion,
        "pull_plaid_transactions",
        lambda: {"added": malformed_transactions, "next_cursor": "test-cursor"},
    )

    run = ingestion.create_run(user_id=test_user["id"])

    # Should have ingested 1 good transaction
    assert run["inserted"] == 1
    # Should have 2 parse errors
    assert run["parse_errors"] == 2
    # Status should indicate parse errors
    assert "parse_error" in run["status"].lower()

    # Verify flagged transactions exist in DB
    flagged = isolated_db.rows(
        "SELECT id, external_id, status FROM transactions WHERE user_id = %s AND status = 'flagged'",
        (test_user["id"],),
    )
    assert len(flagged) == 2
    assert flagged[0]["external_id"] == "malformed-1"
    assert flagged[1]["external_id"] == "malformed-2"

    # Verify audit log has parse_error entries
    parse_error_logs = isolated_db.rows(
        "SELECT * FROM audit_log WHERE user_id = %s AND event_type = 'parse_error'",
        (test_user["id"],),
    )
    assert len(parse_error_logs) == 2
    # Verify error message is in detail
    for log in parse_error_logs:
        assert "Missing required fields" in log["detail_text"]


def test_duplicate_detection_in_workflow(isolated_db, test_user, monkeypatch, fake_plaid):
    """Test that high-confidence decisions are auto-posted (baseline), and duplicates escalate."""
    from app.services.ingestion import create_run
    from app.services.workflow import evaluate_node

    # Create first run (inserts 3 transactions)
    run = create_run(user_id=test_user["id"])
    assert run["inserted"] == 3

    # Get first transaction
    transactions = isolated_db.rows(
        "SELECT * FROM transactions WHERE user_id = %s AND status = 'pending'",
        (test_user["id"],),
    )
    assert len(transactions) == 3
    first_tx = transactions[0]

    # Test 1: High confidence (≥0.85) should auto_post
    state = {
        "transaction": first_tx,
        "memory": [],
        "decision": {
            "proposed_vendor": "Test Vendor",
            "category": "Test",
            "confidence_score": 0.95,  # High confidence
            "reasoning": "Clear match",
        },
    }
    result = evaluate_node(state)
    assert result["decision"]["action"] == "auto_post"

    # Test 2: Now insert a second transaction with same parsed_amount + parsed_date + parsed_vendor_raw
    # as first_tx to simulate duplicate, then test that evaluate_node escalates it
    second_tx = {
        "user_id": test_user["id"],
        "run_id": run["run_id"],
        "source": "plaid",
        "external_id": "dup-external-id",
        "raw_payload": "{}",
        "parsed_amount": first_tx["parsed_amount"],
        "parsed_date": first_tx["parsed_date"],
        "parsed_vendor_raw": first_tx["parsed_vendor_raw"],
        "status": "pending",
    }

    with isolated_db.connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO transactions (user_id, run_id, source, external_id, raw_payload, parsed_amount, parsed_date, parsed_vendor_raw, status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                second_tx["user_id"],
                second_tx["run_id"],
                second_tx["source"],
                second_tx["external_id"],
                second_tx["raw_payload"],
                second_tx["parsed_amount"],
                second_tx["parsed_date"],
                second_tx["parsed_vendor_raw"],
                second_tx["status"],
                isolated_db.now(),
            ),
        )
        row = cur.fetchone()
        second_tx["id"] = row["id"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]

    # Test that evaluate_node detects duplicate and escalates
    state2 = {
        "transaction": dict(second_tx),
        "memory": [],
        "decision": {
            "proposed_vendor": "Same Vendor",
            "category": "Test",
            "confidence_score": 0.95,  # High confidence, but duplicate
            "reasoning": "Clear match",
        },
    }
    result2 = evaluate_node(state2)
    assert result2["decision"]["action"] == "escalate"
    assert "Duplicate of transaction" in result2["decision"].get("escalation_reason", "")


def test_groq_api_failure_gracefully_escalates(isolated_db, test_user, monkeypatch, fake_plaid):
    """Test that Groq API failures (error field in decision) force escalation."""
    from app.services.workflow import evaluate_node

    with isolated_db.connection() as conn:
        run_cur = conn.execute(
            "INSERT INTO runs (user_id, source, status, transaction_count, created_at) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (test_user["id"], "test", "completed", 1, isolated_db.now())
        )
        run_id = run_cur.fetchone()["id"]
        
        tx_cur = conn.execute(
            """INSERT INTO transactions (user_id, run_id, source, external_id, raw_payload, parsed_amount, parsed_date, parsed_vendor_raw, status, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (test_user["id"], run_id, "test", "fake-id", "{}", 25.00, "2026-09-01", "Coffee Shop", "pending", isolated_db.now())
        )
        tx_id = tx_cur.fetchone()["id"]

    test_tx = {
        "id": tx_id,
        "user_id": test_user["id"],
        "parsed_amount": 25.00,
        "parsed_date": "2026-09-01",
        "parsed_vendor_raw": "Coffee Shop",
    }

    error_decision = {
        "proposed_vendor": "UNKNOWN",
        "category": "ERROR",
        "confidence_score": 0.0,
        "reasoning": "API call failed: Connection timeout",
        "error": "Connection timeout",
        "exception_type": "TimeoutError",
    }

    state = {
        "transaction": test_tx,
        "memory": [],
        "decision": error_decision,
    }

    result = evaluate_node(state)

    assert result["decision"]["action"] == "escalate"
    assert "error" in result["decision"]["escalation_reason"].lower()


def test_vendor_correction_adapts_next_matching_transaction(isolated_db, test_user, monkeypatch):
    """A human correction is used for the next transaction and is auditable."""
    from app.services import categorization
    from app.services.corrections import record_correction

    class FakeResponse:
        def __init__(self, content):
            self.choices = [type("Choice", (), {"message": type("Message", (), {"content": content})()})()]

    class FakeCompletions:
        def create(self, **kwargs):
            prompt = kwargs["messages"][1]["content"]
            if "vendor_raw_pattern" in prompt or "canonical_vendor" in prompt:
                return FakeResponse(
                    '{"proposed_vendor":"Starbucks","category":"Meals","confidence_score":0.99,"reasoning":"Learned mapping"}'
                )
            return FakeResponse(
                '{"proposed_vendor":"Unknown","category":"Other","confidence_score":0.40,"reasoning":"No learned mapping"}'
            )

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(categorization, "OpenAI", FakeClient)

    with isolated_db.connection() as conn:
        cur = conn.execute(
            "INSERT INTO runs(user_id, source, status, transaction_count, created_at) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (test_user["id"], "test", "completed", 2, isolated_db.now()),
        )
        row = cur.fetchone()
        run_id = row["id"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]
        cur = conn.execute(
            """
            INSERT INTO transactions
            (user_id, run_id, source, external_id, raw_payload, parsed_amount, parsed_date, parsed_vendor_raw, status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (test_user["id"], run_id, "test", "starbucks-1", "{}", 8.0, "2026-09-12", "Starbucks", "flagged", isolated_db.now()),
        )
        row = cur.fetchone()
        first_id = row["id"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]

    first = isolated_db.one("SELECT * FROM transactions WHERE id = %s AND user_id = %s", (first_id, test_user["id"]))
    before = categorization.categorize_transaction(first, [], user_id=test_user["id"])
    assert before["proposed_vendor"] == "Unknown"

    correction = record_correction(first_id, "Starbucks", "Meals", user_id=test_user["id"])
    assert correction["corrected_vendor"] == "Starbucks"

    with isolated_db.connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO transactions
            (user_id, run_id, source, external_id, raw_payload, parsed_amount, parsed_date, parsed_vendor_raw, status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (test_user["id"], run_id, "test", "starbucks-2", "{}", 12.0, "2026-09-13", "SBUX #4521", "pending", isolated_db.now()),
        )
        row = cur.fetchone()
        second_id = row["id"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]

    second = isolated_db.one("SELECT * FROM transactions WHERE id = %s AND user_id = %s", (second_id, test_user["id"]))
    after = categorization.categorize_transaction(second, [], user_id=test_user["id"])

    assert after["proposed_vendor"] == "Starbucks"
    assert after["category"] == "Meals"
    memory_audit = isolated_db.rows(
        "SELECT * FROM audit_log WHERE transaction_id = %s AND user_id = %s AND event_type = 'vendor_memory_match'",
        (second_id, test_user["id"]),
    )
    assert len(memory_audit) == 1
    assert '"memory_row_id"' in memory_audit[0]["detail_text"]


def test_middle_confidence_unmatched_vendor_requests_more_info(isolated_db, test_user):
    """An unmatched middle-confidence proposal takes the third workflow action."""
    from app.services.workflow import evaluate_node

    with isolated_db.connection() as conn:
        cur = conn.execute(
            "INSERT INTO runs(user_id, source, status, transaction_count, created_at) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (test_user["id"], "test", "completed", 1, isolated_db.now()),
        )
        row = cur.fetchone()
        run_id = row["id"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]
        cur = conn.execute(
            """
            INSERT INTO transactions
            (user_id, run_id, source, external_id, raw_payload, parsed_amount, parsed_date, parsed_vendor_raw, status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (test_user["id"], run_id, "test", "unknown-1", "{}", 19.0, "2026-09-12", "Mystery Merchant", "pending", isolated_db.now()),
        )
        row = cur.fetchone()
        transaction_id = row["id"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]

    transaction = isolated_db.one("SELECT * FROM transactions WHERE id = %s AND user_id = %s", (transaction_id, test_user["id"]))
    result = evaluate_node(
        {
            "transaction": transaction,
            "memory": [],
            "decision": {
                "proposed_vendor": "New Vendor",
                "category": "Other",
                "confidence_score": 0.72,
                "reasoning": "Some evidence, but vendor is not known",
            },
        }
    )

    assert result["decision"]["action"] == "request_more_info"
    assert (
        isolated_db.one(
            "SELECT status FROM transactions WHERE id = %s AND user_id = %s",
            (transaction_id, test_user["id"]),
        )["status"]
        == "needs_info"
    )
    decision = isolated_db.one(
        "SELECT action, reasoning FROM match_decisions WHERE transaction_id = %s AND user_id = %s",
        (transaction_id, test_user["id"]),
    )
    assert decision["action"] == "request_more_info"
    assert "human detail" in decision["reasoning"]
    audit = isolated_db.one(
        "SELECT event_type, detail_text FROM audit_log WHERE transaction_id = %s AND user_id = %s AND event_type = 'request_more_info'",
        (transaction_id, test_user["id"]),
    )
    assert audit is not None
    assert "request_more_info" in audit["detail_text"]


# ---------------------------------------------------------------------------
# Fuzzy vendor matching
# ---------------------------------------------------------------------------

def test_fuzzy_vendor_memory_finds_near_miss(isolated_db, test_user):
    """get_fuzzy_vendor_memory catches a near-miss store-number difference."""
    from app.services.memory import get_fuzzy_vendor_memory

    with isolated_db.connection() as conn:
        conn.execute(
            "INSERT INTO vendor_rules_memory (user_id, vendor_raw_pattern, canonical_vendor, category, source, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (test_user["id"], "STARBUCKS 04521 SEATTLE WA", "Starbucks", "Meals & Entertainment", "human_correction", isolated_db.now()),
        )

    results = get_fuzzy_vendor_memory("STARBUCKS 04522 SEATTLE WA", user_id=test_user["id"])

    assert len(results) >= 1
    top = results[0]
    assert top["canonical_vendor"] == "Starbucks"
    assert top["fuzzy_score"] >= 0.82
    assert top["match_type"] == "fuzzy"


def test_fuzzy_vendor_memory_finds_nothing_for_unrelated(isolated_db, test_user):
    """get_fuzzy_vendor_memory returns empty when vendor is completely different."""
    from app.services.memory import get_fuzzy_vendor_memory

    with isolated_db.connection() as conn:
        conn.execute(
            "INSERT INTO vendor_rules_memory (user_id, vendor_raw_pattern, canonical_vendor, category, source, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (test_user["id"], "STARBUCKS 04521 SEATTLE WA", "Starbucks", "Meals & Entertainment", "human_correction", isolated_db.now()),
        )

    results = get_fuzzy_vendor_memory("UNITED AIRLINES 1K MILES", user_id=test_user["id"])

    assert results == []


# ---------------------------------------------------------------------------
# Bulk review actions
# ---------------------------------------------------------------------------

def test_bulk_correction_updates_multiple_transactions(isolated_db, test_user):
    """Bulk correct 2 transactions: both get status='matched', one vendor rule created."""
    from app.services.corrections import bulk_action

    with isolated_db.connection() as conn:
        cur = conn.execute(
            "INSERT INTO runs(user_id, source, status, transaction_count, created_at) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (test_user["id"], "test", "completed", 2, isolated_db.now()),
        )
        row = cur.fetchone()
        run_id = row["id"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]
        cur = conn.execute(
            "INSERT INTO transactions (user_id, run_id, source, external_id, raw_payload, parsed_amount, parsed_date, parsed_vendor_raw, status, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (test_user["id"], run_id, "test", "bulk-tx-1", "{}", 20.0, "2026-09-10", "SBUX #100", "flagged", isolated_db.now()),
        )
        row = cur.fetchone()
        id1 = row["id"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]
        cur = conn.execute(
            "INSERT INTO transactions (user_id, run_id, source, external_id, raw_payload, parsed_amount, parsed_date, parsed_vendor_raw, status, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (test_user["id"], run_id, "test", "bulk-tx-2", "{}", 30.0, "2026-09-11", "SBUX #101", "flagged", isolated_db.now()),
        )
        row = cur.fetchone()
        id2 = row["id"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]

    result = bulk_action(
        transaction_ids=[id1, id2],
        corrected_vendor="Starbucks",
        corrected_category="Meals & Entertainment",
        user_id=test_user["id"],
    )

    assert len(result["successes"]) == 2
    assert result["failures"] == []

    statuses = isolated_db.rows(
        "SELECT status FROM transactions WHERE user_id = %s AND id IN (%s, %s) ORDER BY id",
        (test_user["id"], id1, id2),
    )
    assert all(s["status"] == "matched" for s in statuses)

    rules = isolated_db.rows("SELECT vendor_raw_pattern FROM vendor_rules_memory WHERE user_id = %s ORDER BY id", (test_user["id"],))
    patterns = {r["vendor_raw_pattern"] for r in rules}
    assert "SBUX #100" in patterns
    assert "SBUX #101" in patterns


def test_bulk_correction_bad_id_does_not_block_valid(isolated_db, test_user):
    """A bad transaction ID in a bulk batch is reported separately; valid ones still succeed."""
    from app.services.corrections import bulk_action

    with isolated_db.connection() as conn:
        cur = conn.execute(
            "INSERT INTO runs(user_id, source, status, transaction_count, created_at) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (test_user["id"], "test", "completed", 1, isolated_db.now()),
        )
        row = cur.fetchone()
        run_id = row["id"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]
        cur = conn.execute(
            "INSERT INTO transactions (user_id, run_id, source, external_id, raw_payload, parsed_amount, parsed_date, parsed_vendor_raw, status, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (test_user["id"], run_id, "test", "bulk-valid-1", "{}", 15.0, "2026-09-12", "WHOLE FOODS MKT", "flagged", isolated_db.now()),
        )
        row = cur.fetchone()
        valid_id = row["id"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]

    bad_id = 999999  # does not exist

    result = bulk_action(
        transaction_ids=[valid_id, bad_id],
        corrected_vendor="Whole Foods",
        corrected_category="Groceries",
        user_id=test_user["id"],
    )

    assert len(result["successes"]) == 1
    assert result["successes"][0]["transaction_id"] == valid_id
    assert len(result["failures"]) == 1
    assert result["failures"][0]["transaction_id"] == bad_id


# ---------------------------------------------------------------------------
# Demo reset
# ---------------------------------------------------------------------------

def test_reset_demo_data(isolated_db, test_user):
    """reset_demo_data clears all tables; next insert gets ID=1."""
    with isolated_db.connection() as conn:
        cur = conn.execute(
            "INSERT INTO runs(user_id, source, status, transaction_count, created_at) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (test_user["id"], "test", "completed", 1, isolated_db.now()),
        )
        row = cur.fetchone()
        run_id = row["id"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]
        cur = conn.execute(
            "INSERT INTO transactions (user_id, run_id, source, external_id, raw_payload, parsed_amount, parsed_date, parsed_vendor_raw, status, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (test_user["id"], run_id, "test", "reset-tx-1", "{}", 10.0, "2026-09-01", "Test Vendor", "pending", isolated_db.now()),
        )
        row = cur.fetchone()
        tx_id = row["id"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]
        conn.execute(
            "INSERT INTO vendor_rules_memory (user_id, vendor_raw_pattern, canonical_vendor, category, source, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
            (test_user["id"], "Test Vendor", "Test Vendor", "Other", "human_correction", isolated_db.now()),
        )
        conn.execute(
            "INSERT INTO audit_log (user_id, transaction_id, event_type, detail_text, actor, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
            (test_user["id"], tx_id, "test_event", "seeded for reset test", "test", isolated_db.now()),
        )

    # Confirm rows exist
    assert isolated_db.one("SELECT COUNT(*) AS c FROM runs WHERE user_id = %s", (test_user["id"],))["c"] >= 1
    assert isolated_db.one("SELECT COUNT(*) AS c FROM transactions WHERE user_id = %s", (test_user["id"],))["c"] >= 1

    # Reset
    deleted = isolated_db.reset_demo_data(user_id=test_user["id"])

    assert deleted["runs"] >= 1
    assert deleted["transactions"] >= 1
    assert deleted["vendor_rules_memory"] >= 1
    assert deleted["audit_log"] >= 1

    assert isolated_db.one("SELECT COUNT(*) AS c FROM runs WHERE user_id = %s", (test_user["id"],))["c"] == 0
    assert isolated_db.one("SELECT COUNT(*) AS c FROM transactions WHERE user_id = %s", (test_user["id"],))["c"] == 0
    assert isolated_db.one("SELECT COUNT(*) AS c FROM vendor_rules_memory WHERE user_id = %s", (test_user["id"],))["c"] == 0
