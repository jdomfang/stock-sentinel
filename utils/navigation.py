"""Streamlit navigation helpers."""

from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as st_components


def render_sidebar_navigation() -> None:
    """Hide Streamlit's sidebar/navigation for a cleaner, focused layout."""
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"],
        [data-testid="stSidebarNav"] {
            display: none;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_top_nav() -> None:
    """Render a LunarCrush-style top nav header.

    Streamlit has no global layout template, so call this at the top of every page.

    Notes:
    - This nav is made *sticky* via a small JS helper (reliable across Streamlit versions).
    - We keep it simple: Streamlit buttons + switch_page.
    """
    from utils.auth import is_logged_in, sign_out, get_user
    from utils.supabase_client import get_client

    # Anchor so we can locate this block in the DOM and make it sticky.
    st.markdown('<div id="clawd-topnav-anchor"></div>', unsafe_allow_html=True)

    # Nav styling (background + separation)
    st.markdown(
        """
        <style>
        /* Make buttons in the top nav look more like a real navbar (compact like LunarCrush)
           Note: nav links override this via .clawd-navlink.
        */
        .clawd-topnav [data-testid="stButton"] > button {
          border-radius: 999px;
          padding: 0.16rem 0.42rem;
          font-size: 0.80rem;
          letter-spacing: -0.01em;
          border: 1px solid rgba(148,163,184,0.18);
          background: rgba(15,23,42,0.62);
          min-height: 32px;
          line-height: 1;
          width: fit-content !important;
          white-space: nowrap !important;
        }

        /* Prevent iPad/Safari from wrapping the auth label vertically */
        .clawd-topnav button[data-testid="stBaseButton-primary"],
        .clawd-topnav .stButton > button[kind="primary"] {
          white-space: nowrap !important;
          min-width: 86px !important;
        }

        /* Primary (Log in) — slightly more premium, less "bulky" */
        .clawd-topnav button[data-testid="stBaseButton-primary"],
        .clawd-topnav .stButton > button[kind="primary"] {
          min-height: 32px !important;
          padding: 0.16rem 0.52rem !important;
          font-size: 0.80rem !important;
          font-weight: 750 !important;
          border-radius: 999px !important;
          box-shadow: 0 10px 24px rgba(0,0,0,.18);
          width: fit-content !important;
        }

        /* Reduce extra vertical whitespace under the nav row */
        .clawd-topnav {
          margin-bottom: 0.15rem;
        }

        /* Keep Market Scan + Analyze a Stock closer together */
        .clawd-topnav [data-testid="stHorizontalBlock"] {
          gap: 0.20rem !important;
        }

        /* Align nav items to the right edge of their columns so spacing is consistent */
        .clawd-topnav .clawd-navlink,
        .clawd-topnav .clawd-auth {
          display: flex;
          justify-content: flex-end;
          align-items: center;
          width: 100%;
        }

        /* Create a slightly larger gap between the nav links and the CTA */
        .clawd-topnav .clawd-auth {
          margin-left: 0.55rem;
        }

        /* Top-nav link buttons (Market Scan / Analyze a Stock):
           We can't reliably "wrap" Streamlit elements with HTML, so we target by key.
        */
        .st-key-nav_discover [data-testid="stButton"] > button,
        .st-key-nav_deep [data-testid="stButton"] > button {
          background: transparent !important;
          background-color: transparent !important;
          border: 0 !important;
          border-color: transparent !important;
          outline: 0 !important;
          box-shadow: none !important;
          padding: 0.10rem 0.10rem !important;
          min-height: 32px !important;
          color: rgba(229,231,235,.92) !important;
          font-weight: 750 !important;
          font-size: 0.86rem !important;
          letter-spacing: -0.01em;
          white-space: nowrap !important;
        }
        .st-key-nav_discover [data-testid="stButton"] > button:hover,
        .st-key-nav_deep [data-testid="stButton"] > button:hover {
          background: transparent !important;
          background-color: transparent !important;
          border-color: transparent !important;
          text-decoration: underline;
          text-underline-offset: 4px;
          text-decoration-color: rgba(56,189,248,.55);
        }
        .st-key-nav_discover [data-testid="stButton"] > button:focus,
        .st-key-nav_deep [data-testid="stButton"] > button:focus,
        .st-key-nav_discover [data-testid="stButton"] > button:focus-visible,
        .st-key-nav_deep [data-testid="stButton"] > button:focus-visible {
          outline: 0 !important;
          border-color: transparent !important;
          box-shadow: none !important;
        }

        /* Ensure wrapper doesn't contribute a hairline outline */
        .st-key-nav_discover [data-testid="stButton"],
        .st-key-nav_deep [data-testid="stButton"] {
          border: 0 !important;
          outline: 0 !important;
          background: transparent !important;
          box-shadow: none !important;
        }
        .clawd-topnav .clawd-navlink [data-testid="stButton"] > button:hover {
          background: transparent !important;
          background-color: transparent !important;
          border-color: transparent !important;
          color: rgba(229,231,235,1) !important;
          text-decoration: underline;
          text-underline-offset: 4px;
          text-decoration-color: rgba(56,189,248,.55);
        }
        .clawd-topnav .clawd-navlink [data-testid="stButton"] > button:focus,
        .clawd-topnav .clawd-navlink [data-testid="stButton"] > button:focus-visible {
          outline: none !important;
          box-shadow: none !important;
          border-color: transparent !important;
        }

        /* Ensure global topnav button styling doesn't re-add pill borders to nav links */
        .clawd-topnav .clawd-navlink [data-testid="stButton"] > button {
          border-radius: 10px !important;
          border: none !important;
        }

        /* Services dropdown (native Streamlit selectbox) — tighten empty space before caret */
        .clawd-topnav .clawd-services {
          display: inline-flex;
          justify-content: flex-end;
          align-items: center;
        }

        /* (1) Make the control narrower so there's less gap between label and caret */
        .clawd-topnav .clawd-services [data-testid="stSelectbox"] {
          min-width: 150px !important;
          max-width: 170px !important;
        }
        .clawd-topnav .clawd-services [data-baseweb="select"] {
          width: 100% !important;
          max-width: 170px !important;
        }

        /* (2) Reduce internal padding so caret feels closer */
        .clawd-topnav .clawd-services [data-baseweb="select"] > div {
          border-radius: 999px !important;
          background-color: rgba(2,6,23,.52) !important;
          border-color: rgba(148,163,184,0.16) !important;
          min-height: 32px !important;
          padding-left: 10px !important;
          padding-right: 10px !important;
        }
        /* Make sure the word "Services" is readable (some browsers dim placeholder text) */
        .clawd-topnav .clawd-services [data-baseweb="select"] [role="button"],
        .clawd-topnav .clawd-services [data-baseweb="select"] [role="button"] * {
          color: rgba(229,231,235,.95) !important;
          -webkit-text-fill-color: rgba(229,231,235,.95) !important; /* Safari/Chrome */
          opacity: 1 !important;
          font-weight: 700 !important;
        }
        .clawd-topnav .clawd-services [data-baseweb="select"] [role="button"] {
          padding-right: 8px !important;
        }
        /* Some BaseWeb builds mark placeholder/value with lower-opacity styles */
        .clawd-topnav .clawd-services [data-baseweb="select"] [aria-selected],
        .clawd-topnav .clawd-services [data-baseweb="select"] [data-baseweb="tag"],
        .clawd-topnav .clawd-services [data-baseweb="select"] [data-baseweb="select"] {
          opacity: 1 !important;
        }

        /* Tablet: give Services a touch more width so it doesn't look cramped */
        @media (min-width: 700px) and (max-width: 1024px) {
          .clawd-topnav .clawd-services [data-testid="stSelectbox"] {
            min-width: 170px !important;
            max-width: 190px !important;
          }
          .clawd-topnav .clawd-services [data-baseweb="select"] {
            max-width: 190px !important;
          }
        }

        /* Tighten spacing between columns inside the nav row + vertically align controls */
        .clawd-topnav [data-testid="stHorizontalBlock"] {
          gap: 0.12rem;
          align-items: center !important;
        }
        
        /* Reduce vertical margin below nav to close gap with hero */
        .clawd-topnav {
          /* Pull the whole nav row closer to the top */
          margin-top: -0.6rem !important;

          /* Pull the page content up under the nav */
          margin-bottom: -4.0rem !important;
          padding-bottom: 0 !important;
        }
        
        /* (Services is a nav-link dropdown) */
        
        /* Auth button wrapper (keeps Login styling without affecting Services) */
        .clawd-topnav .clawd-auth [data-testid="stButton"] {
          flex-grow: 0 !important;
          margin-left: -9px; /* pull CTA slightly left to match premium spacing */
        }

        /* Keep hover on non-primary buttons, but scope it away from Services link trigger */
        .clawd-topnav :not(.clawd-services) [data-testid="stButton"] > button:hover {
          border-color: rgba(56,189,248,0.55);
          background: rgba(15,23,42,0.95);
        }

        .clawd-topnav .brand [data-testid="stButton"] > button {
          font-weight: 800;
          letter-spacing: -0.01em;
          border-color: rgba(56,189,248,0.28);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    nav = st.container()
    with nav:
        st.markdown('<div class="clawd-topnav">', unsafe_allow_html=True)

        # Keep header minimal: Services dropdown immediately adjacent to Log in/Log out.
        # Important: if we reserve empty columns (credits/admin) while logged out,
        # Services will NOT sit next to the auth button. So we render two layouts.

        @st.cache_data(ttl=2, show_spinner=False)
        def _load_credit_counts(user_id: str) -> tuple[int, int] | None:
            """Return (scan_credits, deep_credits) for the given user_id.

            Cached briefly to avoid hammering Supabase on reruns.
            """
            if not user_id:
                return None
            sb = get_client()
            resp = (
                sb.table("profiles")
                .select("scan_credits,deep_credits")
                .eq("user_id", user_id)
                .maybe_single()
                .execute()
            )
            data = getattr(resp, "data", None) or None
            if not data:
                return None
            return int(data.get("scan_credits") or 0), int(data.get("deep_credits") or 0)

        if is_logged_in():
            # Tune column widths so nav links sit closer together (premium spacing) while keeping labels single-line.
            # Add a tiny gap column before the CTA for a consistent nav→CTA separation.
            spacer_col, credits_col, admin_col, discover_col, deep_col, gap_col, auth_col = st.columns(
                [5.90, 1.15, 0.55, 0.55, 0.70, 0.02, 0.65]
            )
        else:
            # Logged-out: tune widths so Market Scan + Analyze a Stock sit closer together.
            spacer_col, discover_col, deep_col, gap_col, auth_col = st.columns([7.97, 0.55, 0.70, 0.02, 0.68])

        with spacer_col:
            st.markdown("")

        # Fixed gap before CTA (keeps nav→CTA spacing consistent)
        if 'gap_col' in locals():
            with gap_col:
                st.markdown("")

        # Logged-in extras
        if is_logged_in():
            with credits_col:
                user = get_user() or {}
                uid = (user.get("id") if isinstance(user, dict) else getattr(user, "id", None)) or ""
                try:
                    counts = _load_credit_counts(uid)
                except Exception:
                    counts = None

                if counts:
                    scan_c, deep_c = counts
                    st.markdown(
                        f"""
                        <div style="
                          display: inline-block;
                          padding: 6px 10px;
                          border-radius: 999px;
                          border: 1px solid rgba(148,163,184,0.22);
                          background: rgba(15,23,42,0.70);
                          color: rgba(229,231,235,0.92);
                          font-size: 0.78rem;
                          font-weight: 650;
                          text-align: center;
                          width: 100%;
                        ">
                          Scan: {scan_c} | Deep: {deep_c}
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown("")

            with admin_col:
                user = get_user() or {}
                user_email = (user.get("email") if isinstance(user, dict) else getattr(user, "email", None)) or ""
                admin_email = st.secrets.get("ADMIN_EMAIL", "").lower().strip()

                if user_email.lower().strip() == admin_email and admin_email:
                    if st.button("🛠️", use_container_width=True, help="Admin Dashboard"):
                        st.switch_page("pages/Admin.py")

        # Top-nav links (no dropdown)
        with discover_col:
            st.markdown('<div class="clawd-navlink">', unsafe_allow_html=True)
            if st.button("Market Scan", use_container_width=False, key="nav_discover"):
                st.switch_page("pages/Discovery.py" if is_logged_in() else "pages/Auth.py")
            st.markdown('</div>', unsafe_allow_html=True)

        with deep_col:
            st.markdown('<div class="clawd-navlink">', unsafe_allow_html=True)
            if st.button("Analyze a Stock", use_container_width=False, key="nav_deep"):
                st.switch_page("pages/Deep_Analysis.py" if is_logged_in() else "pages/Auth.py")
            st.markdown('</div>', unsafe_allow_html=True)

        # Auth button (right-most)        # Auth button (right-most)
        with auth_col:
            st.markdown('<div class="clawd-auth">', unsafe_allow_html=True)
            if is_logged_in():
                if st.button("Log out", use_container_width=False):
                    sign_out()
                    st.switch_page("pages/Home.py")
            else:
                if st.button("Log in", use_container_width=False, type="primary"):
                    st.switch_page("pages/Auth.py")
            st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('</div>', unsafe_allow_html=True)

        # JS: hide the sentinel "Services" row in the dropdown menu (so menu shows only real destinations)

    # JS: force selectbox placeholder/value readable + keep dropdown themed (the earlier working approach)

    # No extra spacer after nav (prevents big gap before the hero)
