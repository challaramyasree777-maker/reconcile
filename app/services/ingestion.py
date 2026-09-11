from __future__ import annotations

import json
import os
from typing import Any

import requests
from dotenv import load_dotenv

from app import db

load_dotenv()
PLAID_BASE_URL = "https://sandbox.plaid.com"


def _plaid_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(f"{PLAID_BASE_URL}{path}", json=payload, timeout=30)
    print(f"PLAID {path} HTTP {response.status_code}")
    print(response.text)
    response.raise_for_status()
    return response.json()


def pull_plaid_transactions() -> dict[str, Any]:
    base = {"client_id": os.environ["PLAID_CLIENT_ID"], "secret": os.environ["PLAID_SECRET"]}
    public = _plaid_post("/sandbox/public_token/create", {**base, "institution_id": "ins_109508", "initial_products": ["transactions"]})
    exchange = _plaid_post("/item/public_token/exchange", {**base, "public_token": public["public_token"]})
    return _plaid_post("/transactions/sync", {**base, "access_token": exchange["access_token"]})


def create_run(source: str = "plaid_sandbox") -> dict[str, Any]:
    plaid_response = pull_plaid_transactions()
    transactions = plaid_response.get("added", [])
    with db.connection() as conn:
        cursor = conn.execute("INSERT INTO runs(source, status, transaction_count, created_at) VALUES (?, ?, ?, ?)", (source, "ingesting", len(transactions), db.now()))
        run_id = cursor.lastrowid
        inserted = 0
        duplicates = 0
        audit_events: list[tuple[int | None, str, dict[str, Any]]] = []
        for transaction in transactions:
            external_id = transaction["transaction_id"]
            try:
                cursor = conn.execute(
                    "INSERT INTO transactions (run_id, source, external_id, raw_payload, parsed_amount, parsed_date, parsed_vendor_raw, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                    (run_id, "bank", external_id, json.dumps(transaction), transaction.get("amount"), transaction.get("date"), transaction.get("merchant_name") or transaction.get("name"), db.now()),
                )
                audit_events.append((cursor.lastrowid, "transaction_ingested", {"source": source, "external_id": external_id}))
                inserted += 1
            except Exception as exc:
                if "UNIQUE constraint failed" in str(exc):
                    duplicates += 1
                    audit_events.append((None, "duplicate_skipped", {"external_id": external_id}))
                else:
                    raise
        status = "completed" if duplicates == 0 else "completed_with_duplicates"
        conn.execute("UPDATE runs SET status = ?, transaction_count = ? WHERE id = ?", (status, inserted, run_id))
    for transaction_id, event_type, detail in audit_events:
        db.add_audit(transaction_id, event_type, detail)
    db.add_audit(None, "plaid_sync_completed", {"run_id": run_id, "added": len(transactions), "next_cursor": plaid_response.get("next_cursor")})
    return {"run_id": run_id, "source": source, "inserted": inserted, "duplicates": duplicates, "status": status, "next_cursor": plaid_response.get("next_cursor")}
