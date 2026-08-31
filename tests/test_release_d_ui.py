#!/usr/bin/env python3
"""Release D premium-flow convergence source contract."""

from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED.append(name) if condition else FAILED.append((name, detail)))
    print(f"  {'PASS' if condition else 'FAIL'}  {name}")


def main() -> int:
    home = (REPO / "pages" / "Home.py").read_text()
    discovery = (REPO / "pages" / "Discovery.py").read_text()
    deep = (REPO / "pages" / "Deep_Analysis.py").read_text()
    result = (REPO / "pages" / "Analysis_Result.py").read_text()
    nav = (REPO / "utils" / "navigation.py").read_text()
    ui = (REPO / "utils" / "ui.py").read_text()
    roadmap = (REPO / "docs" / "ui-product-roadmap.md").read_text()
    tokens = (
        REPO / "assets" / "styles" / "stock-sentinel-tokens.css"
    ).read_text()

    print("=" * 72)
    print("  Release D UI: premium flow and layout convergence")
    print("=" * 72)

    check(
        "Release D is recorded against the approved v4 composition",
        "Release D — Flow and layout convergence" in roadmap
        and "stock-sentinel-premium-flow-v4" in roadmap,
    )
    check(
        "portable tokens own workspace and display scale",
        "--ss-workspace-max-width" in tokens
        and "--ss-marketing-max-width" in tokens
        and "--ss-font-display" in tokens,
    )
    check(
        "public Home uses a two-column story and full-width decision workspace",
        'key="home_public_intro"' in home
        and "_decision_workspace_html(" in home
        and 'class="ss-decision-grid"' in home
        and "Product preview" in home,
    )
    check(
        "public preview is clearly illustrative",
        'aria-label="Illustrative decision workspace"' in home
        and home.count("illustrative") >= 2,
    )
    check(
        "landing workflow explains scan, analyze, and evidence",
        'id="how-it-works"' in home
        and "Scan a sector" in home
        and "Analyze a ticker" in home
        and "Inspect the evidence" in home,
    )
    check(
        "landing trust strip explains the complete credit promise",
        "Costs shown before every action" in home
        and "Eligible failed runs are automatically refunded" in home
        and "Credits never expire" in home,
    )
    check(
        "public navigation has distinct login and start-free actions",
        "def _signup_control" in nav
        and '"Start free"' in nav
        and '"Log in"' in nav,
    )
    check(
        "scan results create a full-width result workspace",
        'key="scan_result_workspace"' in discovery
        and 'key="scan_workspace_results"' in discovery
        and 'key="scan_workspace_analysis"' in discovery
        and '_workspace.columns([1.28, .82])' not in discovery,
    )
    check(
        "delivered scan-row action becomes nonpaying viewing state",
        "View result" in discovery
        and "View result ↓" not in discovery
        and '_is_selected and st.session_state.get("deep_analysis_card")'
        in discovery,
    )
    check(
        "selected result uses the canonical complete decision summary",
        "render_delivered_analysis_result(" in discovery
        and "compact=True" not in discovery
        and "embedded=True" not in discovery
        and 'key="selected_analysis_panel"' in discovery
        and "def render_delivered_analysis_result(" in ui,
    )
    check(
        "selected result opens its full breakdown without leaving context",
        'key=f"delivered_analysis_breakdown_{safe_key}"' in ui
        and 'label="View full breakdown"' in ui
        and "render_delivered_analysis_result(" in discovery
        and "render_delivered_analysis_result(" in deep
        and '"pages/Analysis_Result.py"' not in discovery
        and '"pages/Analysis_Result.py"' not in deep,
    )
    check(
        "full breakdown consumes no credit and runs no analysis",
        "consume_credit" not in result
        and "refund_credit" not in result
        and "analyze_remote" not in result,
    )
    check(
        "full breakdown reads only the delivered session result",
        'st.session_state.get("deep_analysis_card")' in result
        and 'st.session_state.get("deep_analysis_results")' in result
        and "expanded=True" in result,
    )
    check(
        "result summaries expose what could change the call",
        "would_change: list[str] | None" in ui
        and "What would change this" in ui,
    )
    check(
        "result workspace retains labelled responsive flow",
        ".st-key-selected_analysis_panel" in discovery
        and '[class*="st-key-delivered_analysis_breakdown_"]' in ui
        and "grid-template-columns:repeat(2,minmax(0,1fr))" in ui
        and "scan-mobile-label" in discovery,
    )

    print("\n" + "=" * 72)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for name, detail in FAILED:
        print(f"    - {name}: {detail}")
    print("=" * 72)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
