from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import app.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    return db


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


def test_demo_run_ingests_and_syncs(isolated_db, fake_plaid):
    from app.services.ingestion import create_run
    from app.services.ledger import sync_pending_transactions

    run = create_run()
    sync = sync_pending_transactions()

    assert run["inserted"] == 3
    assert sync["posted"] == 3
    assert isolated_db.rows("SELECT status FROM transactions") == [{"status": "posted"}] * 3
    assert isolated_db.one("SELECT COUNT(*) AS count FROM audit_log")["count"] == 7


def test_second_run_skips_duplicates(isolated_db, fake_plaid):
    from app.services.ingestion import create_run

    first = create_run()
    second = create_run()

    assert first["inserted"] == 3
    assert second["inserted"] == 0
    assert second["duplicates"] == 3
