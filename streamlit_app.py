from __future__ import annotations

import os
from typing import Any

import requests
import streamlit as st

API_URL = os.getenv("RECONCILE_API_URL", "http://localhost:8000")

st.set_page_config(page_title="Reconcile / control room", page_icon="R", layout="wide")
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&family=Space+Mono&display=swap');
    :root { --ink: #17211b; --muted: #6d776f; --paper: #f4f1e8; --mint: #b9d9c2; --orange: #ee865b; }
    .stApp { background: var(--paper); color: var(--ink); }
    h1, h2, h3 { font-family: 'DM Sans', sans-serif; letter-spacing: 0; }
    p, label, .stMarkdown { font-family: 'DM Sans', sans-serif; }
    .eyebrow { color: var(--orange); font-family: 'Space Mono', monospace; font-size: .75rem; letter-spacing: .12em; text-transform: uppercase; }
    .hero { border-bottom: 1px solid #c9c7bb; padding: 1rem 0 1.75rem; margin-bottom: 1.5rem; }
    .hero h1 { font-size: 3.5rem; margin: .15rem 0 0; }
    .hero p { color: var(--muted); font-size: 1.05rem; max-width: 38rem; }
    [data-testid='stMetric'] { background: #e6eadf; border: 1px solid #d0d7c9; padding: 1rem; border-radius: 6px; }
    .stButton > button { background: var(--ink); color: white; border: 0; border-radius: 4px; padding: .65rem 1rem; }
    code { font-family: 'Space Mono', monospace; }
    </style>
    """,
    unsafe_allow_html=True,
)


def get(path: str) -> list[dict[str, Any]]:
    try:
        response = requests.get(f"{API_URL}{path}", timeout=3)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        st.error(f"Backend unavailable: {exc}")
        return []


st.markdown('<div class="hero"><div class="eyebrow">Autonomous bookkeeping / phase 01</div><h1>Reconcile</h1><p>A calm control room for moving bank activity into the ledger, with every handoff visible.</p></div>', unsafe_allow_html=True)

if st.button("Run Plaid sandbox demo", type="primary"):
    try:
        response = requests.post(f"{API_URL}/runs/demo", timeout=10)
        response.raise_for_status()
        st.success(f"Run {response.json()['run_id']} completed and {response.json()['ledger']['posted']} entries synced.")
        st.rerun()
    except requests.RequestException as exc:
        st.error(f"Run failed: {exc}")

transactions = get("/transactions")
ledger_entries = get("/ledger")
audits = get("/audit")
runs = get("/runs")

posted = sum(item["status"] == "posted" for item in transactions)
col1, col2, col3, col4 = st.columns(4)
col1.metric("Runs", len(runs))
col2.metric("Transactions", len(transactions))
col3.metric("Posted", posted)
col4.metric("Audit events", len(audits))

left, right = st.columns([1.25, 1])
with left:
    st.subheader("Bank intake")
    if transactions:
        st.dataframe(
            [{"vendor": item["parsed_vendor_raw"], "amount": f"${item['parsed_amount']:,.2f}", "date": item["parsed_date"], "status": item["status"], "source id": item["external_id"]} for item in transactions],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No transactions yet. Trigger the demo run to begin the flow.")

with right:
    st.subheader("Ledger sync")
    st.dataframe(
        [{"ledger id": item["external_ledger_id"], "vendor": item["vendor"], "amount": f"${item['amount']:,.2f}", "status": item["status"]} for item in ledger_entries],
        use_container_width=True,
        hide_index=True,
    )

st.subheader("Audit trail")
if audits:
    st.dataframe(
        [{"event": item["event_type"], "actor": item["actor"], "detail": item["detail_text"], "at": item["created_at"]} for item in audits[:20]],
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("Audit events will appear here after the first run.")
