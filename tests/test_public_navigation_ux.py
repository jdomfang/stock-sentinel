"""Regression contracts for public navigation and the signup handoff UI.

These checks intentionally perform no network calls, authentication, scans, or
analysis. Browser journey coverage complements them during visual QA.
"""

import ast
from pathlib import Path
import re


REPO = Path(__file__).resolve().parent.parent


def _read(relative: str) -> str:
    return (REPO / relative).read_text(encoding="utf-8")


def test_all_literal_internal_link_targets_exist() -> None:
    source_files = [REPO / "app.py"]
    source_files.extend((REPO / "pages").glob("*.py"))
    source_files.extend((REPO / "utils").glob("*.py"))

    found_targets: set[str] = set()
    for path in source_files:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            target_arg = None
            if (
                isinstance(func, ast.Attribute)
                and func.attr in {"page_link", "switch_page"}
            ):
                target_arg = node.args[0]
            elif (
                isinstance(func, ast.Name)
                and func.id == "_nav_link"
                and len(node.args) > 1
            ):
                target_arg = node.args[1]
            if not (
                isinstance(target_arg, ast.Constant)
                and isinstance(target_arg.value, str)
            ):
                continue
            target = target_arg.value
            if target.startswith("pages/"):
                found_targets.add(target)
                assert (REPO / target).is_file(), f"{path}: missing {target}"

        for href in re.findall(r'href=["\']([^"\']+)', source):
            if href.startswith("#"):
                fragment = re.escape(href[1:])
                assert re.search(
                    rf'id=["\']{fragment}["\']', source
                ), f"{path}: missing page-local fragment target {href}"
                continue
            if href.startswith("/") and not href.startswith("//"):
                raise AssertionError(f"{path}: unresolved hardcoded route {href}")

    expected = {
        "pages/Home.py", "pages/How_It_Works.py", "pages/FAQ.py",
        "pages/Contact.py", "pages/Trust_Center.py", "pages/Auth.py",
        "pages/Discovery.py", "pages/Deep_Analysis.py", "pages/Account.py",
        "pages/Admin.py", "pages/Analysis_Result.py",
    }
    assert expected.issubset(found_targets)


def test_public_how_it_works_uses_a_real_route() -> None:
    nav = _read("utils/navigation.py")
    home = _read("pages/Home.py")

    assert '"pages/How_It_Works.py", "How it works"' in nav
    assert 'href="#how-it-works"' not in nav
    assert 'href="#how-it-works"' not in home
    assert (REPO / "pages" / "How_It_Works.py").is_file()

    public_sources = [
        _read("utils/navigation.py"),
        _read("pages/Home.py"),
        _read("pages/FAQ.py"),
        _read("pages/Contact.py"),
        _read("pages/Trust_Center.py"),
        _read("pages/How_It_Works.py"),
    ]
    assert all('href="#' not in source for source in public_sources)


def test_primary_public_navigation_stays_minimal() -> None:
    nav = _read("utils/navigation.py")
    marketing = nav.split("def _marketing_links", 1)[1].split(
        "def render_top_nav", 1
    )[0]

    assert '"How it works"' in marketing
    assert '"FAQ"' in marketing
    assert 'label="Methodology"' not in marketing
    assert 'label="Trust Center"' not in marketing


def test_how_it_works_explains_output_and_credit_boundaries() -> None:
    page = _read("pages/How_It_Works.py")

    for expected in (
        "How Stock Sentinel works",
        "Bullish, Bearish, or Neutral",
        "Buy, Watch, or Avoid",
        "Market Scan · 1 credit",
        "Deep Analyze · 1 credit",
        "How the signal is formed",
        "Confidence is not probability",
        "Read the Trust Center",
    ):
        assert expected in page

    assert 'render_top_nav(active="how_it_works")' in page
    for forbidden in (
        "consume_credit", "refund_credit", "analyze_remote",
        "create_checkout", "stripe.checkout", "run_sector_scan",
    ):
        assert forbidden not in page


def test_trust_detail_is_footer_only_and_complete() -> None:
    footer = _read("utils/ui.py")
    trust = _read("pages/Trust_Center.py")

    assert 'st.page_link("pages/Trust_Center.py", label="Trust Center")' in footer
    for section in (
        "Methodology",
        "Data sources and freshness",
        "Confidence and limitations",
        "Credits and refunds",
        "Privacy and payment handling",
        "Not financial advice",
    ):
        assert section in trust


def test_faq_has_a_real_contact_destination() -> None:
    faq = _read("pages/FAQ.py")

    assert 'st.page_link("pages/Contact.py", label="Contact support")' in faq
    assert "Services → Contact" not in faq
    assert 'render_top_nav(active="faq")' in faq
    assert "Search FAQs" not in faq
    assert "query = st.text_input" not in faq


def test_start_free_has_a_mode_specific_premium_auth_surface() -> None:
    auth = _read("pages/Auth.py")
    nav = _read("utils/navigation.py")
    home = _read("pages/Home.py")
    how = _read("pages/How_It_Works.py")

    assert '"Create your free account"' in auth
    assert '"Start with 2 free credits. No card required."' in auth
    assert '"Create free account"' in auth
    assert "Secure account access powered by Supabase" in auth
    assert "all: revert" not in auth
    assert "Payment details are entered only on Stripe" not in auth
    assert 'form_col, value_col = st.columns([1.08, .92])' in auth
    assert '<h1 class="auth-form-heading">' in auth
    assert '<h2 class="auth-value-title">' in auth
    for origin in (nav, home, how):
        assert 'st.session_state["auth_initial_mode"] = "Create Account"' in origin
        assert 'st.session_state["_after_auth_page"] = "Discovery"' in origin


def test_every_explicit_login_origin_resets_sign_in_mode() -> None:
    for relative in (
        "utils/navigation.py", "utils/guard.py", "pages/Admin.py",
    ):
        source = _read(relative)
        assert 'st.session_state["auth_initial_mode"] = "Sign In"' in source
        assert 'st.switch_page("pages/Auth.py")' in source

    nav = _read("utils/navigation.py")
    assert 'st.session_state["_after_auth_page"] = after_auth_page' in nav
    assert 'after_auth_page: str = "Discovery"' in nav

    guard = _read("utils/guard.py")
    auth = _read("pages/Auth.py")
    admin = _read("pages/Admin.py")
    assert 'st.session_state["_after_auth_page"] = after_auth_page' in guard
    assert 'st.session_state["_after_auth_page"] = "Admin"' in admin
    assert 'render_top_nav(after_auth_page="Admin")' in admin
    for target in (
        "pages/Home.py", "pages/Discovery.py", "pages/Deep_Analysis.py",
        "pages/Analysis_Result.py", "pages/Account.py", "pages/Admin.py",
    ):
        assert f'st.switch_page("{target}")' in auth


def test_shared_links_have_accessible_targets_and_current_state() -> None:
    nav = _read("utils/navigation.py")
    footer = _read("utils/ui.py")
    billing = _read("utils/billing.py")

    assert 'aria-current="page"' in nav
    assert 'class="ss-nav-semantic"' in nav
    assert "ss-nav-active-link" not in nav
    assert 'min-height: 44px;' in footer
    assert 'with st.container(key="footer_links"):' in footer
    assert '.st-key-footer_links [data-testid="stPageLink"] a' in footer
    assert 'label = label or f"Buy {PACK_CREDITS} credits · {PACK_PRICE}"' in billing


def main() -> int:
    tests = [
        test_all_literal_internal_link_targets_exist,
        test_public_how_it_works_uses_a_real_route,
        test_primary_public_navigation_stays_minimal,
        test_how_it_works_explains_output_and_credit_boundaries,
        test_trust_detail_is_footer_only_and_complete,
        test_faq_has_a_real_contact_destination,
        test_start_free_has_a_mode_specific_premium_auth_surface,
        test_every_explicit_login_origin_resets_sign_in_mode,
        test_shared_links_have_accessible_targets_and_current_state,
    ]
    failed: list[tuple[str, str]] = []

    print("=" * 72)
    print("  Public navigation and signup UX regression contract")
    print("=" * 72)
    for test in tests:
        try:
            test()
        except Exception as exc:  # direct-run suite must report every failure
            failed.append((test.__name__, str(exc) or type(exc).__name__))
            print(f"  FAIL  {test.__name__}: {failed[-1][1]}")
        else:
            print(f"  PASS  {test.__name__}")

    print("\n" + "=" * 72)
    print(f"  {len(tests) - len(failed)} passed, {len(failed)} failed")
    print("=" * 72)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
