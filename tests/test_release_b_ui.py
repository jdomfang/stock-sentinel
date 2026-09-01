#!/usr/bin/env python3
"""Release B landing and decision-clarity source contract."""

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
    auth = (REPO / "pages" / "Auth.py").read_text()
    discovery = (REPO / "pages" / "Discovery.py").read_text()
    deep = (REPO / "pages" / "Deep_Analysis.py").read_text()
    nav = (REPO / "utils" / "navigation.py").read_text()
    ui = (REPO / "utils" / "ui.py").read_text()
    tokens = (
        REPO / "assets" / "styles" / "stock-sentinel-tokens.css"
    ).read_text()

    print("=" * 72)
    print("  Release B UI: landing and decision clarity")
    print("=" * 72)

    check(
        "all delivered analysis entry points use the shared decision summary",
        "render_delivered_analysis_result(" in deep
        and "render_delivered_analysis_result(" in discovery
        and "def render_delivered_analysis_result(" in ui,
    )
    check(
        "legacy probability-like bars are absent from decision summaries",
        "_bar_pct" not in ui and "_bar_pct" not in discovery,
    )
    check(
        "live signal horizon comes from the result model",
        'movement.get("horizon_days")' in ui
        and 'view["horizon"]' in ui,
    )
    check(
        "analysis generation time does not claim evidence freshness",
        'freshness="Analysis generated now"' in discovery
        and 'freshness="Analysis generated now"' in deep
        and "Updated just now" not in ui,
    )
    check(
        "degraded evidence remains posts rather than independent clusters",
        'elif raw_mentions is not None' in ui
        and "post{suffix} analyzed" in ui,
    )
    check(
        "non-finite sentiment is rendered as unscored",
        "math.isfinite(avg_sentiment)" in ui,
    )
    check(
        "landing preview is capped and links a selected analysis ticker",
        "limit: int = 5" in home
        and "preferred_tickers=_demo_available" in home
        and 'selected = " selected"' in home,
    )
    check(
        "demo product data is explicitly illustrative",
        home.lower().count("illustrative") >= 2,
    )
    check(
        "landing proof language avoids unsupported speed/freshness claims",
        "not live market data" in home
        and "not probability of return" in home
        and "Volatility context · not a forecast" in home
        and "Results in under 60 seconds" not in home,
    )
    check(
        "landing controls and mobile cards use accessible sizing",
        "min-height:52px" in home
        and '@media (max-width:800px)' in home
        and '.ss-b5-scan-grid {grid-template-columns:1fr;}' in home
        and 'flex:1 1 100%!important' in home,
    )
    check(
        "the product preview is named for assistive technology",
        'aria-label="Illustrative decision workspace"' in home,
    )
    check(
        "full breakdown is keyboard reachable and mobile contained",
        'class="ss-breakdown-scroll"' in ui
        and 'tabindex="0"' in ui
        and "overflow-wrap:anywhere" in ui,
    )
    check(
        "create-account intent survives auth reruns",
        'key="auth_mode"' in auth
        and 'st.session_state["auth_mode"]' in auth,
    )
    check(
        "navigation retains hover and admin differentiation",
        "background:rgba(56,189,248,.07)!important" in nav
        and "border:1px solid rgba(148,163,184,.24)!important" in nav,
    )
    check(
        "portable tokens include product semantics",
        all(token in tokens for token in (
            "--ss-color-sentiment-bullish",
            "--ss-color-recommendation-watch",
            "--ss-color-recommendation-avoid",
        )),
    )

    print("\n" + "=" * 72)
    print(f"  {len(PASSED)} passed, {len(FAILED)} failed")
    for name, detail in FAILED:
        print(f"    - {name}: {detail}")
    print("=" * 72)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
