"""
Landing page (public) — Google Sign-In only.

Manual email/password/OTP sign-in and sign-up have been removed for the demo.
Token storage: FastAPI sets the reconcile_session cookie (HttpOnly; SameSite=Lax)
directly in its response headers, and also passes the token via a one-time URL
query parameter that streamlit_app.py captures on redirect back from Google.
"""
from __future__ import annotations

import os

import requests
import streamlit as st
import streamlit.components.v1 as components

API_URL = os.getenv("RECONCILE_API_URL", "http://localhost:8000")

st.markdown(
    '<div class="hero"><div class="eyebrow" role="doc-subtitle">Agentic bookkeeping • phase 01</div>'
    '<h1>Reconcile</h1>'
    '<p>Sign in to access your financial reconciliation control room.</p></div>',
    unsafe_allow_html=True,
)

# SEO meta injection (best-effort inside Streamlit iframe)
components.html(
    """<meta name="description" content="Reconcile — agentic bookkeeping control room. Ingests bank transactions, categorises with AI, posts to ledger.">
<meta name="robots" content="noindex, nofollow">
<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"WebPage","name":"Reconcile","description":"Agentic bookkeeping control room"}
</script>""",
    height=0,
)

col1, col2, col3 = st.columns([1, 2, 1])

# Parse URL state (error/success messages can still arrive via redirect)
qp = st.query_params
error_msg = qp.get("error")
success_msg = qp.get("success")

with col2:
    if error_msg:
        st.error(error_msg)
    if success_msg:
        st.success(success_msg)

    st.subheader("Sign In")

    if st.button("Sign in with Google", use_container_width=True):
        try:
            resp = requests.get(f"{API_URL}/auth/google/login", timeout=5)
            if resp.status_code == 200:
                auth_url = resp.json()["auth_url"]
                st.markdown(
                    f'<meta http-equiv="refresh" content="0;url={auth_url}">',
                    unsafe_allow_html=True,
                )
            else:
                st.error(f"Google auth unavailable: {resp.json().get('detail', '')}")
        except Exception as exc:
            st.error(f"Error connecting to Google auth: {exc}")