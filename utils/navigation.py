"""Shared application navigation for Stock Sentinel."""

from __future__ import annotations

import html
import streamlit as st

_ACTIVE_KEYS = {"home", "market_scan", "deep_analyze", "account"}


def render_sidebar_navigation() -> None:
    """Hide Streamlit's generated sidebar navigation."""
    st.markdown(
        """<style>
        [data-testid="stSidebar"], [data-testid="stSidebarNav"] {
          display: none !important;
        }
        </style>""",
        unsafe_allow_html=True,
    )


def _nav_link(column, page: str, label: str, key: str,
              active: str, surface: str) -> None:
    with column:
        with st.container(key=f"nav_{surface}_{key}"):
            st.page_link(page, label=label, use_container_width=True)
            if active == key:
                st.markdown(
                    f'<span class="ss-sr-only">Current page: '
                    f'{html.escape(label)}</span>'
                    '<span class="ss-nav-current" aria-hidden="true"></span>',
                    unsafe_allow_html=True,
                )


def _brand(column, surface: str) -> None:
    with column:
        with st.container(key=f"nav_{surface}_brand"):
            st.page_link(
                "pages/Home.py", label="STOCK SENTINEL",
                use_container_width=True,
            )


def _admin_link(column, surface: str) -> None:
    with column:
        with st.container(key=f"nav_{surface}_admin"):
            st.page_link("pages/Admin.py", label="Admin",
                         use_container_width=True)


def _account_link(column, surface: str, active: str) -> None:
    with column:
        with st.container(key=f"nav_{surface}_account"):
            st.page_link(
                "pages/Account.py", label="Account", use_container_width=True,
            )
            if active == "account":
                st.markdown(
                    '<span class="ss-sr-only">Current page: Account</span>'
                    '<span class="ss-nav-current" aria-hidden="true"></span>',
                    unsafe_allow_html=True,
                )


def _auth_control(column, *, logged_in: bool, surface: str) -> None:
    from utils.auth import sign_out

    with column:
        with st.container(key=f"nav_{surface}_auth"):
            if logged_in:
                if st.button("Log out", use_container_width=True,
                             key=f"nav_{surface}_logout_button"):
                    sign_out()
                    st.switch_page("pages/Home.py")
            elif st.button("Log in", type="primary", use_container_width=True,
                           key=f"nav_{surface}_login_button"):
                st.switch_page("pages/Auth.py")


def render_top_nav(*, active: str = "", credits: int | None = None) -> None:
    """Render a shared two-layout header without extra data reads."""
    from utils.auth import get_user, is_logged_in

    active = active if active in _ACTIVE_KEYS else ""
    logged_in = is_logged_in()
    user = get_user() or {}
    email = ((user.get("email") if isinstance(user, dict)
              else getattr(user, "email", None)) or "").strip()
    admin_email = str(st.secrets.get("ADMIN_EMAIL", "") or "").lower().strip()
    is_admin = bool(logged_in and admin_email and email.lower() == admin_email)
    show_credits = bool(logged_in and credits is not None)

    st.markdown(
        """<style>
        .ss-sr-only {
          position:absolute!important;width:1px!important;height:1px!important;
          padding:0!important;margin:-1px!important;overflow:hidden!important;
          clip:rect(0,0,0,0)!important;white-space:nowrap!important;border:0!important;
        }
        .st-key-ss_top_nav {
          border-bottom:1px solid rgba(148,163,184,.18);
          background:rgba(6,13,27,.94);padding:.62rem 0 .58rem;
          margin:0 0 1.05rem;
        }
        .st-key-ss_top_nav [data-testid="stHorizontalBlock"] {
          align-items:center!important;gap:.6rem!important;
        }
        .st-key-ss_nav_mobile { display:none; }
        [class*="st-key-nav_desktop_"],
        [class*="st-key-nav_mobile_"] { position:relative; }
        [class*="st-key-nav_desktop_"] [data-testid="stPageLink"] a,
        [class*="st-key-nav_mobile_"] [data-testid="stPageLink"] a {
          min-height:44px;justify-content:center;border-radius:8px;
          padding:.45rem .55rem;color:rgba(203,213,225,.88)!important;
          font-size:.86rem;font-weight:680;text-decoration:none!important;
          white-space:nowrap;background:transparent!important;
          box-shadow:none!important;border:0!important;
        }
        [class*="st-key-nav_desktop_brand"] [data-testid="stPageLink"] a,
        [class*="st-key-nav_mobile_brand"] [data-testid="stPageLink"] a {
          justify-content:flex-start;padding:0;color:var(--accent)!important;
          font-size:.88rem;font-weight:800;letter-spacing:.07em;
          background:transparent!important;
        }
        [class*="st-key-nav_desktop_"] [data-testid="stPageLink"] a:hover,
        [class*="st-key-nav_mobile_"] [data-testid="stPageLink"] a:hover {
          background:rgba(56,189,248,.07)!important;
          color:rgba(248,250,252,.98)!important;
        }
        [class*="st-key-nav_desktop_admin"] [data-testid="stPageLink"] a,
        [class*="st-key-nav_mobile_admin"] [data-testid="stPageLink"] a {
          border:1px solid rgba(148,163,184,.24)!important;
        }
        .ss-nav-current {
          position:absolute;left:18%;right:18%;bottom:-9px;height:2px;
          border-radius:999px;background:var(--accent);
        }
        .ss-credit-badge {
          display:inline-flex;min-height:40px;align-items:center;
          justify-content:center;border:1px solid rgba(56,189,248,.34);
          font-size:.81rem;font-weight:750;
        }
        .ss-credit-badge {
          border-radius:9px;padding:0 .65rem;color:rgba(125,211,252,.98)!important;
          white-space:nowrap;
        }
        [class*="st-key-nav_desktop_account"] [data-testid="stPageLink"] a,
        [class*="st-key-nav_mobile_account"] [data-testid="stPageLink"] a {
          border:1px solid rgba(148,163,184,.24)!important;
          color:rgba(226,232,240,.94)!important;
        }
        [class*="st-key-nav_desktop_auth"] button,
        [class*="st-key-nav_mobile_auth"] button {
          min-height:44px!important;border-radius:9px!important;
          white-space:nowrap!important;
        }
        .st-key-ss_top_nav a:focus-visible,
        .st-key-ss_top_nav button:focus-visible {
          outline:3px solid rgba(56,189,248,.78)!important;
          outline-offset:3px!important;box-shadow:none!important;
        }
        @media (max-width:760px) {
          .st-key-ss_top_nav {padding:.45rem 0 .4rem;margin-bottom:1.15rem;}
          .st-key-ss_nav_desktop {display:none!important;}
          .st-key-ss_nav_mobile {display:block!important;}
          .st-key-ss_nav_mobile_links {
            margin-top:.2rem;padding-top:.2rem;
            border-top:1px solid rgba(148,163,184,.12);
          }
          .st-key-ss_nav_mobile_links [data-testid="stHorizontalBlock"] {
            gap:.25rem!important;
          }
          [class*="st-key-nav_mobile_"] [data-testid="stPageLink"] a {
            padding-left:.2rem;padding-right:.2rem;font-size:.79rem;
          }
          [class*="st-key-nav_mobile_brand"] [data-testid="stPageLink"] a {
            font-size:.76rem;
          }
          .ss-nav-current {bottom:-5px;}
        }
        </style>""",
        unsafe_allow_html=True,
    )

    with st.container(key="ss_top_nav"):
        with st.container(key="ss_nav_desktop"):
            widths = [1.55, 3.2]
            if show_credits:
                widths.append(.82)
            if is_admin:
                widths.append(.58)
            if logged_in:
                widths.append(.74)
            widths.append(.82)
            cols = iter(st.columns(widths))

            _brand(next(cols), "desktop")
            links_col = next(cols)
            with links_col:
                home, scan, deep = st.columns(3)
                _nav_link(home, "pages/Home.py", "Home", "home",
                          active, "desktop")
                _nav_link(scan, "pages/Discovery.py", "Market Scan",
                          "market_scan", active, "desktop")
                _nav_link(deep, "pages/Deep_Analysis.py", "Deep Analyze",
                          "deep_analyze", active, "desktop")

            if show_credits:
                with next(cols):
                    word = "credit" if int(credits) == 1 else "credits"
                    st.markdown(
                        f'<span class="ss-credit-badge">{int(credits)} {word}</span>',
                        unsafe_allow_html=True,
                    )
            if is_admin:
                _admin_link(next(cols), "desktop")
            if logged_in:
                _account_link(next(cols), "desktop", active)
            _auth_control(next(cols), logged_in=logged_in, surface="desktop")

        with st.container(key="ss_nav_mobile"):
            if logged_in:
                brand_col, account_col, auth_col = st.columns([2.0, .8, .8])
                admin_col = None
            else:
                brand_col, auth_col = st.columns([2.4, .8])
                admin_col = None
                account_col = None
            _brand(brand_col, "mobile")
            if admin_col is not None:
                _admin_link(admin_col, "mobile")
            if account_col is not None:
                _account_link(account_col, "mobile", active)
            _auth_control(auth_col, logged_in=logged_in, surface="mobile")

            with st.container(key="ss_nav_mobile_links"):
                home, scan, deep = st.columns(3)
                _nav_link(home, "pages/Home.py", "Home", "home",
                          active, "mobile")
                _nav_link(scan, "pages/Discovery.py", "Market Scan",
                          "market_scan", active, "mobile")
                _nav_link(deep, "pages/Deep_Analysis.py", "Deep Analyze",
                          "deep_analyze", active, "mobile")
            if is_admin:
                with st.container(key="ss_nav_mobile_admin_row"):
                    spacer, admin = st.columns([2.5, .7])
                    del spacer
                    _admin_link(admin, "mobile_utility")
