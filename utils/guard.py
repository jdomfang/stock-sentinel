from __future__ import annotations

import streamlit as st

from utils.auth import is_logged_in
from utils.profile import get_my_profile


def require_login() -> None:
    if not is_logged_in():
        st.warning("Please log in to continue.")
        if st.button("Log in", type="primary"):
            st.switch_page("pages/Auth.py")
        st.stop()


def require_active_account() -> dict:
    """Ensure the user is logged in and not disabled; returns profile."""
    require_login()
    prof = get_my_profile()
    if not prof:
        st.error("Account profile not found. Try logging out/in.")
        st.stop()
    if prof.get("disabled"):
        st.error("Your account is disabled. Contact support.")
        st.stop()
    return prof
