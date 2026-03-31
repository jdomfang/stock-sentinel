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

    (Hotfix: touched to force Streamlit Cloud redeploy after a transient tokenizer/caching error.)

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

        /* Prevent iPad/Safari from wrapping the auth label vertically (desktop/tablet landscape) */
        @media (min-width: 1025px) {
          .clawd-topnav button[data-testid="stBaseButton-primary"],
          .clawd-topnav .stButton > button[kind="primary"] {
            white-space: nowrap !important;
            min-width: 86px !important;
          }
        }

        /* Tablet/phone: keep CTA compact so it doesn't stack vertically */
        @media (max-width: 1024px) {
          .clawd-topnav button[data-testid="stBaseButton-primary"],
          .clawd-topnav .stButton > button[kind="primary"] {
            white-space: nowrap !important;
            min-width: 64px !important;
            padding: 0.14rem 0.44rem !important;
          }
        }

        /* Primary (Log in) — slightly more premium, less "bulky" */
        .clawd-topnav button[data-testid="stBaseButton-primary"],
        .clawd-topnav .stButton > button[kind="primary"] {
          min-height: 32px !important;
          padding: 0.10rem 0.52rem !important;
          font-size: 0.80rem !important;
          font-weight: 750 !important;
          border-radius: 999px !important;
          box-shadow: 0 10px 24px rgba(0,0,0,.18);
          width: fit-content !important;
          line-height: 1 !important;
        }

        /* Top header */
        .clawd-topnav {
          margin-top: -5.80rem !important;
          margin-bottom: -4.60rem !important;
          padding: 0.20rem 0 0.20rem 0 !important;
          border-bottom: 1px solid rgba(148,163,184,0.18);
          backdrop-filter: blur(16px);
          -webkit-backdrop-filter: blur(16px);
          background: rgba(2,6,23,0.85);
          position: relative;
          z-index: 100;
          box-shadow: 0 1px 0 rgba(56,189,248,0.06);
        }

        /* Mobile nav: replace desktop header with a dedicated fixed mobile topbar */
        @media (max-width: 640px) {
          /* Hide the entire desktop header — it stacks weirdly in Streamlit on iOS/Android */
          .clawd-topnav { display: none !important; }

          /* Also hide any leftover Streamlit button containers from the header */
          .clawd-brand,
          .clawd-navlink,
          .clawd-auth,
          .st-key-nav_home,
          .st-key-nav_discover,
          .st-key-nav_deep,
          .st-key-nav_auth_login,
          .st-key-nav_auth_logout {
            display: none !important;
          }

          /* Give the app content room under the fixed mobile bar */
          div[data-testid="stAppViewContainer"] section.main {
            padding-top: 56px !important;
          }

          /* Fixed mobile bar */
          #clawd-mobile-topbar {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            height: 52px;
            padding: 0 12px;
            display: none;
            align-items: center;
            justify-content: space-between;
            background: rgba(2,6,23,0.88);
            border-bottom: 1px solid rgba(148,163,184,0.18);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            z-index: 100000;
          }
          #clawd-mobile-topbar .brand {
            color: rgba(56,189,248,.95);
            font-weight: 800;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            font-size: 0.78rem;
            white-space: nowrap;
          }

          /* Mobile menu drawer */
          #clawd-mobile-menu {
            position: fixed;
            top: 52px;
            left: 0;
            right: 0;
            display: none;
            flex-direction: column;
            background: rgba(2,6,23,0.97);
            border-bottom: 1px solid rgba(148,163,184,0.18);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            z-index: 100000;
            padding: 8px 0;
          }
          #clawd-mobile-menu.open { display: flex !important; }
          #clawd-mobile-menu a {
            display: block;
            width: 100%;
            padding: 12px 16px;
            color: rgba(229,231,235,.92) !important;
            text-decoration: none;
            font-size: 0.96rem;
            font-weight: 650;
          }
          #clawd-mobile-menu a:active,
          #clawd-mobile-menu a:hover {
            background: rgba(56,189,248,.08);
          }

          #clawd-hamburger {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            background: rgba(148,163,184,0.08);
            border: 1px solid rgba(148,163,184,0.22);
            border-radius: 10px;
            width: 36px;
            height: 36px;
            cursor: pointer;
            color: rgba(229,231,235,.92);
            font-size: 1.1rem;
          }
        }

        @media (min-width: 641px) {
          #clawd-mobile-topbar, #clawd-mobile-menu { display: none !important; }
        }

        .clawd-topnav [data-testid="stHorizontalBlock"] {
          gap: 0.35rem !important;
          align-items: center !important;
        }

        .clawd-topnav .clawd-brand {
          display: flex;
          align-items: center !important;
          justify-content: flex-start;
          width: 100%;
          min-height: 32px;
        }
        .clawd-topnav .clawd-brandtext {
          color: rgba(56,189,248,.95) !important;
          -webkit-text-fill-color: rgba(56,189,248,.95) !important;
          font-weight: 750;
          letter-spacing: 0.06em;
          text-transform: uppercase;
          font-size: 0.78rem;
          line-height: 32px;
          white-space: nowrap;
          margin: 0;
          display: inline-block;
        }

        /* Responsive nav note:
           Do NOT hide Market Scan / Analyze a Stock on mobile unless we actually render a replacement menu.
        */

        /* Align nav items to the right edge of their columns so spacing is consistent */
        .clawd-topnav .clawd-navlink,
        .clawd-topnav .clawd-auth {
          display: flex;
          justify-content: flex-end;
          align-items: center !important;
          width: 100%;
          min-height: 32px;
        }

        /* Keep Home and auth visually adjacent like the mockup */
        .clawd-topnav .clawd-auth {
          margin-left: 0.00rem;
        }

        /* Top-nav link buttons (Market Scan / Analyze a Stock):
           We can't reliably "wrap" Streamlit elements with HTML, so we target by key.
        */
        .st-key-nav_discover [data-testid="stButton"] > button,
        .st-key-nav_deep [data-testid="stButton"] > button {
          background: transparent !important;
          background-color: transparent !important;
          /* keep a 1px border for consistent sizing/spacing, but make it invisible */
          border: 1px solid rgba(0,0,0,0) !important;
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

        /* Home nav link: match the mockup-style text link next to auth */
        .st-key-nav_home [data-testid="stButton"] > button {
          background: transparent !important;
          background-color: transparent !important;
          border: 1px solid rgba(0,0,0,0) !important;
          outline: 0 !important;
          box-shadow: none !important;
          padding: 0.10rem 0.10rem !important;
          min-height: 32px !important;
          color: rgba(229,231,235,.92) !important;
          font-weight: 750 !important;
          font-size: 0.86rem !important;
          letter-spacing: -0.01em;
          white-space: nowrap !important;
          width: fit-content !important;
          min-width: auto !important;
        }
        .st-key-nav_home [data-testid="stButton"] > button:hover {
          background: transparent !important;
          background-color: transparent !important;
          border-color: transparent !important;
          text-decoration: underline;
          text-underline-offset: 4px;
          text-decoration-color: rgba(56,189,248,.55);
        }
        .st-key-nav_home [data-testid="stButton"] > button:focus,
        .st-key-nav_home [data-testid="stButton"] > button:focus-visible {
          outline: 0 !important;
          border-color: transparent !important;
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
          min-height: 32px !important;
          line-height: 1 !important;
          padding-top: 0.10rem !important;
          padding-bottom: 0.10rem !important;
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
            brand_col, spacer_col, credits_col, admin_col, home_col, gap_col, auth_col = st.columns(
                [1.45, 5.36, 1.15, 0.55, 0.50, 0.01, 0.98]
            )
        else:
            brand_col, spacer_col, home_col, gap_col, auth_col = st.columns([1.45, 7.09, 0.46, 0.01, 0.99])

        with brand_col:
            st.markdown('<div class="clawd-brand"><div class="clawd-brandtext"><span>STOCK SENTINEL</span></div></div>', unsafe_allow_html=True)

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

        # Home link (right before auth)
        with home_col:
            st.markdown('<div class="clawd-navlink">', unsafe_allow_html=True)
            if st.button("Home", use_container_width=False, key="nav_home"):
                st.switch_page("pages/Home.py")
            st.markdown('</div>', unsafe_allow_html=True)

        # Auth button (right-most)
        with auth_col:
            st.markdown('<div class="clawd-auth">', unsafe_allow_html=True)
            if is_logged_in():
                if st.button("Log out", use_container_width=False, key="nav_auth_logout"):
                    sign_out()
                    st.switch_page("pages/Home.py")
            else:
                if st.button("Log in", use_container_width=False, type="primary", key="nav_auth_login"):
                    st.switch_page("pages/Auth.py")
            st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('</div>', unsafe_allow_html=True)

    # Mobile hamburger menu (injected via components.html so JS runs in iframe → parent)
    st_components.html(
        """
        <script>
        (function () {
          const pickDoc = () => {
            // Streamlit runs inside nested iframes (and iOS Safari can be picky).
            // Walk up a few parents and pick the first document we can access that contains .clawd-topnav.
            let w = window;
            for (let i = 0; i < 4; i++) {
              try {
                const d = w.document;
                if (d && d.querySelector && d.querySelector('.clawd-topnav')) return d;
              } catch (e) {}
              try {
                if (w === w.parent) break;
                w = w.parent;
              } catch (e) { break; }
            }
            return document;
          };

          const tryInject = () => {
            const doc = pickDoc();
            if (doc.getElementById('clawd-mobile-topbar')) return;

            // Fixed topbar
            const bar = doc.createElement('div');
            bar.id = 'clawd-mobile-topbar';
            bar.innerHTML = [
              '<div class="brand">STOCK SENTINEL</div>',
              '<button id="clawd-hamburger" aria-label="Menu">&#9776;</button>',
            ].join('');

            // Menu
            const menu = doc.createElement('div');
            menu.id = 'clawd-mobile-menu';
            menu.innerHTML = [
              '<a href="./Home">Home</a>',
              '<a href="./Discovery">Market Scan</a>',
              '<a href="./Deep_Analysis">Deep Analyze</a>',
              '<a href="./Auth">Log in / Account</a>',
            ].join('');

            doc.body.appendChild(bar);
            doc.body.appendChild(menu);

            const btn = doc.getElementById('clawd-hamburger');

            const sync = () => {
              const w = (doc.documentElement.clientWidth || doc.body.clientWidth || 0);
              const isMobile = w <= 640;
              bar.style.display = isMobile ? 'flex' : 'none';
              if (!isMobile) {
                menu.classList.remove('open');
                btn.innerHTML = '&#9776;';
              }
            };

            btn.addEventListener('click', () => {
              menu.classList.toggle('open');
              btn.innerHTML = menu.classList.contains('open') ? '&#10005;' : '&#9776;';
            });

            // Close menu after navigation
            menu.addEventListener('click', (e) => {
              if (e.target && e.target.tagName === 'A') {
                menu.classList.remove('open');
                btn.innerHTML = '&#9776;';
              }
            });

            sync();
            (doc.defaultView || window).addEventListener('resize', sync);
          };

          const obs = new MutationObserver(tryInject);
          try { obs.observe(document.documentElement, { childList: true, subtree: true }); } catch (e) {}
          try { const d = pickDoc(); obs.observe(d.documentElement, { childList: true, subtree: true }); } catch (e) {}
          setTimeout(tryInject, 300);
          setTimeout(tryInject, 900);
          setTimeout(tryInject, 1800);
        })();
        </script>
        """,
        height=0,
    )

    # No extra spacer after nav (prevents big gap before the hero)
