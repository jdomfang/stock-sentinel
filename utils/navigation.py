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
        /* Make buttons in the top nav look more like a real navbar (compact like LunarCrush) */
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

        /* Dropdown menu (options) readability — match Discovery */
        [data-baseweb="popover"] {
          z-index: 10050 !important;
        }

        ul[data-testid="stSelectboxVirtualDropdown"],
        [data-testid="stSelectboxVirtualDropdown"] {
          background: rgba(15,23,42,0.98) !important;
          background-color: rgba(15,23,42,0.98) !important;
          border: 1px solid rgba(148,163,184,0.18) !important;
          border-radius: 12px !important;
          box-shadow: 0 14px 36px rgba(0,0,0,.46) !important;
          padding: 4px !important;
          min-width: 228px !important; /* avoid oversized detached panel */
        }

        ul[data-testid="stSelectboxVirtualDropdown"] li {
          background: transparent !important;
          background-color: transparent !important;
          color: #E5E7EB !important;
          opacity: 1 !important;
          border-radius: 10px !important;
          padding: 0 !important; /* remove BaseWeb li padding so rows don't feel "puffy" */
          margin: 0 !important;
          font-size: 0.90rem !important;
          line-height: 1.18 !important;
          white-space: nowrap !important;
        }
        ul[data-testid="stSelectboxVirtualDropdown"] li > div,
        ul[data-testid="stSelectboxVirtualDropdown"] li > div > div {
          padding: 6px 8px !important;
          border-radius: 10px !important;
        }
        ul[data-testid="stSelectboxVirtualDropdown"] li:hover {
          background: rgba(56,189,248,.14) !important;
          background-color: rgba(56,189,248,.14) !important;
        }
        ul[data-testid="stSelectboxVirtualDropdown"] li *,
        ul[data-testid="stSelectboxVirtualDropdown"] * {
          color: #E5E7EB !important;
          opacity: 1 !important;
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
            spacer_col, credits_col, admin_col, services_col, auth_col = st.columns(
                [6.0, 1.15, 0.55, 1.05, 0.75]
            )
        else:
            # Logged-out: right cluster should be tight (Services dropdown + Login pill).
            spacer_col, services_col, auth_col = st.columns([8.55, 0.70, 0.75])

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

        # Services dropdown (native Streamlit selectbox for reliable dropdown behavior)
        with services_col:
            st.markdown('<div class="clawd-services">', unsafe_allow_html=True)

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

            st.markdown("</div>", unsafe_allow_html=True)

        # Auth button (right-most)
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

    # JS: force Services trigger readability (matches the earlier working fix)
    # We scope to the top nav container so we don't affect other selectboxes.
    st_components.html(
        """
        <script>
        (function () {
          const APPLY_TO = (doc) => {
            const nav = doc.querySelector('.clawd-topnav');
            if (!nav) return;

            // Find auth button (Log in / Log out) and then select the nearest listbox trigger to its left.
            const buttons = Array.from(nav.querySelectorAll('button'));
            const authBtn = buttons.find(b => (b.innerText || '').trim() === 'Log in')
                        || buttons.find(b => (b.innerText || '').trim() === 'Log out');
            if (!authBtn) return;
            const authRect = authBtn.getBoundingClientRect();

            const listboxTriggers = Array.from(nav.querySelectorAll('[role="button"][aria-haspopup="listbox"], button[aria-haspopup="listbox"]'))
              .filter(el => el && el.offsetParent !== null);
            if (!listboxTriggers.length) return;

            // Choose the trigger immediately to the left of auth button.
            let best = null;
            let bestDist = Infinity;
            for (const t of listboxTriggers) {
              const r = t.getBoundingClientRect();
              const dist = authRect.left - r.right;
              if (dist >= -4 && dist < bestDist) { // allow tiny overlap
                bestDist = dist;
                best = t;
              }
            }
            const trigger = best || listboxTriggers[0];

            const force = (el) => {
              if (!el) return;
              el.style.setProperty('color', '#E5E7EB', 'important');
              el.style.setProperty('opacity', '1', 'important');
              el.style.setProperty('font-weight', '700', 'important');
              el.style.setProperty('-webkit-text-fill-color', '#E5E7EB', 'important');
            };

            force(trigger);
            trigger.querySelectorAll('*').forEach((el) => {
              force(el);
              el.style.setProperty('fill', '#E5E7EB', 'important');
            });
          };

          const APPLY = () => {
            try { APPLY_TO(document); } catch (e) {}
            try {
              if (window.parent && window.parent.document) {
                APPLY_TO(window.parent.document);
              }
            } catch (e) {}
          };

          const obs = new MutationObserver(() => APPLY());
          obs.observe(document.documentElement, { childList: true, subtree: true });
          window.addEventListener('load', APPLY);
          setTimeout(APPLY, 50);
          setTimeout(APPLY, 250);
          setTimeout(APPLY, 1000);
          setInterval(APPLY, 300);
        })();
        </script>
        """,
        height=0,
    )

    # No extra spacer after nav (prevents big gap before the hero)
