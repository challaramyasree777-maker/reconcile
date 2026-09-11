from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from app import db
from app.services import ingestion, ledger


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Reconcile", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/runs/demo")
def trigger_demo_run() -> dict[str, Any]:
    run = ingestion.create_run()
    sync = ledger.sync_pending_transactions()
    db.add_audit(None, "run_completed", {"run_id": run["run_id"], "ingestion": run, "ledger": sync})
    return {**run, "ledger": sync}


@app.get("/runs")
def list_runs() -> list[dict[str, Any]]:
    return db.rows("SELECT * FROM runs ORDER BY id DESC")


@app.get("/transactions")
def list_transactions() -> list[dict[str, Any]]:
    return db.rows("SELECT * FROM transactions ORDER BY id DESC")


@app.get("/ledger")
def list_ledger() -> list[dict[str, Any]]:
    return db.rows("SELECT ledger_entries.*, transactions.parsed_vendor_raw FROM ledger_entries JOIN transactions ON transactions.id = ledger_entries.transaction_id ORDER BY ledger_entries.id DESC")


@app.get("/audit")
def list_audit() -> list[dict[str, Any]]:
    return db.rows("SELECT * FROM audit_log ORDER BY id DESC")