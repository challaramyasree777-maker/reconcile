# Reconcile Architecture and Judging Evidence

## System boundary

Reconcile is a FastAPI + Streamlit application backed by SQLite. External integrations are real sandbox clients: Plaid supplies transactions, Groq returns structured categorization, and QuickBooks receives journal entries. The local database keeps the transaction lifecycle, learned vendor rules, match decisions, ledger results, and audit evidence durable.

```mermaid
flowchart TD
    Trigger[POST /runs/demo] --> Ingest[ingestion.create_run]
    Ingest --> Plaid[Plaid Sandbox]
    Ingest --> Transactions[(transactions)]
    Transactions --> Graph[LangGraph build_graph]
    Graph --> Categorize[categorize_node]
    Categorize --> Rules[(vendor_rules_memory)]
    Categorize --> Groq[Groq structured JSON]
    Categorize --> Decisions[(match_decisions)]
    Graph --> Evaluate[evaluate_node]
    Evaluate --> Auto[auto_post_node]
    Evaluate --> Info[request_more_info_node]
    Evaluate --> Escalate[escalate_node]
    Auto --> QuickBooks[QuickBooks Sandbox]
    Info --> Review[Streamlit review queue]
    Escalate --> Review
    Review --> Correction[POST /corrections/{id}]
    Correction --> Rules
    Correction --> Retry[provide_info -> categorize_node]
    Graph --> Audit[(audit_log)]
    Correction --> Audit
    QuickBooks --> Ledger[(ledger_entries)]
```

## Component map

| Component | Responsibility | Evidence surface |
| --- | --- | --- |
| `app/services/ingestion.py` | Plaid sandbox token exchange, transaction parsing, duplicate protection, parse-error flagging | `create_run`, `pull_plaid_transactions` |
| `app/services/categorization.py` | Groq structured output, learned vendor context, match decision persistence | `categorize_transaction` |
| `app/services/workflow.py` | Explicit state machine and three-way routing | `build_graph`, `evaluate_node`, `route_after_evaluation` |
| `app/services/ledger.py` | QuickBooks sandbox company lookup and journal entry sync | `QuickBooksClient`, `sync_pending_transactions` |
| `app/services/corrections.py` | Human approval, synchronous learning, and provide-info retry | `approve_transaction`, `record_correction`, `provide_transaction_info` |
| `app/main.py` | API trigger, review queue, correction actions, combined audit evidence | `/runs/demo`, `/reviews`, `/corrections/{transaction_id}`, `/audit-log` |
| `streamlit_app.py` | Demo control room and human review interaction | Human review queue and Agent decision evidence sections |
| `app/db.py` | SQLite schema and migrations | `vendor_rules_memory`, `match_decisions`, `audit_log` |

## Decision model

`evaluate_node` preserves the existing error and duplicate safeguards, then adds one middle branch:

- `auto_post`: confidence is at least `0.85`.
- `request_more_info`: confidence is `0.60` to below `0.85` and the proposed vendor has no matching learned rule. The transaction becomes `needs_info` and the route is audited.
- `escalate`: existing fallback for low confidence, known-rule middle confidence, categorization errors, and duplicates.

The LangGraph conditional edge maps all three action strings to actual nodes. `request_more_info_node` is therefore executable workflow behavior, not a label attached after the fact.

## Adaptation loop

1. A flagged or needs-info transaction appears in `/reviews` and Streamlit.
2. A human submits `corrected_vendor` and `corrected_category` to `/corrections/{transaction_id}`.
3. One SQLite transaction upserts `vendor_rules_memory`, inserts `human_corrections`, and marks the reviewed transaction `matched`.
4. The next categorization loads the rule before the Groq call, forces the learned canonical vendor/category into the proposal, and writes a `vendor_memory_match` audit row with the rule ID.
5. A human can instead submit `action=provide_info` and `additional_info`; the endpoint immediately calls `categorize_node` again with that detail in the prompt context.

## Audit evidence

`GET /audit-log` produces one chronological feed from both persisted decision records and audit records. A record contains:

- transaction ID
- selected action or event type
- model reasoning or event/tool detail
- actor
- timestamp

This makes the agent's routing, learned-memory usage, human actions, retries, and ledger handoffs inspectable without reading application logs.

## Criterion mapping

### Goal-driven execution

`POST /runs/demo` executes the bookkeeping goal from source intake through persistence and ledger sync. `app/main.py:trigger_demo_run` records the completed run and returns ingestion plus ledger results.

### Dynamic action selection

`app/services/workflow.py:evaluate_node` calculates the action from confidence, learned vendor rules, error state, and duplicate state. `route_after_evaluation` selects one of three LangGraph branches. `tests/test_phase1.py::test_middle_confidence_unmatched_vendor_requests_more_info` proves `request_more_info` is reachable.

### Multi-step execution

`build_graph` connects categorization, evaluation, one selected action, and final audit. The selected action may invoke QuickBooks or a human workflow. Plaid and QuickBooks each have their own retrying client boundary.

### Adaptation

`record_correction` performs the immediate rule upsert. `categorize_transaction` performs lookup, prompt injection, learned proposal application, and `vendor_memory_match` audit logging. `tests/test_phase1.py::test_vendor_correction_adapts_next_matching_transaction` proves a Starbucks correction changes a later `SBUX #4521` proposal.

### Robustness

`retry_with_backoff` handles transient external failures. Ingestion flags malformed transactions, the workflow escalates duplicates and categorization errors, and ledger synchronization continues per transaction after failures. The original five tests in `tests/test_phase1.py` cover these paths.

## Verification command

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -v
```
