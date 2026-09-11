from __future__ import annotations

import json
import os
import time
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI, RateLimitError

load_dotenv()


def categorize_transaction(transaction: dict[str, Any], memory: list[dict[str, Any]]) -> dict[str, Any]:
    client = OpenAI(api_key=os.environ["GROQ_API_KEY"], base_url="https://api.groq.com/openai/v1")
    prompt = {"transaction": transaction, "vendor_rules_memory": memory, "contract": {"proposed_vendor": "string", "category": "string", "confidence_score": "number 0-1", "reasoning": "string"}}
    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[{"role": "system", "content": "Return only valid JSON matching the requested contract."}, {"role": "user", "content": json.dumps(prompt)}],
                response_format={"type": "json_object"},
                temperature=0,
            )
            return json.loads(response.choices[0].message.content or "{}")
        except RateLimitError:
            if attempt == 2:
                raise
            time.sleep(2**attempt)
    raise RuntimeError("Groq categorization did not return a result")