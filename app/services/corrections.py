from __future__ import annotations

from app import db
from app.services.memory import save_vendor_memory


def record_correction(
    transaction_id: int,
    corrected_category: str,
) -> dict:
    transaction = db.one(
        "SELECT parsed_vendor_raw, parsed_amount FROM transactions WHERE id = ?",
        (transaction_id,),
    )

    if not transaction:
        raise ValueError(f"Transaction {transaction_id} not found")

    save_vendor_memory(
        transaction["parsed_vendor_raw"],
        corrected_category,
    )

    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO human_corrections
            (transaction_id, original_category, corrected_category, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                transaction_id,
                None,
                corrected_category,
                db.now(),
            ),
        )

    db.add_audit(
        transaction_id,
        "human_correction",
        {
            "vendor": transaction["parsed_vendor_raw"],
            "corrected_category": corrected_category,
        },
        actor="human",
    )

    return {
        "transaction_id": transaction_id,
        "vendor": transaction["parsed_vendor_raw"],
        "corrected_category": corrected_category,
    }