"""Contracts for the post-Release-E navigation simplification.

These checks are source-only: they perform no login, scan, analysis, checkout,
or network request.
"""

from pathlib import Path


REPO = Path(__file__).resolve().parent.parent


def _read(relative: str) -> str:
    return (REPO / relative).read_text(encoding="utf-8")


def test_public_home_is_not_a_duplicate_authenticated_workspace() -> None:
    home = _read("pages/Home.py")

    assert 'render_top_nav()' in home
    assert 'render_top_nav(active="home")' not in home
    assert 'key="home_cap_grid"' not in home
    assert 'key="home_credit_hub"' not in home
    assert "Welcome back," not in home
    assert '"Open Market Scan"' in home
    assert '"Analyze a ticker"' in home


def test_authenticated_navigation_has_one_destination_per_job() -> None:
    nav = _read("utils/navigation.py")

    assert 'scan, deep, account = st.columns(3)' in nav
    assert 'scan, deep = st.columns(2)' in nav
    assert '"pages/Discovery.py", "Market Scan"' in nav
    assert '"pages/Deep_Analysis.py", "Deep Analyze"' in nav
    assert '"pages/Account.py", "Account"' in nav
    assert '"pages/Home.py", "Home"' not in nav
    assert '_auth_control(next(cols), logged_in=True' not in nav
    assert 'nav_desktop_account"] [data-testid="stPageLink"] a' not in nav


def test_brand_remains_the_public_landing_route_for_every_session() -> None:
    nav = _read("utils/navigation.py")

    brand = nav.split("def _brand", 1)[1].split("def _admin_link", 1)[0]
    assert '"pages/Home.py", label="STOCK SENTINEL"' in brand


def test_default_and_explicit_post_auth_destinations_are_preserved() -> None:
    auth = _read("pages/Auth.py")
    nav = _read("utils/navigation.py")
    guard = _read("utils/guard.py")
    home = _read("pages/Home.py")

    assert 'or "Discovery"' in auth
    assert 'after_auth_page: str = "Discovery"' in nav
    assert 'after_auth_page: str = "Discovery"' in guard
    assert 'st.session_state["_after_auth_page"] = "Discovery"' in home
    assert 'st.session_state["_after_auth_page"] = "Deep_Analysis"' in home
    assert 'st.switch_page("pages/Discovery.py")' in auth
    assert 'st.switch_page("pages/Deep_Analysis.py")' in auth


def test_account_owns_logout_and_alignment_contracts() -> None:
    account = _read("pages/Account.py")
    adapter = _read("assets/styles/stock-sentinel-streamlit-adapter.css")

    assert 'from utils.auth import flush_pending_rt_save, get_user, sign_out' in account
    assert 'key="account_header"' in account
    assert 'key="account_header_logout"' in account
    assert 'key="account_session"' not in account
    assert 'st.button("Log out"' in account
    assert 'st.switch_page("pages/Home.py")' in account
    assert 'align-items: stretch !important' in adapter
    assert 'height: 100% !important' in adapter
    assert 'box-sizing:border-box;width:100%;height:100%;' in account
    assert '[data-testid="stVerticalBlockBorderWrapper"]' in adapter
    assert '[data-testid="stElementContainer"]:has(.ss-account-card)' in adapter
    assert '[data-testid="stHtml"]:has(.ss-account-card)' in adapter
    assert account.count('class="ss-account-kicker"') >= 2
    assert '.st-key-account_header_logout button' in adapter
    assert 'min-height: 44px !important' in adapter
    assert 'flex: 1 1 100% !important' in adapter
    assert 'min-width: 100% !important' in adapter
    mobile_header = account.split("@media (max-width:720px)", 1)[1].split(
        "</style>", 1
    )[0]
    assert '.st-key-account_header [data-testid="stHorizontalBlock"]' in mobile_header
    assert "flex-wrap:wrap!important" in mobile_header
    assert '.st-key-account_header [data-testid="stColumn"]' in mobile_header
    assert "flex:1 1 100%!important" in mobile_header


def test_navigation_controls_share_alignment_and_touch_targets() -> None:
    nav = _read("utils/navigation.py")
    tokens = _read("assets/styles/stock-sentinel-tokens.css")
    ui = _read("utils/ui.py")
    home = _read("pages/Home.py")

    assert 'align-items:center!important;gap:.6rem!important;' in nav
    assert 'min-height:44px;justify-content:center' in nav
    assert 'class="ss-nav-semantic"' in nav
    assert 'left:18%;right:18%;bottom:-9px;' in nav
    assert 'brand_col, account_col = st.columns([2.3, .9])' in nav
    assert 'flex-wrap:nowrap!important' in nav
    assert 'aria-current="page"' in nav
    assert 'class="ss-nav-semantic"' in nav
    assert nav.count("st.page_link(page, label=label") == 1
    assert nav.count('st.html(') >= 1
    assert 'f".st-key-nav_desktop_{active}"' in nav
    assert 'f".st-key-nav_mobile_{active}"' in nav
    assert "ss-nav-active-link" not in nav
    assert "--ss-shell-max-width: 1100px" in tokens
    assert "max-width: var(--ss-shell-max-width)" in ui
    assert "max-width: var(--ss-marketing-max-width)" not in home


def test_authenticated_marketing_actions_do_not_restart_signup() -> None:
    home = _read("pages/Home.py")
    how = _read("pages/How_It_Works.py")

    assert 'if _logged_in:' in how
    assert 'label="Continue to Market Scan"' in how
    assert '"pages/Discovery.py", label="Continue to Market Scan"' in how
    assert 'label="Open Market Scan"' in home
    assert 'label="Open Deep Analyze"' in home


def test_responsive_panels_and_actions_stack_before_they_cramp() -> None:
    home = _read("pages/Home.py")
    account = _read("pages/Account.py")

    assert '@media (max-width:900px)' in home
    assert '@media (max-width:520px)' in home
    assert '.st-key-home_public_ctas [data-testid="stColumn"]' in home
    assert 'row-gap:.65rem!important' in account


def main() -> int:
    tests = [
        test_public_home_is_not_a_duplicate_authenticated_workspace,
        test_authenticated_navigation_has_one_destination_per_job,
        test_brand_remains_the_public_landing_route_for_every_session,
        test_default_and_explicit_post_auth_destinations_are_preserved,
        test_account_owns_logout_and_alignment_contracts,
        test_navigation_controls_share_alignment_and_touch_targets,
        test_authenticated_marketing_actions_do_not_restart_signup,
        test_responsive_panels_and_actions_stack_before_they_cramp,
    ]
    failures: list[tuple[str, str]] = []
    for test in tests:
        try:
            test()
        except Exception as exc:
            failures.append((test.__name__, str(exc) or type(exc).__name__))
            print(f"  FAIL  {test.__name__}: {failures[-1][1]}")
        else:
            print(f"  PASS  {test.__name__}")
    print(f"\n  {len(tests) - len(failures)} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
