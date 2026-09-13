from __future__ import annotations

import json
import os
from typing import Any

import requests
from dotenv import load_dotenv

from app import db
from app.services.utils import retry_with_backoff

load_dotenv()
PLAID_BASE_URL = "https://sandbox.plaid.com"


def _plaid_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(f"{PLAID_BASE_URL}{path}", json=payload, timeout=30)
    print(f"PLAID {path} HTTP {response.status_code}")
    print(response.text)
    response.raise_for_status()
    return response.json()


def pull_plaid_transactions() -> dict[str, Any]:
    """Fetch transactions from Plaid with automatic retry on transient errors."""
    base = {
        "client_id": os.environ.get("PLAID_CLIENT_ID", ""),
        "secret": os.environ.get("PLAID_SECRET", ""),
    }

    def _fetch():
        public = retry_with_backoff(
            _plaid_post,
            "/sandbox/public_token/create",
            {**base, "institution_id": "ins_109508", "initial_products": ["transactions"]},
        )
        exchange = retry_with_backoff(
            _plaid_post,
            "/item/public_token/exchange",
            {**base, "public_token": public["public_token"]},
        )
        return retry_with_backoff(
            _plaid_post,
            "/transactions/sync",
            {**base, "access_token": exchange["access_token"]},
        )

    return _fetch()


def create_run(source: str = "plaid_sandbox", user_id: int = 1) -> dict[str, Any]:
    """
    Create a reconciliation run by pulling transactions from Plaid.
    Scoped by user_id.
    """
    try:
        plaid_response = pull_plaid_transactions()
    except Exception as exc:
        # Plaid fetch failed — log and return early
        error_msg = str(exc)
        db.add_audit(
            None,
            "plaid_fetch_failed",
            {"error": error_msg, "exception_type": type(exc).__name__},
            user_id=user_id,
        )
        return {
            "run_id": None,
            "source": source,
            "inserted": 0,
            "flagged": 0,
            "status": "plaid_fetch_failed",
            "error": error_msg,
        }

    transactions = plaid_response.get("added", [])
    with db.connection() as conn:
        cursor = conn.execute(
            "INSERT INTO runs(user_id, source, status, transaction_count, created_at) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (user_id, source, "ingesting", len(transactions), db.now()),
        )
        run_row = cursor.fetchone()
        run_id = run_row["id"] if isinstance(run_row, dict) or hasattr(run_row, "keys") else run_row[0]

        inserted = 0
        duplicates = 0
        parse_errors = 0
        audit_events: list[tuple[int | None, str, dict[str, Any]]] = []

        for transaction in transactions:
            external_id = transaction.get("transaction_id", "unknown")
            try:
                # Parse required fields
                parsed_amount = transaction.get("amount")
                parsed_date = transaction.get("date")
                parsed_vendor = transaction.get("merchant_name") or transaction.get("name")

                if parsed_amount is None or parsed_date is None or parsed_vendor is None:
                    raise ValueError(
                        f"Missing required fields: amount={parsed_amount}, date={parsed_date}, vendor={parsed_vendor}"
                    )

                # Check for duplicates before insert to avoid Postgres aborted transaction state
                existing = conn.execute(
                    "SELECT id FROM transactions WHERE user_id = %s AND external_id = %s",
                    (user_id, external_id)
                ).fetchone()
                
                if existing:
                    duplicates += 1
                    audit_events.append((None, "duplicate_skipped", {"external_id": external_id}))
                    continue

                tx_cur = conn.execute(
                    """
                    INSERT INTO transactions
                    (user_id, run_id, source, external_id, raw_payload, parsed_amount, parsed_date, parsed_vendor_raw, status, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s)
                    RETURNING id
                    """,
                    (
                        user_id,
                        run_id,
                        "bank",
                        external_id,
                        json.dumps(transaction),
                        parsed_amount,
                        parsed_date,
                        parsed_vendor,
                        db.now(),
                    ),
                )
                tx_row = tx_cur.fetchone()
                tx_id = tx_row["id"] if isinstance(tx_row, dict) or hasattr(tx_row, "keys") else tx_row[0]
                audit_events.append((tx_id, "transaction_ingested", {"source": source, "external_id": external_id}))
                inserted += 1
            except Exception as parse_exc:
                # Parse error — mark as flagged instead of crashing
                parse_errors += 1
                error_detail = str(parse_exc)
                try:
                    err_cur = conn.execute(
                        """
                        INSERT INTO transactions (user_id, run_id, source, external_id, raw_payload, status, created_at)
                        VALUES (%s, %s, %s, %s, %s, 'flagged', %s)
                        RETURNING id
                        """,
                        (user_id, run_id, "bank", external_id, json.dumps(transaction), db.now()),
                    )
                    err_row = err_cur.fetchone()
                    err_id = err_row["id"] if isinstance(err_row, dict) or hasattr(err_row, "keys") else err_row[0]
                    audit_events.append(
                        (err_id, "parse_error", {"error": error_detail, "exception_type": type(parse_exc).__name__})
                    )
                except Exception:
                    audit_events.append(
                        (None, "parse_error_unrecoverable", {"external_id": external_id, "error": error_detail})
                    )

        status = "completed"
        if duplicates > 0:
            status = "completed_with_duplicates"
        if parse_errors > 0:
            status = "completed_with_parse_errors"
        if parse_errors > 0 and duplicates > 0:
            status = "completed_with_duplicates_and_parse_errors"

        conn.execute("UPDATE runs SET status = %s, transaction_count = %s WHERE id = %s", (status, inserted, run_id))

    for transaction_id, event_type, detail in audit_events:
        db.add_audit(transaction_id, event_type, detail, user_id=user_id)
    db.add_audit(
        None,
        "plaid_sync_completed",
        {
            "run_id": run_id,
            "added": len(transactions),
            "inserted": inserted,
            "duplicates": duplicates,
            "parse_errors": parse_errors,
            "next_cursor": plaid_response.get("next_cursor"),
        },
        user_id=user_id,
    )

    return {
        "run_id": run_id,
        "source": source,
        "inserted": inserted,
        "duplicates": duplicates,
        "parse_errors": parse_errors,
        "status": status,
        "next_cursor": plaid_response.get("next_cursor"),
    }
