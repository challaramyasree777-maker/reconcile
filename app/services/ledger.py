from __future__ import annotations

import base64
import os
from typing import Any

import requests
from dotenv import load_dotenv

from app import db

load_dotenv()


class QuickBooksClient:
    token_url = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
    api_root = "https://sandbox-quickbooks.api.intuit.com/v3/company"

    def __init__(self) -> None:
        self.client_id = os.environ["QUICKBOOKS_CLIENT_ID"]
        self.client_secret = os.environ["QUICKBOOKS_CLIENT_SECRET"]
        self.realm_id = os.environ["QUICKBOOKS_REALM_ID"]
        self.refresh_token = os.environ["QUICKBOOKS_REFRESH_TOKEN"]
        self.access_token: str | None = None

    def refresh_access_token(self) -> str:
        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()

        response = requests.post(
            self.token_url,
            headers={
                "Authorization": f"Basic {credentials}",
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            },
            timeout=30,
        )

        print(f"QUICKBOOKS token refresh HTTP {response.status_code}")
        print(response.text)

        response.raise_for_status()

        token_response = response.json()
        self.access_token = token_response["access_token"]
        return self.access_token

    def request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        token = self.access_token or self.refresh_access_token()

        headers = kwargs.pop("headers", {})
        headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            }
        )

        response = requests.request(
            method,
            f"{self.api_root}/{self.realm_id}{path}",
            headers=headers,
            timeout=30,
            **kwargs,
        )

        if response.status_code == 401:
            self.refresh_access_token()
            headers["Authorization"] = f"Bearer {self.access_token}"

            response = requests.request(
                method,
                f"{self.api_root}/{self.realm_id}{path}",
                headers=headers,
                timeout=30,
                **kwargs,
            )

        print(f"QUICKBOOKS {method} {path} HTTP {response.status_code}")
        print(response.text)

        response.raise_for_status()
        return response

    def company_info(self) -> dict[str, Any]:
        return self.request(
            "GET",
            "/companyinfo/" + self.realm_id,
        ).json()

    def create_journal_entry(
        self, transaction: dict[str, Any]
    ) -> dict[str, Any]:
        amount = abs(float(transaction["parsed_amount"]))

        payload = {
            "TxnDate": transaction["parsed_date"],
            "PrivateNote": f"Reconcile transaction {transaction['external_id']}",
            "Line": [
                {
                    "Description": transaction["parsed_vendor_raw"],
                    "Amount": amount,
                    "DetailType": "JournalEntryLineDetail",
                    "JournalEntryLineDetail": {
                        "PostingType": "Debit",
                        "AccountRef": {"value": "1"},
                    },
                },
                {
                    "Description": f"Offset for {transaction['parsed_vendor_raw']}",
                    "Amount": amount,
                    "DetailType": "JournalEntryLineDetail",
                    "JournalEntryLineDetail": {
                        "PostingType": "Credit",
                        "AccountRef": {"value": "1"},
                    },
                },
            ],
        }

        return self.request(
            "POST",
            "/journalentry",
            json=payload,
        ).json()


def sync_pending_transactions() -> dict[str, Any]:
    client = QuickBooksClient()

    company_info = client.company_info()

    pending = db.rows(
        "SELECT * FROM transactions WHERE status = 'pending' ORDER BY id"
    )

    posted = 0

    for transaction in pending:
        ledger_response = client.create_journal_entry(transaction)

        external_id = ledger_response.get(
            "JournalEntry", {}
        ).get("Id", "unknown")

        with db.connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO ledger_entries
                (transaction_id, external_ledger_id, amount, vendor, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    transaction["id"],
                    external_id,
                    transaction["parsed_amount"],
                    transaction["parsed_vendor_raw"],
                    "posted",
                    db.now(),
                ),
            )

            conn.execute(
                "UPDATE transactions SET status = 'posted' WHERE id = ?",
                (transaction["id"],),
            )

        db.add_audit(
            transaction["id"],
            "ledger_synced",
            {
                "adapter": "quickbooks_sandbox",
                "external_ledger_id": external_id,
            },
        )

        posted += 1

    return {
        "posted": posted,
        "adapter": "quickbooks_sandbox",
        "company_info": company_info,
    }
