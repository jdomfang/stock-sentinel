#!/usr/bin/env python3
"""Focused regression contract for the delivered-result workspace repair."""

from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parent.parent


def read(relative: str) -> str:
    return (REPO / relative).read_text(encoding="utf-8")


def test_completion_is_communicated_without_a_layout_shifting_banner() -> None:
    discovery = read("pages/Discovery.py")
    assert "Analysis ready for" not in discovery
    assert "The completed result is open beside the shortlist" not in discovery
    assert "Viewing result" in discovery
    assert "analysis result</h2>" in discovery


def test_full_breakdown_is_nonpaying_and_opens_in_place() -> None:
    discovery = read("pages/Discovery.py")
    deep = read("pages/Deep_Analysis.py")
    result = read("pages/Analysis_Result.py")

    assert 'key="selected_analysis_breakdown"' in discovery
    assert 'label="View full breakdown"' in discovery
    assert 'label="View full breakdown"' in deep
    assert "render_full_analysis_expander," in deep.split(
        "from utils.ui import (", 1
    )[1].split(")", 1)[0]
    deep_breakdown = deep.split(
        'with st.container(key="deep_full_result_link"):', 1
    )[1].split("# Written AFTER delivery", 1)[0]
    assert "render_full_analysis_expander(\n                    analysis_results," in deep_breakdown
    assert '"pages/Analysis_Result.py"' not in discovery
    assert '"pages/Analysis_Result.py"' not in deep
    assert "consume_credit" not in result
    assert "analyze_remote" not in result


def test_compact_result_metrics_finish_on_an_aligned_row() -> None:
    ui = read("utils/ui.py")
    assert (
        ".ss-decision-card.compact .ss-decision-financials > "
        ":last-child:nth-child(odd)"
    ) in ui
    assert "grid-column:1 / -1" in ui


def test_prefilled_ticker_uses_explicit_cross_browser_contrast() -> None:
    adapter = read("assets/styles/stock-sentinel-streamlit-adapter.css")
    assert '[data-testid="stTextInput"] input' in adapter
    assert "-webkit-text-fill-color: var(--ss-color-text)" in adapter
    assert "caret-color: var(--ss-color-action)" in adapter
    assert '[data-testid="stTextInput"] input::placeholder' in adapter


def test_every_page_loads_theme_before_visible_navigation() -> None:
    for relative in (
        "pages/Home.py", "pages/FAQ.py", "pages/Contact.py",
        "pages/How_It_Works.py", "pages/Trust_Center.py", "pages/Admin.py",
        "pages/Discovery.py", "pages/Deep_Analysis.py",
        "pages/Analysis_Result.py", "pages/Account.py",
    ):
        source = read(relative)
        assert source.index("apply_theme()") < source.index(
            "render_top_nav("
        ), relative

    for relative in (
        "pages/Discovery.py", "pages/Deep_Analysis.py",
        "pages/Analysis_Result.py", "pages/Account.py",
    ):
        source = read(relative)
        assert source.index("render_top_nav(") < source.index(
            "require_active_account("
        ), relative


def main() -> int:
    tests = [
        test_completion_is_communicated_without_a_layout_shifting_banner,
        test_full_breakdown_is_nonpaying_and_opens_in_place,
        test_compact_result_metrics_finish_on_an_aligned_row,
        test_prefilled_ticker_uses_explicit_cross_browser_contrast,
        test_every_page_loads_theme_before_visible_navigation,
    ]
    failed: list[tuple[str, str]] = []
    print("=" * 72)
    print("  Delivered result workspace and navigation UX")
    print("=" * 72)
    for test in tests:
        try:
            test()
        except Exception as exc:
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
