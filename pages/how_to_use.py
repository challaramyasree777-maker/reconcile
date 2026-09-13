import streamlit as st

st.title("How to Use Reconcile")

try:
    with open("USAGE.md", "r", encoding="utf-8") as f:
        st.markdown(f.read())
except FileNotFoundError:
    st.error("USAGE.md not found.")
