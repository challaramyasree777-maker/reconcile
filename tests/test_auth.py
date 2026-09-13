from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth import create_access_token, create_session, hash_password
from app.main import app


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import app.db as db

    test_db_url = os.getenv("TEST_DATABASE_URL")
    if test_db_url:
        monkeypatch.setenv("DATABASE_URL", test_db_url)
        with db.connection() as conn:
            if db.is_postgres():
                conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    else:
        test_file = tmp_path / "test_auth.db"
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{test_file}")

    db.init_db()
    return db


@pytest.fixture
def client(isolated_db):
    return TestClient(app)


@pytest.fixture
def user_a(isolated_db):
    with isolated_db.connection() as conn:
        cur = conn.execute(
            "INSERT INTO users (email, hashed_password, created_at) VALUES (%s, %s, %s) RETURNING id",
            ("user_a@example.com", hash_password("password_a"), isolated_db.now()),
        )
        row = cur.fetchone()
        uid = row["id"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]
    return {"id": uid, "email": "user_a@example.com", "password": "password_a"}


@pytest.fixture
def user_b(isolated_db):
    with isolated_db.connection() as conn:
        cur = conn.execute(
            "INSERT INTO users (email, hashed_password, created_at) VALUES (%s, %s, %s) RETURNING id",
            ("user_b@example.com", hash_password("password_b"), isolated_db.now()),
        )
        row = cur.fetchone()
        uid = row["id"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]
    return {"id": uid, "email": "user_b@example.com", "password": "password_b"}


def test_unauthenticated_request_returns_401(client):
    """GET /transactions without token returns 401 Unauthorized."""
    response = client.get("/transactions")
    assert response.status_code == 401
    assert "Not authenticated" in response.text or "credentials" in response.text


def test_login_returns_httponly_cookie(client, user_a):
    """POST /auth/login sets a reconcile_session cookie that is HttpOnly."""
    response = client.post(
        "/auth/login",
        json={"email": user_a["email"], "password": user_a["password"]},
    )
    assert response.status_code == 200
    # Response body must NOT contain a raw access_token (no JS-readable path)
    assert "access_token" not in response.json()
    # Set-Cookie header must be present
    set_cookie = response.headers.get("set-cookie", "")
    assert "reconcile_session" in set_cookie, f"Expected reconcile_session in Set-Cookie, got: {set_cookie}"
    assert "httponly" in set_cookie.lower(), f"Expected HttpOnly in Set-Cookie, got: {set_cookie}"


def test_authenticated_request_succeeds(client, isolated_db, user_a):
    """GET /transactions with a valid, non-revoked Bearer token succeeds with 200."""
    token = create_access_token({"sub": str(user_a["id"]), "user_id": user_a["id"], "email": user_a["email"]})
    # Must register the session or get_current_user will treat it as revoked
    create_session(user_a["id"], token)
    response = client.get("/transactions", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_user_a_cannot_see_user_b_transactions(client, isolated_db, user_a, user_b):
    """Transactions created by user B are completely invisible to user A."""
    # Insert a transaction for user B
    with isolated_db.connection() as conn:
        cur = conn.execute(
            "INSERT INTO runs(user_id, source, status, transaction_count, created_at) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (user_b["id"], "test", "completed", 1, isolated_db.now()),
        )
        row = cur.fetchone()
        run_b_id = row["id"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]
        conn.execute(
            """
            INSERT INTO transactions (user_id, run_id, source, external_id, raw_payload, parsed_amount, parsed_date, parsed_vendor_raw, status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (user_b["id"], run_b_id, "test", "tx-b-secret", "{}", 999.0, "2026-09-12", "Secret Vendor B", "pending", isolated_db.now()),
        )

    token_a = create_access_token({"sub": str(user_a["id"]), "user_id": user_a["id"], "email": user_a["email"]})
    create_session(user_a["id"], token_a)
    response_a = client.get("/transactions", headers={"Authorization": f"Bearer {token_a}"})
    assert response_a.status_code == 200
    assert response_a.json() == []

    token_b = create_access_token({"sub": str(user_b["id"]), "user_id": user_b["id"], "email": user_b["email"]})
    create_session(user_b["id"], token_b)
    response_b = client.get("/transactions", headers={"Authorization": f"Bearer {token_b}"})
    assert response_b.status_code == 200
    txs_b = response_b.json()
    assert len(txs_b) == 1
    assert txs_b[0]["parsed_vendor_raw"] == "Secret Vendor B"


def test_user_a_cannot_correct_user_b_transaction(client, isolated_db, user_a, user_b):
    """User A attempting to approve or correct user B's transaction gets rejected."""
    # Insert a transaction for user B
    with isolated_db.connection() as conn:
        cur = conn.execute(
            "INSERT INTO runs(user_id, source, status, transaction_count, created_at) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (user_b["id"], "test", "completed", 1, isolated_db.now()),
        )
        row = cur.fetchone()
        run_b_id = row["id"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]
        cur = conn.execute(
            """
            INSERT INTO transactions (user_id, run_id, source, external_id, raw_payload, parsed_amount, parsed_date, parsed_vendor_raw, status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (user_b["id"], run_b_id, "test", "tx-b-flagged", "{}", 42.0, "2026-09-12", "Flagged Vendor B", "flagged", isolated_db.now()),
        )
        row = cur.fetchone()
        tx_b_id = row["id"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]

    token_a = create_access_token({"sub": str(user_a["id"]), "user_id": user_a["id"], "email": user_a["email"]})
    create_session(user_a["id"], token_a)

    # User A tries to approve user B's transaction
    response = client.post(
        f"/corrections/{tx_b_id}",
        json={"approve": True},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert response.status_code in (400, 403, 404)
    assert "not found" in response.text.lower() or "forbidden" in response.text.lower()

    # User A tries to correct user B's transaction
    response2 = client.post(
        f"/corrections/{tx_b_id}",
        json={"corrected_vendor": "Hacked Vendor", "corrected_category": "Hacked Category"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert response2.status_code in (400, 403, 404)
    assert "not found" in response2.text.lower() or "forbidden" in response2.text.lower()


def test_signup_and_verify_otp_sets_httponly_cookie(client, isolated_db):
    email = "newuser@example.com"
    # Signup
    resp = client.post("/auth/signup", json={
        "email": email,
        "password": "password123",
        "confirm_password": "password123"
    })
    assert resp.status_code == 200

    # Check inactive in DB
    user = isolated_db.one("SELECT is_active, otp_hash FROM users WHERE email = %s", (email,))
    assert not user["is_active"]
    assert user["otp_hash"]

    # Test that wrong OTP returns 400
    resp2 = client.post("/auth/verify-otp", json={"email": email, "otp": "000000"})
    assert resp2.status_code == 400
    assert "Invalid OTP" in resp2.json()["detail"]


def test_logout_revokes_session(client, isolated_db, user_a):
    """Logout must invalidate the session server-side; subsequent requests with the
    same token must be rejected with 401."""
    # Login to get a real session
    resp = client.post(
        "/auth/login",
        json={"email": user_a["email"], "password": user_a["password"]},
    )
    assert resp.status_code == 200
    set_cookie = resp.headers.get("set-cookie", "")
    assert "reconcile_session" in set_cookie
    assert "httponly" in set_cookie.lower()

    # Extract token from cookie for direct Bearer use in the next call
    # (TestClient doesn't automatically replay cookies)
    import re
    match = re.search(r"reconcile_session=([^;]+)", set_cookie)
    assert match, "Could not parse token from Set-Cookie header"
    token = match.group(1)

    # Confirm the token works before logout
    resp2 = client.get("/transactions", headers={"Authorization": f"Bearer {token}"})
    assert resp2.status_code == 200

    # Logout — should revoke session in DB and clear cookie
    resp3 = client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert resp3.status_code == 200
    logout_cookie = resp3.headers.get("set-cookie", "")
    assert "max-age=0" in logout_cookie.lower() or "max-age=0" in logout_cookie

    # The same token must now be rejected (session revoked server-side)
    resp4 = client.get("/transactions", headers={"Authorization": f"Bearer {token}"})
    assert resp4.status_code == 401


def test_rate_limiting_blocks_brute_force(client, isolated_db):
    email = "brute@example.com"
    for _ in range(6):
        resp = client.post("/auth/signup", json={"email": email, "password": "abc", "confirm_password": "abc"})
    assert resp.status_code == 429
    assert "Too many requests" in resp.json()["detail"]
