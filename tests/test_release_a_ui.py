#!/usr/bin/env python3
"""Release A presentation contract, independent of the rendering platform."""

from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED.append(name) if condition else FAILED.append((name, detail)))
    print(f"  {'PASS' if condition else 'FAIL'}  {name}")


def main() -> int:
    discovery = (REPO / "pages" / "Discovery.py").read_text()
    home = (REPO / "pages" / "Home.py").read_text()
    deep = (REPO / "pages" / "Deep_Analysis.py").read_text()
    nav = (REPO / "utils" / "navigation.py").read_text()
    ui = (REPO / "utils" / "ui.py").read_text()
    tokens = (
        REPO / "assets" / "styles" / "stock-sentinel-tokens.css"
    ).read_text()

    print("=" * 72)
    print("  Release A UI: portable product rules and current adapter safety")
    print("=" * 72)

    check("portable design tokens use the ss namespace", "--ss-color-bg" in tokens)
    check("the current adapter loads the portable token file",
          "stock-sentinel-tokens.css" in ui)
    check("DOM mutation observers are gone",
          all("MutationObserver" not in src for src in (home, discovery, nav, ui)))
    check("recurring DOM patch loops are gone",
          all("setInterval" not in src for src in (home, discovery, nav, ui)))

    check("scan cost is disclosed before activation",
          "Run scan · 1 credit" in discovery)
    check("analysis cost is disclosed before activation",
          "Analyze · 1 credit" in discovery and "Analyze · 1 credit" in deep)
    check("a delivered analysis becomes a nonpaying in-page result",
          "scan-view-result" in discovery
          and "Viewing result" in discovery
          and 'key="selected_analysis_breakdown"' in discovery
          and 'label="View full breakdown"' in discovery)
    check("analysis renders in the page rather than a scrolling iframe",
          "render_recommendation_panel(" in discovery and
          "components.html(_panel_html" not in discovery)

    check("asserted scan sentiment enforces the evidence floor",
          "label in _ASSERTED and evidence >= 3" in discovery)
    check("scan ranking is evidence/attention based",
          '["_group", "Evidence", "Mentions", "Ticker"]' in discovery)
    check("completed results keep their original scan sector",
          '_result_sector = st.session_state.get("selected_sector") or sector'
          in discovery)
    check("mobile result values carry explicit labels",
          all(label in discovery for label in (
              "Last close</span>", "Sentiment</span>",
              "Evidence state</span>", "Attention</span>",
          )))

    check("navigation has a dedicated mobile layout",
          "ss_nav_mobile_links" in nav and "ss_nav_desktop" in nav)
    check("admin navigation remains gated and reachable",
          "ADMIN_EMAIL" in nav and "pages/Admin.py" in nav)
    check("the active page is exposed to assistive technology",
          'aria-current="page"' in nav)

    print("\n" + "=" * 72)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for name, detail in FAILED:
        print(f"    - {name}: {detail}")
    print("=" * 72)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
