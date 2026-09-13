from __future__ import annotations

import difflib
from typing import Any

from app import db


def get_vendor_memory(vendor: str, user_id: int = 1) -> list[dict[str, Any]]:
    """Return learned rules that directly match the raw vendor text for this user."""
    normalized = (vendor or "").strip().lower()
    if not normalized:
        return []
    matches = db.rows(
        """
        SELECT id, vendor_raw_pattern, canonical_vendor, category, source
        FROM vendor_rules_memory
        WHERE user_id = %s
          AND (LOWER(vendor_raw_pattern) = %s
               OR %s LIKE '%%' || LOWER(vendor_raw_pattern) || '%%'
               OR LOWER(vendor_raw_pattern) LIKE '%%' || %s || '%%')
        ORDER BY id DESC
        """,
        (user_id, normalized, normalized, normalized),
    )
    return matches


def get_fuzzy_vendor_memory(
    vendor: str,
    user_id: int = 1,
    threshold: float = 0.82,
) -> list[dict[str, Any]]:
    """Return vendor rules whose raw pattern is similar to *vendor* for this user."""
    normalized = (vendor or "").strip().lower()
    if not normalized:
        return []

    all_rules = db.rows(
        "SELECT id, vendor_raw_pattern, canonical_vendor, category, source FROM vendor_rules_memory WHERE user_id = %s",
        (user_id,),
    )
    scored: list[tuple[float, dict[str, Any]]] = []
    for rule in all_rules:
        pattern = (rule["vendor_raw_pattern"] or "").strip().lower()
        score = difflib.SequenceMatcher(None, normalized, pattern).ratio()
        if score >= threshold:
            scored.append((score, {**rule, "fuzzy_score": round(score, 4), "match_type": "fuzzy"}))

    scored.sort(key=lambda t: t[0], reverse=True)
    return [row for _, row in scored]


def get_all_vendor_memory(user_id: int = 1) -> list[dict[str, Any]]:
    """Return learned rules as model context without forcing an unrelated match."""
    return db.rows(
        """
        SELECT id, vendor_raw_pattern, canonical_vendor, category, source
        FROM vendor_rules_memory
        WHERE user_id = %s
        ORDER BY id DESC
        """,
        (user_id,),
    )


def save_vendor_memory(
    vendor_raw_pattern: str,
    canonical_vendor: str,
    category: str,
    source: str = "human_correction",
    user_id: int = 1,
) -> dict[str, Any]:
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO vendor_rules_memory
            (user_id, vendor_raw_pattern, canonical_vendor, category, source, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT(user_id, vendor_raw_pattern) DO UPDATE SET
                canonical_vendor = excluded.canonical_vendor,
                category = excluded.category,
                source = excluded.source,
                created_at = excluded.created_at
            """,
            (user_id, vendor_raw_pattern, canonical_vendor, category, source, db.now()),
        )
        row = db.one(
            "SELECT * FROM vendor_rules_memory WHERE user_id = %s AND vendor_raw_pattern = %s",
            (user_id, vendor_raw_pattern),
        )
    return row or {}