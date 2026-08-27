#!/usr/bin/env python3
"""Release C account and transaction-trust source contract."""

from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED.append(name) if condition else FAILED.append((name, detail)))
    print(f"  {'PASS' if condition else 'FAIL'}  {name}")


def main() -> int:
    account = (REPO / "pages" / "Account.py").read_text()
    trust = (REPO / "pages" / "Trust_Center.py").read_text()
    auth = (REPO / "pages" / "Auth.py").read_text()
    discovery = (REPO / "pages" / "Discovery.py").read_text()
    deep = (REPO / "pages" / "Deep_Analysis.py").read_text()
    home = (REPO / "pages" / "Home.py").read_text()
    billing = (REPO / "utils" / "billing.py").read_text()
    nav = (REPO / "utils" / "navigation.py").read_text()
    ui = (REPO / "utils" / "ui.py").read_text()
    roadmap = (REPO / "docs" / "ui-product-roadmap.md").read_text()

    print("=" * 72)
    print("  Release C UI: account and transaction trust")
    print("=" * 72)

    check(
        "account is a persistent signed-in destination",
        '"account"' in nav
        and '"pages/Account.py"' in nav
        and 'label="Account"' in nav
        and 'active="account"' in account,
    )
    check(
        "account reads existing profile state without owning credit behavior",
        "require_active_account()" in account
        and "billing.render_buy_credits" in account
        and "consume_credit" not in account
        and "refund_credit" not in account,
    )
    check(
        "purchase handoff has an explicit review state",
        "Purchase review" in billing
        and "One-time purchase" in billing
        and "Credit expiration" in billing
        and "Continue to Stripe" in billing
        and 'st.button("Not now"' in billing,
    )
    check(
        "Stripe remains the payment handoff",
        "st.link_button" in billing
        and "get_checkout_url(uid)" in billing
        and "Secure Stripe checkout" in billing,
    )
    check(
        "authentication uses a contained responsive shell",
        'key="auth_shell"' in auth
        and 'key="auth_form_panel"' in auth
        and "auth-value-list" in auth,
    )
    check(
        "auth mode intent still survives reruns",
        'key="auth_mode"' in auth
        and 'st.session_state["auth_mode"]' in auth,
    )
    check(
        "obsolete auth divider DOM mutation is removed",
        "Remove the divider bar line" not in auth
        and "radioElement.parentElement" not in auth,
    )
    check(
        "password-manager and remember-device contracts remain present",
        "autocomplete','username" in auth
        and "ss_remember_code" in auth
        and "Remember me on this device" in auth,
    )
    check(
        "task pages share a quiet empty-state contract",
        "def render_workflow_hint" in ui
        and "render_workflow_hint(" in discovery
        and "render_workflow_hint(" in deep,
    )
    check(
        "market and recommendation taxonomies stay separate in empty states",
        "reports social sentiment only" in discovery
        and "separate Buy, Watch, or Avoid recommendation" in deep,
    )
    check(
        "trust center consolidates the required credibility surfaces",
        all(label in trust for label in (
            "Methodology", "Data sources", "Privacy overview",
            "Use and risk overview",
        )),
    )
    check(
        "trust center is discoverable from the shared footer",
        'st.page_link("pages/Trust_Center.py", label="Trust Center")' in ui,
    )
    check(
        "authenticated home uses one compact wallet band",
        'key="home_credit_hub"' in home
        and "1 credit runs one scan or analysis" in home,
    )
    check(
        "spending task headers avoid stale balance claims",
        'render_top_nav(active="market_scan")' in discovery
        and 'render_top_nav(active="deep_analyze")' in deep
        and "billing.render_credit_meter(profile=_profile" in discovery
        and "billing.render_credit_meter(profile=_profile" in deep,
    )
    check(
        "Release C remains explicitly presentation-only",
        "presentation changes only" in roadmap
        and "existing authentication, payment, credit, database, and API paths" in roadmap,
    )

    print("\n" + "=" * 72)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for name, detail in FAILED:
        print(f"    - {name}: {detail}")
    print("=" * 72)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
