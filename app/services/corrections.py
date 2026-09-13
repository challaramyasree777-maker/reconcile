from __future__ import annotations

from app import db


def provide_transaction_info(
    transaction_id: int,
    additional_info: str,
    user_id: int | None = None,
) -> dict:
    if user_id is not None:
        transaction = db.one("SELECT * FROM transactions WHERE id = %s AND user_id = %s", (transaction_id, user_id))
    else:
        transaction = db.one("SELECT * FROM transactions WHERE id = %s", (transaction_id,))

    if not transaction:
        raise ValueError(f"Transaction {transaction_id} not found")
    if not additional_info.strip():
        raise ValueError("additional_info is required")

    uid = user_id or transaction["user_id"]
    from app.services.workflow import categorize_node

    enriched_transaction = dict(transaction)
    enriched_transaction["additional_human_detail"] = additional_info.strip()
    enriched_transaction["user_id"] = uid
    result = categorize_node({"transaction": enriched_transaction, "memory": []})
    db.add_audit(
        transaction_id,
        "human_provided_info",
        {"additional_info": additional_info.strip(), "decision": result["decision"]},
        actor="human",
        user_id=uid,
    )
    return {
        "transaction_id": transaction_id,
        "status": transaction["status"],
        "decision": result["decision"],
    }


def approve_transaction(transaction_id: int, user_id: int | None = None) -> dict:
    if user_id is not None:
        transaction = db.one(
            "SELECT id, user_id FROM transactions WHERE id = %s AND user_id = %s",
            (transaction_id, user_id),
        )
    else:
        transaction = db.one(
            "SELECT id, user_id FROM transactions WHERE id = %s",
            (transaction_id,),
        )
    if not transaction:
        raise ValueError(f"Transaction {transaction_id} not found")

    uid = user_id or transaction["user_id"]
    with db.connection() as conn:
        conn.execute("UPDATE transactions SET status = 'matched' WHERE id = %s", (transaction_id,))

    db.add_audit(transaction_id, "human_approved", {"status": "matched"}, actor="human", user_id=uid)
    return {"transaction_id": transaction_id, "status": "matched"}


def record_correction(
    transaction_id: int,
    corrected_vendor: str,
    corrected_category: str,
    user_id: int | None = None,
) -> dict:
    if user_id is not None:
        transaction = db.one(
            "SELECT id, user_id, parsed_vendor_raw, parsed_amount FROM transactions WHERE id = %s AND user_id = %s",
            (transaction_id, user_id),
        )
    else:
        transaction = db.one(
            "SELECT id, user_id, parsed_vendor_raw, parsed_amount FROM transactions WHERE id = %s",
            (transaction_id,),
        )

    if not transaction:
        raise ValueError(f"Transaction {transaction_id} not found")

    uid = user_id or transaction["user_id"]
    with db.connection() as conn:
        now = db.now()
        conn.execute(
            """
            INSERT INTO vendor_rules_memory
            (user_id, vendor_raw_pattern, canonical_vendor, category, source, created_at)
            VALUES (%s, %s, %s, %s, 'human_correction', %s)
            ON CONFLICT(user_id, vendor_raw_pattern) DO UPDATE SET
                canonical_vendor = excluded.canonical_vendor,
                category = excluded.category,
                source = excluded.source,
                created_at = excluded.created_at
            """,
            (uid, transaction["parsed_vendor_raw"], corrected_vendor, corrected_category, now),
        )
        rule = db.one(
            "SELECT * FROM vendor_rules_memory WHERE user_id = %s AND vendor_raw_pattern = %s",
            (uid, transaction["parsed_vendor_raw"]),
        )
        conn.execute(
            """
            INSERT INTO human_corrections
            (user_id, transaction_id, original_category, corrected_vendor, corrected_category, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (uid, transaction_id, None, corrected_vendor, corrected_category, now),
        )
        conn.execute(
            "UPDATE transactions SET status = 'matched' WHERE id = %s",
            (transaction_id,),
        )

    db.add_audit(
        transaction_id,
        "human_correction",
        {
            "memory_row_id": rule["id"] if rule else None,
            "vendor_raw_pattern": transaction["parsed_vendor_raw"],
            "canonical_vendor": corrected_vendor,
            "corrected_category": corrected_category,
        },
        actor="human",
        user_id=uid,
    )

    return {
        "transaction_id": transaction_id,
        "vendor_raw_pattern": transaction["parsed_vendor_raw"],
        "corrected_vendor": corrected_vendor,
        "corrected_category": corrected_category,
    }


def bulk_action(
    transaction_ids: list[int],
    approve: bool = False,
    corrected_vendor: str | None = None,
    corrected_category: str | None = None,
    user_id: int | None = None,
) -> dict:
    """Apply an approve or correction action to multiple transactions at once."""
    if approve and (corrected_vendor or corrected_category):
        raise ValueError("approve cannot be combined with correction fields")
    if not approve and not (corrected_vendor and corrected_category):
        raise ValueError(
            "Either approve=True or both corrected_vendor and corrected_category are required"
        )

    successes: list[dict] = []
    failures: list[dict] = []

    for tid in transaction_ids:
        try:
            if approve:
                result = approve_transaction(tid, user_id=user_id)
            else:
                result = record_correction(tid, corrected_vendor, corrected_category, user_id=user_id)  # type: ignore[arg-type]
            successes.append(result)
        except Exception as exc:
            failures.append({"transaction_id": tid, "error": str(exc)})

    return {"successes": successes, "failures": failures}