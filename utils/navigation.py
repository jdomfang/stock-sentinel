"""Streamlit navigation helpers."""

from __future__ import annotations

import streamlit as st


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
        /* Make buttons in the top nav look more like a real navbar (compact like LunarCrush) */
        .clawd-topnav [data-testid="stButton"] > button {
          border-radius: 999px;
          padding: 0.20rem 0.55rem;
          font-size: 0.82rem;
          letter-spacing: -0.01em;
          border: 1px solid rgba(148,163,184,0.20);
          background: rgba(15,23,42,0.66);
          min-height: 36px;
          line-height: 1;
        }

        /* Primary (Log in) — slightly more premium, less "bulky" */
        .clawd-topnav button[data-testid="stBaseButton-primary"],
        .clawd-topnav .stButton > button[kind="primary"] {
          min-height: 36px !important;
          padding: 0.20rem 0.62rem !important;
          font-size: 0.82rem !important;
          font-weight: 750 !important;
          border-radius: 999px !important;
          box-shadow: 0 10px 24px rgba(0,0,0,.22);
        }

        /* Reduce extra vertical whitespace under the nav row */
        .clawd-topnav {
          margin-bottom: 0.15rem;
        }

        /* Match Discovery page select styling (inputs) */
        .clawd-topnav [data-baseweb="select"] > div {
          border-radius: 999px !important;
          background-color: rgba(2,6,23,.55) !important;
          border-color: rgba(148,163,184,0.18) !important;
          color: #E5E7EB !important;
          min-height: 36px !important;
        }
        .clawd-topnav [data-baseweb="select"] * {
          color: #E5E7EB !important;
          opacity: 1 !important;
        }
        
        /* Improve placeholder text visibility */
        .clawd-topnav [data-baseweb="select"] [role="button"] {
          color: #E5E7EB !important;
          font-weight: 600 !important;
          opacity: 1 !important;
        }
        .clawd-topnav [data-baseweb="select"] [role="button"]::placeholder {
          color: #E5E7EB !important;
          opacity: 1 !important;
        }
        .clawd-topnav [data-baseweb="select"] span {
          color: #E5E7EB !important;
          font-size: 0.92rem !important;
          font-weight: 600 !important;
          opacity: 1 !important;
        }
        .clawd-topnav [data-baseweb="select"] * {
          opacity: 1 !important;
        }

        /* Dropdown menu (options) readability — match Discovery */
        [data-baseweb="popover"] {
          z-index: 10050 !important;
        }

        ul[data-testid="stSelectboxVirtualDropdown"],
        [data-testid="stSelectboxVirtualDropdown"] {
          background: #0F172A !important;
          background-color: #0F172A !important;
          border: 1px solid rgba(148,163,184,0.18) !important;
          border-radius: 14px !important;
          box-shadow: 0 16px 40px rgba(0,0,0,.45) !important;
        }

        ul[data-testid="stSelectboxVirtualDropdown"] li {
          background: transparent !important;
          background-color: transparent !important;
          color: #E5E7EB !important;
          opacity: 1 !important;
        }
        ul[data-testid="stSelectboxVirtualDropdown"] li:hover {
          background: rgba(56,189,248,.16) !important;
          background-color: rgba(56,189,248,.16) !important;
        }
        ul[data-testid="stSelectboxVirtualDropdown"] li *,
        ul[data-testid="stSelectboxVirtualDropdown"] * {
          color: #E5E7EB !important;
          opacity: 1 !important;
        }

        /* Tighten spacing between columns inside the nav row */
        .clawd-topnav [data-testid="stHorizontalBlock"] {
          gap: 0.22rem;
        }
        
        /* Reduce vertical margin below nav to close gap with hero */
        .clawd-topnav {
          /* Pull the whole nav row closer to the top */
          margin-top: -0.6rem !important;

          /* Pull the page content up under the nav */
          margin-bottom: -4.0rem !important;
          padding-bottom: 0 !important;
        }
        
        /* Services selectbox: compact (LunarCrush-style) */
        .clawd-topnav [data-testid="stSelectbox"] {
          max-width: 210px !important;
          min-width: 180px !important;
        }
        .clawd-topnav [data-baseweb="select"] {
          width: 100% !important;
          max-width: 210px !important;
        }
        
        /* Shrink Login button to 50% width */
        .clawd-topnav [data-testid="stButton"] {
          flex-grow: 0 !important;
        }
        .clawd-topnav [data-testid="stButton"] > button {
          width: auto !important;
          padding: 0.22rem 0.4rem !important;
          font-size: 0.80rem !important;
          min-height: 32px !important;
          white-space: nowrap !important;
        }
        .clawd-topnav [data-testid="stButton"] > button:hover {
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
            spacer_col, credits_col, admin_col, services_col, auth_col = st.columns(
                [6.0, 1.15, 0.55, 1.45, 0.75]
            )
        else:
            # Slightly narrower Services column so it hugs the auth button like LunarCrush
            spacer_col, services_col, auth_col = st.columns([8.0, 1.25, 0.9])

        with spacer_col:
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

        # Services dropdown (always present, always adjacent to auth)
        with services_col:
            choice = st.selectbox(
                "Services",
                options=["Discover", "Deep Analyze"],
                index=None,
                placeholder="Services",
                label_visibility="collapsed",
                key="topnav_services",
            )

            if choice == "Discover":
                st.switch_page("pages/Discovery.py" if is_logged_in() else "pages/Auth.py")
            elif choice == "Deep Analyze":
                st.switch_page("pages/Deep_Analysis.py" if is_logged_in() else "pages/Auth.py")

        # Auth button (right-most)
        with auth_col:
            if is_logged_in():
                if st.button("Log out", use_container_width=True):
                    sign_out()
                    st.switch_page("pages/Home.py")
            else:
                if st.button("Log in", use_container_width=True, type="primary"):
                    st.switch_page("pages/Auth.py")

        st.markdown('</div>', unsafe_allow_html=True)

    # JS: dropdown readability fix + Services selectbox visibility (match Discovery).
    import streamlit.components.v1 as components

    components.html(
        """
        <script>
        (function () {
          const applyDropdownTheme = (doc) => {
            // Fix dropdown menu visibility
            const ul = doc.querySelector('ul[data-testid="stSelectboxVirtualDropdown"]');
            if (ul) {
              ul.style.setProperty('background-color', '#0F172A', 'important');
              ul.style.setProperty('color', '#E5E7EB', 'important');
              ul.style.setProperty('border', '1px solid rgba(148,163,184,0.18)', 'important');
              ul.style.setProperty('border-radius', '14px', 'important');
              ul.querySelectorAll('li, li *').forEach((el) => {
                el.style.setProperty('color', '#E5E7EB', 'important');
                el.style.setProperty('opacity', '1', 'important');
              });
            }
            
            // Ensure Services selectbox placeholder is visible
            const serviceSelectboxes = doc.querySelectorAll('[data-testid="stSelectbox"]');
            serviceSelectboxes.forEach((select) => {
              // Target the button (dropdown trigger)
              const button = select.querySelector('[role="button"]');
              if (button) {
                button.style.setProperty('color', '#E5E7EB', 'important');
                button.style.setProperty('opacity', '1', 'important');
                button.style.setProperty('font-weight', '600', 'important');
              }
              // Target all text nodes and spans recursively
              const allElements = select.querySelectorAll('*');
              allElements.forEach((el) => {
                el.style.setProperty('color', '#E5E7EB', 'important');
                el.style.setProperty('opacity', '1', 'important');
                if (el.tagName === 'SPAN' || el.tagName === 'DIV') {
                  el.style.setProperty('font-weight', '600', 'important');
                }
              });
            });
          };

          const applyAll = () => {
            try { applyDropdownTheme(document); } catch (e) {}
            try {
              if (window.parent && window.parent.document) {
                applyDropdownTheme(window.parent.document);
              }
            } catch (e) {}
          };

          const obs = new MutationObserver(() => applyAll());
          obs.observe(document.documentElement, { childList: true, subtree: true });
          window.addEventListener('load', applyAll);
          setTimeout(applyAll, 250);
          setTimeout(applyAll, 1000);
        })();
        </script>
        """,
        height=0,
    )

    # No extra spacer after nav (prevents big gap before the hero)
