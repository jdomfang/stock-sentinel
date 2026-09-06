"""Shared application navigation for Stock Sentinel."""

from __future__ import annotations

import html
import streamlit as st

from utils import config as _config

_ACTIVE_KEYS = {
    "market_scan", "deep_analyze", "account", "admin",
    "how_it_works", "faq",
}


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
            # Keep the same Streamlit PageLink component mounted on every
            # route. Swapping it for a raw anchor on the active route caused
            # the desktop navigation to visibly reflow between pages.
            st.page_link(page, label=label, use_container_width=True)
            current_attr = ' aria-current="page"' if active == key else ""
            current_text = (
                f"Current page: {html.escape(label)}" if active == key else ""
            )
            # This zero-layout semantic slot exists for every destination so
            # the active route never changes the nav cell's child geometry.
            st.html(
                f'<span class="ss-nav-semantic"{current_attr}>'
                f'<span class="ss-sr-only">{current_text}</span></span>',
            )


def _brand(column, surface: str) -> None:
    with column:
        with st.container(key=f"nav_{surface}_brand"):
            st.page_link(
                "pages/Home.py", label="STOCK SENTINEL",
                use_container_width=True,
            )


def _admin_link(column, surface: str, active: str) -> None:
    """Render Admin through the same stable destination component as tabs."""
    _nav_link(
        column,
        "pages/Admin.py",
        "Admin",
        "admin",
        active,
        surface,
    )


def _login_control(
    column, *, surface: str, after_auth_page: str = "Discovery",
) -> None:
    with column:
        with st.container(key=f"nav_{surface}_auth"):
            if st.button("Log in", use_container_width=True,
                         key=f"nav_{surface}_login_button"):
                st.session_state.pop("_public_research_intent", None)
                st.session_state["auth_initial_mode"] = "Sign In"
                st.session_state["_after_auth_page"] = after_auth_page
                st.switch_page("pages/Auth.py")


def _signup_control(column, *, surface: str, primary: bool = True) -> None:
    """Public primary action; authentication behavior remains owned by Auth."""
    with column:
        with st.container(key=f"nav_{surface}_signup"):
            if st.button(
                "Start free", type="primary" if primary else "secondary",
                use_container_width=True,
                key=f"nav_{surface}_signup_button",
            ):
                st.session_state.pop("_public_research_intent", None)
                st.session_state["auth_initial_mode"] = "Create Account"
                st.session_state["_after_auth_page"] = "Discovery"
                st.switch_page("pages/Auth.py")


def _session_initial(user: object, email: str) -> str:
    """Return one stable, human initial for the compact session control."""
    metadata: object = {}
    if isinstance(user, dict):
        metadata = user.get("user_metadata") or {}
    else:
        metadata = getattr(user, "user_metadata", {}) or {}

    display_name = ""
    if isinstance(metadata, dict):
        display_name = str(
            metadata.get("display_name")
            or metadata.get("full_name")
            or metadata.get("name")
            or ""
        ).strip()

    for candidate in (display_name, email.split("@", 1)[0]):
        for character in candidate:
            if character.isalpha():
                return character.upper()
    return "U"


def _session_menu(column, *, surface: str, user: object, email: str) -> None:
    """Render a fixed-size session trigger whose menu overlays the page."""
    from utils.auth import sign_out

    initial = _session_initial(user, email)
    safe_email = html.escape(email or "Signed-in account", quote=True)
    with column:
        with st.container(key=f"nav_{surface}_session"):
            with st.popover(
                f"{initial} Account menu",
                help=f"Open account menu for {email}" if email else "Open account menu",
                use_container_width=True,
            ):
                st.html(
                    '<div class="ss-session-menu">'
                    '<div class="ss-session-menu__label">Signed in as</div>'
                    f'<div class="ss-session-menu__email">{safe_email}</div>'
                    '</div>'
                )
                if st.button(
                    "Log out",
                    key=f"nav_{surface}_logout_button",
                    use_container_width=True,
                ):
                    sign_out()
                    st.switch_page("pages/Home.py")


def render_top_nav(
    *, active: str = "", credits: int | None = None,
    after_auth_page: str = "Discovery", signup_primary: bool = True,
) -> None:
    """Render a shared two-layout header without extra data reads."""
    from utils.auth import get_user, is_logged_in

    active = active if active in _ACTIVE_KEYS else ""
    logged_in = is_logged_in()
    user = get_user() or {}
    email = ((user.get("email") if isinstance(user, dict)
              else getattr(user, "email", None)) or "").strip()
    # utils.config, NOT st.secrets directly. st.secrets.get() RAISES
    # FileNotFoundError when no secrets.toml exists anywhere -- and this
    # function renders on every page, so a container configured purely from
    # environment variables (Railway, a VPS, any Docker host) died here before
    # a single line of page code ran. utils.config reads os.environ first and
    # swallows exactly this failure; the portal has always been portable
    # everywhere except these two lines.
    admin_email = _config.get("ADMIN_EMAIL", "").lower().strip()
    is_admin = bool(logged_in and admin_email and email.lower() == admin_email)
    show_credits = bool(logged_in and credits is not None)

    active_css = ""
    if active:
        desktop = f".st-key-nav_desktop_{active}"
        mobile = f".st-key-nav_mobile_{active}"
        active_css = f"""
        {desktop} [data-testid="stPageLink"] a,
        {mobile} [data-testid="stPageLink"] a {{
          color:rgba(248,250,252,.98)!important;
        }}
        {desktop}::after,
        {mobile}::after {{
          content:"";position:absolute;left:18%;right:18%;bottom:-9px;
          height:2px;border-radius:999px;background:var(--accent);
          pointer-events:none;
        }}
        @media (max-width:900px) {{
          {mobile}::after {{bottom:-5px;}}
        }}
        """

    st.markdown(
        """<style>
        .ss-sr-only {
          position:absolute!important;width:1px!important;height:1px!important;
          padding:0!important;margin:-1px!important;overflow:hidden!important;
          clip:rect(0,0,0,0)!important;white-space:nowrap!important;border:0!important;
        }
        .st-key-ss_top_nav {
          border-bottom:1px solid rgba(148,163,184,.18);
          background:transparent;padding:.62rem 0 .58rem;
          margin:0 0 1.05rem;
        }
        .st-key-ss_top_nav [data-testid="stHorizontalBlock"] {
          align-items:center!important;gap:.6rem!important;
        }
        .st-key-ss_nav_desktop_links [data-testid="stHorizontalBlock"] {
          display:flex!important;align-items:center!important;
          flex-wrap:nowrap!important;
          gap:clamp(16px, 2vw, 20px)!important;
        }
        .st-key-ss_nav_desktop_links [data-testid="stHorizontalBlock"]
          > [data-testid="stColumn"] {
          min-width:0!important;
        }
        .st-key-ss_nav_desktop_links [data-testid="stColumn"]:has(.st-key-nav_desktop_session),
        .st-key-ss_nav_mobile_primary [data-testid="stColumn"]:has(.st-key-nav_mobile_session) {
          flex:0 0 44px!important;width:44px!important;
          min-width:44px!important;max-width:44px!important;
        }
        .st-key-ss_nav_mobile { display:none; }
        [class*="st-key-nav_desktop_"],
        [class*="st-key-nav_mobile_"] { position:relative; }
        [class*="st-key-nav_desktop_"] > [data-testid="stVerticalBlock"],
        [class*="st-key-nav_mobile_"] > [data-testid="stVerticalBlock"] {
          gap:0!important;
        }
        [class*="st-key-nav_desktop_"] [data-testid="stElementContainer"]:has(.ss-nav-semantic),
        [class*="st-key-nav_mobile_"] [data-testid="stElementContainer"]:has(.ss-nav-semantic) {
          position:absolute!important;width:1px!important;height:1px!important;
          min-height:0!important;margin:0!important;padding:0!important;
          overflow:hidden!important;
        }
        [class*="st-key-nav_desktop_"] [data-testid="stPageLink"] a,
        [class*="st-key-nav_mobile_"] [data-testid="stPageLink"] a {
          min-height:44px;justify-content:center;border-radius:8px;
          padding:.45rem .55rem;color:rgba(203,213,225,.88)!important;
          font-size:.86rem;font-weight:680;text-decoration:none!important;
          white-space:nowrap;background:transparent!important;
          box-shadow:none!important;border:0!important;align-items:center;
          display:flex;width:100%;box-sizing:border-box;
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
        .ss-credit-badge {
          display:inline-flex;min-height:40px;align-items:center;
          justify-content:center;border:1px solid rgba(56,189,248,.34);
          font-size:.81rem;font-weight:750;
        }
        .ss-credit-badge {
          border-radius:9px;padding:0 .65rem;color:rgba(125,211,252,.98)!important;
          white-space:nowrap;
        }
        [class*="st-key-nav_desktop_auth"] button,
        [class*="st-key-nav_mobile_auth"] button,
        [class*="st-key-nav_desktop_signup"] button,
        [class*="st-key-nav_mobile_signup"] button {
          min-height:44px!important;border-radius:9px!important;
          white-space:nowrap!important;
        }
        [class*="st-key-nav_desktop_session"] [data-testid="stPopoverButton"],
        [class*="st-key-nav_mobile_session"] [data-testid="stPopoverButton"] {
          width:44px!important;min-width:44px!important;max-width:44px!important;
          height:44px!important;min-height:44px!important;max-height:44px!important;
          padding:0!important;border-radius:50%!important;
          display:flex!important;align-items:center!important;justify-content:center!important;
          border:1px solid rgba(56,189,248,.34)!important;
          background:rgba(15,23,42,.78)!important;color:#f8fafc!important;
          font-weight:800!important;line-height:1!important;
        }
        [class*="st-key-nav_desktop_session"] [data-testid="stPopoverButton"]
          > div:last-child:has(svg[aria-hidden="true"]),
        [class*="st-key-nav_mobile_session"] [data-testid="stPopoverButton"]
          > div:last-child:has(svg[aria-hidden="true"]) {
          display:none!important;
        }
        [class*="st-key-nav_desktop_session"] [data-testid="stPopoverButton"]
          :is([data-testid="stMarkdownContainer"], p, span),
        [class*="st-key-nav_mobile_session"] [data-testid="stPopoverButton"]
          :is([data-testid="stMarkdownContainer"], p, span) {
          margin:0!important;color:#f8fafc!important;
          -webkit-text-fill-color:#f8fafc!important;
          line-height:1!important;text-align:center!important;
        }
        [class*="st-key-nav_desktop_session"] [data-testid="stPopoverButton"] p,
        [class*="st-key-nav_mobile_session"] [data-testid="stPopoverButton"] p {
          font-size:0!important;width:1rem!important;white-space:nowrap!important;
          overflow:visible!important;
        }
        [class*="st-key-nav_desktop_session"] [data-testid="stPopoverButton"] p::first-letter,
        [class*="st-key-nav_mobile_session"] [data-testid="stPopoverButton"] p::first-letter {
          font-size:.86rem!important;font-weight:800!important;
        }
        [class*="st-key-nav_desktop_session"] [data-testid="stPopoverButton"]:hover,
        [class*="st-key-nav_mobile_session"] [data-testid="stPopoverButton"]:hover,
        [class*="st-key-nav_desktop_session"] [data-testid="stPopover"]
          > div[aria-expanded="true"] [data-testid="stPopoverButton"],
        [class*="st-key-nav_mobile_session"] [data-testid="stPopover"]
          > div[aria-expanded="true"] [data-testid="stPopoverButton"] {
          border-color:rgba(56,189,248,.7)!important;
          background:rgba(56,189,248,.1)!important;
        }
        [data-testid="stPopoverBody"]:has(.ss-session-menu) {
          width:min(290px, calc(100vw - 2rem))!important;
          padding:.8rem!important;border-radius:12px!important;
          border:1px solid rgba(56,189,248,.28)!important;
          background:#091326!important;
          box-shadow:0 18px 48px rgba(0,0,0,.42)!important;
        }
        [data-testid="stPopoverBody"]:has(.ss-session-menu) > div,
        [data-testid="stPopoverBody"]:has(.ss-session-menu)
          [data-testid="stVerticalBlockBorderWrapper"],
        [data-testid="stPopoverBody"]:has(.ss-session-menu)
          [data-testid="stVerticalBlock"],
        [data-testid="stPopoverBody"]:has(.ss-session-menu)
          [data-testid="stElementContainer"],
        [data-testid="stPopoverBody"]:has(.ss-session-menu)
          [data-testid="stHtml"] {
          background:transparent!important;background-color:transparent!important;
        }
        [data-testid="stPopoverBody"]:has(.ss-session-menu) [data-testid="stVerticalBlock"] {
          gap:.65rem!important;
        }
        [data-testid="stPopoverBody"]:has(.ss-session-menu) button {
          min-height:44px!important;width:100%!important;
          display:flex!important;align-items:center!important;justify-content:center!important;
          border-color:rgba(56,189,248,.38)!important;
          background:rgba(15,23,42,.94)!important;color:#f8fafc!important;
        }
        [data-testid="stPopoverBody"]:has(.ss-session-menu) button
          :is([data-testid="stMarkdownContainer"], p, span) {
          color:#f8fafc!important;-webkit-text-fill-color:#f8fafc!important;
          margin:0!important;text-align:center!important;
        }
        .ss-session-menu__label {
          color:#a8b6c9!important;-webkit-text-fill-color:#a8b6c9!important;
          font-size:.72rem;font-weight:800;line-height:1.2;opacity:1!important;
          letter-spacing:.07em;text-transform:uppercase;
        }
        .ss-session-menu__email {
          margin-top:.22rem;color:#f8fafc!important;
          -webkit-text-fill-color:#f8fafc!important;
          font-size:.88rem;font-weight:700;line-height:1.35;
          opacity:1!important;overflow-wrap:anywhere;
        }
        [class*="st-key-nav_desktop_marketing"] [data-testid="stHorizontalBlock"] {
          gap:.2rem!important;
        }
        [class*="st-key-nav_desktop_marketing"] [data-testid="stPageLink"] a,
        .ss-marketing-nav-link {
          min-height:44px;display:flex;align-items:center;justify-content:center;
          padding:.4rem .35rem;color:rgba(203,213,225,.82)!important;
          font-size:.82rem;font-weight:650;text-decoration:none!important;
          white-space:nowrap;border-radius:8px;
        }
        .ss-marketing-nav-link:hover {
          background:rgba(56,189,248,.07);color:#f8fafc!important;
        }
        .st-key-ss_top_nav a:focus-visible,
        .st-key-ss_top_nav button:focus-visible {
          outline:3px solid rgba(56,189,248,.78)!important;
          outline-offset:3px!important;box-shadow:none!important;
        }
        @media (min-width:1100px) and (hover:hover) and (pointer:fine) {
          .st-key-ss_top_nav {
            padding:.45rem 0 .4rem;
            margin-bottom:.65rem;
          }
        }
        @media (max-width:900px) {
          .st-key-ss_top_nav {padding:.45rem 0 .4rem;margin-bottom:1.15rem;}
          .st-key-ss_nav_desktop {display:none!important;}
          .st-key-ss_nav_mobile {display:block!important;}
          .st-key-ss_nav_mobile_primary [data-testid="stHorizontalBlock"],
          .st-key-ss_nav_mobile_links [data-testid="stHorizontalBlock"],
          .st-key-ss_nav_mobile_marketing [data-testid="stHorizontalBlock"] {
            display:flex!important;flex-wrap:nowrap!important;
            align-items:center!important;gap:.35rem!important;
          }
          .st-key-ss_nav_mobile_primary [data-testid="stColumn"],
          .st-key-ss_nav_mobile_links [data-testid="stColumn"],
          .st-key-ss_nav_mobile_marketing [data-testid="stColumn"] {
            min-width:0!important;width:auto!important;
          }
          .st-key-ss_nav_mobile_links {
            margin-top:.2rem;padding-top:.2rem;
            border-top:1px solid rgba(148,163,184,.12);
          }
          .st-key-ss_nav_mobile_marketing {
            margin-top:.2rem;padding-top:.2rem;
            border-top:1px solid rgba(148,163,184,.12);
          }
          [class*="st-key-nav_mobile_"] [data-testid="stPageLink"] a {
            padding-left:.2rem;padding-right:.2rem;font-size:.79rem;
          }
          [class*="st-key-nav_mobile_brand"] [data-testid="stPageLink"] a {
            font-size:.76rem;
          }
        }
        </style>""" + f"<style>{active_css}</style>",
        unsafe_allow_html=True,
    )

    with st.container(key="ss_top_nav"):
        with st.container(key="ss_nav_desktop"):
            if not logged_in:
                brand, marketing, login, signup = st.columns(
                    [1.55, 2.15, .72, .82]
                )
                _brand(brand, "desktop")
                _login_control(
                    login, surface="desktop", after_auth_page=after_auth_page,
                )
                _signup_control(
                    signup, surface="desktop", primary=signup_primary,
                )
            else:
                # One stable outer shell and one non-wrapping destination row.
                # Admin and the session trigger used to live in different
                # nested column systems. At desktop zoom or narrower shells,
                # the avatar wrapped below Market Scan while Admin remained on
                # the first row. Keep every authenticated action in one row,
                # with the fixed-size session utility always last.
                brand_col, links_col = st.columns([1.35, 3.65])
                _brand(brand_col, "desktop")
                with links_col:
                    with st.container(key="ss_nav_desktop_links"):
                        nav_widths = [1, 1, 1]
                        if is_admin:
                            nav_widths.append(.72)
                        if show_credits:
                            nav_widths.append(.72)
                        nav_widths.append(.30)
                        nav_cols = iter(st.columns(nav_widths))
                        scan = next(nav_cols)
                        deep = next(nav_cols)
                        account = next(nav_cols)
                        _nav_link(scan, "pages/Discovery.py", "Market Scan",
                                  "market_scan", active, "desktop")
                        _nav_link(deep, "pages/Deep_Analysis.py", "Deep Analyze",
                                  "deep_analyze", active, "desktop")
                        _nav_link(account, "pages/Account.py", "Account",
                                  "account", active, "desktop")
                        if is_admin:
                            _admin_link(next(nav_cols), "desktop", active)
                        if show_credits:
                            with next(nav_cols):
                                word = "credit" if int(credits) == 1 else "credits"
                                st.markdown(
                                    f'<span class="ss-credit-badge">'
                                    f'{int(credits)} {word}</span>',
                                    unsafe_allow_html=True,
                                )
                        _session_menu(
                            next(nav_cols), surface="desktop", user=user,
                            email=email,
                        )

        with st.container(key="ss_nav_mobile"):
            with st.container(key="ss_nav_mobile_primary"):
                if logged_in:
                    brand_col, account_col, session_col = st.columns(
                        [2.3, .9, .34]
                    )
                    auth_col = None
                    signup_col = None
                else:
                    brand_col, auth_col, signup_col = st.columns([1.65, .72, .82])
                    account_col = None
                    session_col = None
                _brand(brand_col, "mobile")
                if account_col is not None:
                    _nav_link(
                        account_col, "pages/Account.py", "Account", "account",
                        active, "mobile",
                    )
                if session_col is not None:
                    _session_menu(
                        session_col, surface="mobile", user=user, email=email,
                    )
                if auth_col is not None:
                    _login_control(
                        auth_col, surface="mobile",
                        after_auth_page=after_auth_page,
                    )
                if signup_col is not None:
                    _signup_control(
                        signup_col, surface="mobile", primary=signup_primary,
                    )

            if logged_in:
                with st.container(key="ss_nav_mobile_links"):
                    scan, deep = st.columns(2)
                    _nav_link(scan, "pages/Discovery.py", "Market Scan",
                              "market_scan", active, "mobile")
                    _nav_link(deep, "pages/Deep_Analysis.py", "Deep Analyze",
                              "deep_analyze", active, "mobile")
            if is_admin:
                with st.container(key="ss_nav_mobile_admin_row"):
                    spacer, admin = st.columns([2.5, .7])
                    del spacer
                    _admin_link(admin, "mobile", active)
