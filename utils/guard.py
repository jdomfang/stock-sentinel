from __future__ import annotations

import streamlit as st

from utils.auth import is_logged_in
from utils.profile import get_my_profile


def require_login(*, after_auth_page: str = "Home") -> None:
    if not is_logged_in():
        st.markdown(
            """
            <div style="
              border:1px solid rgba(56,189,248,.25);
              border-radius:18px;
              padding:36px 28px;
              background:linear-gradient(180deg,rgba(56,189,248,.05),rgba(15,23,42,.80));
              text-align:center;
              margin:2rem auto;
              max-width:480px;
            ">
              <div style="font-size:2rem;margin-bottom:10px;">🔒</div>
              <div style="font-size:1.15rem;font-weight:800;color:rgba(248,250,252,.98);margin-bottom:6px;">Sign in to continue</div>
              <div style="color:rgba(148,163,184,.80);font-size:0.92rem;line-height:1.5;max-width:320px;margin:0 auto 20px auto;">
                Create a free account or log in to run scans and access market signals.
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        col_a, col_b, col_c = st.columns([1.5, 1, 1.5])
        with col_b:
            if st.button("Log in →", type="primary", use_container_width=True, key="guard_login_button"):
                st.session_state["auth_initial_mode"] = "Sign In"
                st.session_state["_after_auth_page"] = after_auth_page
                st.switch_page("pages/Auth.py")
        st.stop()


def require_active_account(*, after_auth_page: str = "Home") -> dict:
    """Ensure the user is logged in and not disabled; returns profile."""
    require_login(after_auth_page=after_auth_page)
    prof = get_my_profile()
    if not prof:
        st.error("Account profile not found. Try logging out/in.")
        st.stop()
    if prof.get("disabled"):
        st.error("Your account is disabled. Contact support.")
        st.stop()
    return prof
