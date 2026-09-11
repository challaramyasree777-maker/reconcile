from __future__ import annotations

from typing import Any

from app import db


def get_vendor_memory(vendor: str) -> list[dict[str, Any]]:
    return db.rows(
        "SELECT vendor, category, source FROM vendor_memory WHERE LOWER(vendor) = LOWER(?)",
        (vendor,),
    )


def save_vendor_memory(vendor: str, category: str, source: str = "human_correction") -> None:
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO vendor_memory (vendor, category, source, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(vendor) DO UPDATE SET
                category = excluded.category,
                source = excluded.source
            """,
            (vendor, category, source, db.now()),
        )