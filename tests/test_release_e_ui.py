#!/usr/bin/env python3
"""Release E responsive production-polish source contract."""

from __future__ import annotations

import json
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED.append(name) if condition else FAILED.append((name, detail)))
    print(f"  {'PASS' if condition else 'FAIL'}  {name}")


def read(relative: str) -> str:
    return (REPO / relative).read_text(encoding="utf-8")


def main() -> int:
    tokens = read("assets/styles/stock-sentinel-tokens.css")
    components = read("assets/styles/stock-sentinel-components.css")
    adapter = read("assets/styles/stock-sentinel-streamlit-adapter.css")
    ui = read("utils/ui.py")
    nav = read("utils/navigation.py")
    auth = read("pages/Auth.py")
    home = read("pages/Home.py")
    discovery = read("pages/Discovery.py")
    deep = read("pages/Deep_Analysis.py")
    billing = read("utils/billing.py")
    roadmap = read("docs/ui-product-roadmap.md")
    manifest = json.loads(read("docs/design/release-e-qa/manifest.json"))
    expected_public_routes = [
        {"route": "Home", "marker": "Finding short-term opportunities"},
        {"route": "How_It_Works", "marker": "How Stock Sentinel works"},
        {"route": "FAQ", "marker": "Search FAQs"},
        {"route": "Contact", "marker": "Send a message"},
        {"route": "Trust_Center", "marker": "Data sources and freshness"},
        {"route": "Auth", "marker": "Welcome back"},
    ]
    expected_authenticated_surfaces = [
        "Market Scan empty, loading, failure, sparse-evidence and results",
        "Deep Analyze empty, loading, failure and delivered result",
        "Analysis Result",
        "Account zero-credit, purchase-review and payment-return",
    ]
    required_acceptance = {
        "No horizontal document overflow",
        "Every interactive target is at least 44 CSS pixels high",
        "Primary actions use interaction cyan, never host coral",
        "Amber appears only for caution and Watch semantics",
        "Bullish/Bearish/Neutral and Buy/Watch/Avoid include text labels",
        "Keyboard focus is visible and follows source order",
        "Loading and feedback states expose live-region semantics",
        "Reduced-motion preferences disable nonessential animation",
        "No scan, analysis or checkout is triggered by visual QA",
    }
    worker_failure = discovery.split(
        'if "error" in _holder:', 1
    )[1].split("# ONE SHAPE", 1)[0]
    normalized_scan_failure = discovery.split(
        "        if _err:", 1
    )[1].split("        if _x_err", 1)[0]
    zero_post_upstream_failure = discovery.split(
        "        if _posts == 0:", 1
    )[1].split("            else:", 1)[0]
    catch_all_scan_failure = discovery.split(
        '    except Exception:\n        logger.exception("Discovery scan failed")', 1
    )[1].split("    finally:", 1)[0]

    print("=" * 72)
    print("  Release E UI: responsive production polish")
    print("=" * 72)

    check(
        "Release E keeps product semantics independent of the host",
        "Host theme defaults are never a source" in roadmap
        and "disposable Streamlit adapter" in roadmap,
    )
    check(
        "portable tokens define every interaction and feedback state",
        all(name in tokens for name in (
            "--ss-color-action-hover", "--ss-color-action-pressed",
            "--ss-color-action-ink", "--ss-color-info-surface",
            "--ss-color-success-surface", "--ss-color-warning-surface",
            "--ss-color-error-surface",
        )),
    )
    check(
        "portable components contain no Streamlit selectors",
        ".ss-system-state" in components
        and ".ss-processing-state" in components
        and "data-testid" not in components
        and "stButton" not in components,
    )
    check(
        "the shared host-widget adapter is isolated and loaded last",
        "_STREAMLIT_ADAPTER_CSS_PATH" in ui
        and "adapter_css" in ui
        and "stock-sentinel-streamlit-adapter.css" in ui,
    )
    check(
        "primary and form-submit actions cannot inherit host coral",
        'button[data-testid^="stBaseButton-primary"]' in adapter
        and 'a[data-testid^="stBaseLinkButton-primary"]' in adapter
        and '[data-testid="stFormSubmitButton"] button[kind="primary"]' in adapter
        and "var(--ss-color-action)" in adapter
        and "var(--ss-color-action-rest-end)" in home
        and "var(--ss-color-action-rest-end)" in discovery
        and "rgba(14,116,144,.95)" not in home
        and "rgba(14,116,144,.95)" not in discovery,
    )
    check(
        "radio and checkbox selections use the product action token",
        '[data-testid="stRadio"] input' in adapter
        and '[data-testid="stCheckbox"] input' in adapter
        and '[data-baseweb="radio"]:has(input:checked) > span:first-child' in adapter
        and '[data-baseweb="checkbox"]:has(input:checked) > span:first-child' in adapter
        and "accent-color: var(--ss-color-action)" in adapter,
    )
    check(
        "Auth consumes portable tokens instead of literal primary colors",
        "var(--ss-color-action)" in auth
        and "#ff4b4b" not in auth.lower()
        and "255,75,75" not in auth.replace(" ", ""),
    )
    check(
        "mobile navigation has explicit nonwrapping keyed rows",
        'key="ss_nav_mobile_primary"' in nav
        and '[data-testid="stColumn"]' in nav
        and "flex-wrap:nowrap" in nav,
    )
    check(
        "stale Streamlit column selectors are removed from product pages",
        all('data-testid="column"' not in read(path) for path in (
            "pages/Home.py", "pages/Discovery.py", "pages/Deep_Analysis.py",
            "pages/Account.py", "pages/Auth.py", "pages/How_It_Works.py",
        )),
    )
    check(
        "feedback states map errors and nonerrors to correct live regions",
        'role = "alert" if normalized == "error" else "status"' in ui
        and 'live = "assertive" if normalized == "error" else "polite"' in ui
        and 'aria-live="{live}" aria-atomic="true"' in ui
        and 'role="status" aria-live="polite" aria-atomic="true"' in billing,
    )
    check(
        "scan and analysis loading states share one portable component",
        "processing_state_html(" in discovery
        and "processing_state_html(" in deep
        and "ss-processing-state" in components,
    )
    check(
        "paid failures are semantic and never suggest retry during a pending refund",
        discovery.count("render_system_state(") >= 10
        and "_deep_state" in discovery
        and "If your credit was not returned" in discovery
        and "if _refunded and _disc_holder.get(\"pre_spend\")" in discovery
        and "⚠️ {_deep_error}" not in discovery
        and "if refunded and retry_ok" in deep
        and "If your credit was not returned" in deep,
    )
    check(
        "scan retry guidance requires confirmed refund and pre-spend evidence",
        "_retryable = bool(_r.retryable)" in discovery
        and "if _refunded and _retryable" in normalized_scan_failure
        and '_kind in ("network", "transport")' in normalized_scan_failure
        and 'meta=""' in worker_failure
        and "Try again" not in worker_failure
        and 'meta=""' in catch_all_scan_failure
        and "Try again" not in catch_all_scan_failure
        and 'meta=""' in zero_post_upstream_failure
        and "Try again" not in zero_post_upstream_failure,
    )
    check(
        "zero-credit interruption is semantic and actionable",
        'class="ss-system-state" data-kind="warning" role="status"' in billing
        and "Buy credits" in billing
        and "Credits never expire" in billing,
    )
    check(
        "touch-target contract covers route-specific and shared controls",
        "--ss-control-min-height: 44px" in tokens
        and '[data-baseweb="select"],' in adapter
        and '[data-baseweb="input"],' in adapter
        and '[data-baseweb="select"] > div' in adapter
        and '[data-baseweb="input"] > div' in adapter
        and '[data-testid="stFormSubmitButton"] button' in adapter
        and '[data-testid="stPageLink"] a' in adapter
        and '[data-testid="stLinkButton"] a' in adapter
        and '[data-testid="stCheckbox"] label' in adapter
        and "color: var(--ss-color-action) !important" in adapter
        and ".st-key-discovery_control_row" in adapter
        and ".st-key-deep_control_row" in adapter
        and "min-height: 44px !important" in adapter
        and "min-height: 48px !important" in adapter
        and "min-height:var(--ss-control-min-height)" in auth
        and ".st-key-how_trust_link" in read("pages/How_It_Works.py"),
    )
    check(
        "focus and reduced-motion behavior are adapter-wide",
        ":focus-visible" in adapter
        and "prefers-reduced-motion" in adapter
        and "animation-duration: 0.01ms" in adapter,
    )
    check(
        "visual acceptance matrix specifies every route, state and viewport",
        manifest.get("release") == "E"
        and manifest.get("viewports") == [
            {"name": "mobile", "width": 390, "height": 844},
            {"name": "tablet", "width": 768, "height": 1024},
            {"name": "compact-desktop", "width": 1024, "height": 768},
            {"name": "desktop", "width": 1440, "height": 900},
        ]
        and manifest.get("public_routes") == expected_public_routes
        and manifest.get("authenticated_surfaces") == expected_authenticated_surfaces
        and set(manifest.get("acceptance", [])) == required_acceptance,
    )

    print("\n" + "=" * 72)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for name, detail in FAILED:
        print(f"    - {name}: {detail}")
    print("=" * 72)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
