# Reconcile

Reconcile is an agentic bookkeeping control room. It pulls transactions from Plaid Sandbox, proposes vendor/category matches with Groq structured output, selects an action in LangGraph, posts approved work to QuickBooks Sandbox, and records the complete decision trail in SQLite (development) or PostgreSQL (production).

## Environment setup

Copy `.env.example` to `.env` and fill in values:

```text
# Database — SQLite for local dev, Postgres for production:
DATABASE_URL=sqlite:///./reconcile.db
# DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/reconcile

# Auth — use a strong random string (e.g. openssl rand -hex 32):
SECRET_KEY=<at-least-32-char-random-string>

# Admin account — leave blank to auto-generate a random password on first
# startup (it will be printed once to the server log). Set a value here to
# pin it. Rotate the password immediately after the first login.
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=       # leave blank → auto-generated

# External sandboxes:
GROQ_API_KEY=...
PLAID_CLIENT_ID=...
PLAID_SECRET=...
PLAID_ENV=sandbox
QUICKBOOKS_CLIENT_ID=...
QUICKBOOKS_CLIENT_SECRET=...
QUICKBOOKS_REALM_ID=...
QUICKBOOKS_REFRESH_TOKEN=...
QB_ENVIRONMENT=sandbox
```

> **Security note:** `.env` is git-ignored and must never be committed. The example file contains no real credentials.

## Run with a virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

In a second terminal:

```powershell
.\.venv\Scripts\Activate.ps1
streamlit run streamlit_app.py
```

Open `http://localhost:8501`. The API and interactive documentation are at `http://localhost:8000` and `http://localhost:8000/docs`.

## Run with Docker

```powershell
Copy-Item .env.example .env
docker compose up --build
```

The API is exposed on port `8000` and Streamlit on port `8501`.

## Architecture

```mermaid
flowchart LR
    UI[Streamlit review control room] --> API[FastAPI]
    API --> G[LangGraph]
    G --> P[Plaid Sandbox ingestion]
    G --> C[Groq structured categorization]
    C --> M[(vendor_rules_memory)]
    G --> E{evaluate_node}
    E -->|auto_post| Q[QuickBooks Sandbox ledger sync]
    E -->|request_more_info| R[needs_info review]
    E -->|escalate| H[human review]
    R --> API
    H --> API
    API -->|correction| M
    API --> D[(SQLite: transactions, decisions, audit)]
    Q --> D
```

The detailed component and evidence map is in [docs/architecture.md](docs/architecture.md).

## Hackathon criteria and evidence

| Criterion | Implementation evidence |
| --- | --- |
| Goal-driven execution | `app/main.py:trigger_demo_run` drives ingestion, ledger synchronization, and completion audit as one goal-oriented request. |
| Dynamic action selection | `app/services/workflow.py:evaluate_node` reaches `auto_post`, `request_more_info`, or `escalate`; `route_after_evaluation` maps the selected action to a LangGraph node. `test_middle_confidence_unmatched_vendor_requests_more_info` proves the third route. |
| Multi-step execution | `workflow.py:build_graph` runs categorize -> evaluate -> selected action -> audit; Plaid and QuickBooks calls are isolated in `ingestion.py` and `ledger.py`. |
| Adaptation | `app/services/corrections.py:record_correction` synchronously upserts `vendor_rules_memory`; `categorization.py:categorize_transaction` injects and applies the rule. `test_vendor_correction_adapts_next_matching_transaction` proves Starbucks -> SBUX adaptation and audit evidence. |
| Robustness | `app/services/utils.py:retry_with_backoff`, ingestion parse-error handling, duplicate routing, and ledger/Groq failure handling are covered by the five original robustness tests in `tests/test_phase1.py`. |

## Audit evidence

`GET /audit-log` returns a chronological union of `match_decisions` and `audit_log`, including route/action, reasoning or tool/event detail, actor, transaction ID, and timestamp. The Streamlit **Agent decision evidence** table renders the same feed for demos.

## Tests

```powershell
pytest -v
```

### Running tests against PostgreSQL

By default the test suite uses an in-memory SQLite database. To run against
a real PostgreSQL instance, set `TEST_DATABASE_URL` before calling pytest:

```powershell
# Create the test database first:
$env:PGPASSWORD="yourpassword"
psql -U postgres -c "CREATE DATABASE reconcile_test;"

# Run the full suite against it:
$env:TEST_DATABASE_URL="postgresql://postgres:yourpassword@localhost:5432/reconcile_test"
pytest -v
```

Every test uses a fresh `init_db()` call that creates all tables if they
don't already exist. The test database is not cleaned up between runs;
drop and recreate it if you need a clean slate.

### Admin account password

On first startup, `seed_admin_user()` is called by `init_db()`:

- If `ADMIN_PASSWORD` is set in `.env`, that value is used.
- If `ADMIN_PASSWORD` is **not set or empty**, a 24-character cryptographically
  random password is generated, printed **once** to the server log, and never
  stored anywhere else.

**Rotate the password immediately after the first login**, regardless of which
path created it.
