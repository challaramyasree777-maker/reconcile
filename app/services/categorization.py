from __future__ import annotations

import json
import os
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from app import db
from app.services.memory import get_all_vendor_memory, get_fuzzy_vendor_memory, get_vendor_memory
from app.services.utils import retry_with_backoff

load_dotenv()


def categorize_transaction(
    transaction: dict[str, Any],
    memory: list[dict[str, Any]],
    user_id: int | None = None,
) -> dict[str, Any]:
    """
    Categorize a transaction using Groq LLM with automatic retry on transient errors.
    Scoped by user_id for memory lookups and decision storage.
    """
    uid = user_id or transaction.get("user_id") or 1
    client = OpenAI(api_key=os.environ.get("GROQ_API_KEY", ""), base_url="https://api.groq.com/openai/v1")
    vendor_raw = transaction.get("parsed_vendor_raw", "")
    learned_rules = get_vendor_memory(vendor_raw, user_id=uid)
    # Fuzzy candidates only fired when exact/substring match finds nothing
    fuzzy_rules = get_fuzzy_vendor_memory(vendor_raw, user_id=uid) if not learned_rules else []
    memory_context = learned_rules or get_all_vendor_memory(user_id=uid) or memory
    prompt = {
        "transaction": transaction,
        "additional_human_detail": transaction.get("additional_human_detail", ""),
        "vendor_rules_memory": memory_context,
        "fuzzy_vendor_candidates": [
            {
                "vendor_raw_pattern": r["vendor_raw_pattern"],
                "canonical_vendor": r["canonical_vendor"],
                "category": r["category"],
                "similarity_score": r["fuzzy_score"],
            }
            for r in fuzzy_rules
        ],
        "contract": {
            "proposed_vendor": "string",
            "category": "string",
            "confidence_score": "number 0-1",
            "reasoning": "string",
        },
    }

    if learned_rules:
        rule = learned_rules[0]
        db.add_audit(
            transaction.get("id"),
            "vendor_memory_match",
            {
                "memory_row_id": rule["id"],
                "vendor_raw_pattern": rule["vendor_raw_pattern"],
                "canonical_vendor": rule["canonical_vendor"],
            },
            user_id=uid,
        )

    if fuzzy_rules and not learned_rules:
        db.add_audit(
            transaction.get("id"),
            "fuzzy_vendor_candidates",
            {
                "vendor_raw": vendor_raw,
                "candidates": [
                    {"vendor_raw_pattern": r["vendor_raw_pattern"], "fuzzy_score": r["fuzzy_score"]}
                    for r in fuzzy_rules[:3]
                ],
            },
            user_id=uid,
        )

    def _call_groq():
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": "Return only valid JSON matching the requested contract."},
                {"role": "user", "content": json.dumps(prompt)},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        return json.loads(response.choices[0].message.content or "{}")

    try:
        result = retry_with_backoff(_call_groq, max_retries=2, backoff_base=1.0)
        if learned_rules:
            rule = learned_rules[0]
            result["proposed_vendor"] = rule["canonical_vendor"]
            result["category"] = rule["category"]
            result["reasoning"] = f"Matched learned vendor rule {rule['id']} for {rule['vendor_raw_pattern']}"
        elif memory_context:
            matched_rule = next(
                (
                    rule
                    for rule in memory_context
                    if result.get("proposed_vendor", "").strip().lower() == rule["canonical_vendor"].strip().lower()
                ),
                None,
            )
            if matched_rule:
                db.add_audit(
                    transaction.get("id"),
                    "vendor_memory_match",
                    {
                        "memory_row_id": matched_rule["id"],
                        "vendor_raw_pattern": matched_rule["vendor_raw_pattern"],
                        "canonical_vendor": matched_rule["canonical_vendor"],
                        "match_type": "model_context",
                    },
                    user_id=uid,
                )
        if transaction.get("id"):
            with db.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO match_decisions
                    (user_id, transaction_id, proposed_vendor, category, confidence_score, reasoning, action, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        uid,
                        transaction["id"],
                        result.get("proposed_vendor", "UNKNOWN"),
                        result.get("category", "ERROR"),
                        float(result.get("confidence_score", 0)),
                        result.get("reasoning", ""),
                        result.get("action"),
                        db.now(),
                    ),
                )
        return result
    except Exception as exc:
        # Groq call failed after retries — return flagged decision
        error_msg = str(exc)
        return {
            "proposed_vendor": "UNKNOWN",
            "category": "ERROR",
            "confidence_score": 0.0,
            "reasoning": f"Categorization failed: {error_msg}",
            "error": error_msg,
            "exception_type": type(exc).__name__,
        }