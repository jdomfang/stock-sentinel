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


def test_scan_header_and_rows_share_one_alignment_contract() -> None:
    discovery = read("pages/Discovery.py")

    assert (
        "_SCAN_RESULT_COLUMNS = [1.75, 0.72, 0.92, 0.62, 1.15]"
        in discovery
    )
    assert '_header.columns(_SCAN_RESULT_COLUMNS, gap="small")' in discovery
    assert discovery.count('_SCAN_RESULT_COLUMNS, gap="small"') == 2
    assert (
        '["Stock", "Last close", signal_label, "Social posts", "Action"]'
        in discovery
    )
    assert '<span class="scan-social-posts">' in discovery
    assert "f'{_mentions}</span></div>'" in discovery


def test_selected_rows_and_actions_do_not_change_column_geometry() -> None:
    discovery = read("pages/Discovery.py")

    selected_css = discovery.split(
        '[class*="st-key-scan_row_selected_"] {', 1
    )[1].split("}", 1)[0]
    assert "padding-left" not in selected_css
    assert "padding-right" not in selected_css
    assert "box-shadow: inset" in selected_css
    viewing_css = discovery.split(".scan-view-result {", 1)[1].split("}", 1)[0]
    button_css = discovery.split(
        '[class*="st-key-scan_row_"] .stButton > button {', 1
    )[1].split("}", 1)[0]
    assert "min-height: 44px" in viewing_css
    assert "width: 100%" in viewing_css
    assert "white-space: nowrap" in viewing_css
    assert "min-height: 44px !important" in button_css
    assert "white-space: nowrap !important" in button_css
    assert 'use_container_width=True' in discovery


def test_master_detail_stacks_before_the_shortlist_becomes_cramped() -> None:
    discovery = read("pages/Discovery.py")

    assert '_workspace.columns([1.28, .82])' in discovery
    tablet_rule = discovery.split("@media (max-width: 1024px)", 1)[1].split(
        "@media (max-width: 720px)", 1
    )[0]
    assert ".st-key-scan_result_workspace" in tablet_rule
    assert "flex-wrap:wrap!important" in tablet_rule
    assert "flex:1 1 100%!important" in tablet_rule
    assert ".st-key-selected_analysis_panel {position:static" in tablet_rule


def test_mobile_result_metadata_stacks_labels_without_shrinking_targets() -> None:
    discovery = read("pages/Discovery.py")

    mobile_rule = discovery.split("@media (max-width: 720px)", 1)[1].split(
        "/* Hide Streamlit", 1
    )[0]
    assert ".scan-meta-cell" in mobile_rule
    assert "flex-direction: column" in mobile_rule
    assert "align-items: flex-start" in mobile_rule
    assert ".scan-social-posts {white-space: nowrap;}" in mobile_rule


def test_desktop_result_panel_is_contained_and_top_aligned() -> None:
    discovery = read("pages/Discovery.py")

    workspace_css = discovery.split(
        '.st-key-scan_result_workspace [data-testid="stHorizontalBlock"]:has(\n'
        "      .st-key-scan_workspace_results\n"
        "    ):has(.st-key-scan_workspace_analysis) {",
        1,
    )[1].split("}", 1)[0]
    column_css = discovery.split(
        '.st-key-scan_result_workspace [data-testid="stHorizontalBlock"]:has(\n'
        "      .st-key-scan_workspace_results\n"
        "    ):has(.st-key-scan_workspace_analysis) > "
        '[data-testid="stColumn"] {',
        1,
    )[1].split("}", 1)[0]
    assert "align-items:flex-start!important;gap:20px!important" in workspace_css
    assert "min-width:0!important" in column_css
    panel_css = discovery.split(
        ".st-key-selected_analysis_panel {", 1
    )[1].split("}", 1)[0]
    assert "position:sticky" in panel_css
    assert "padding:14px" in panel_css
    assert "border:1px solid" in panel_css
    assert "border-radius:var(--radius-panel)" in panel_css


def test_public_preview_uses_compact_aligned_table_geometry() -> None:
    home = read("pages/Home.py")

    preview_css = home.split(".ss-hero-preview {", 1)[1].split("}", 1)[0]
    table_css = home.split(".ss-hero-preview-table {", 1)[1].split("}", 1)[0]
    count_css = home.split(".ss-hero-preview-count {", 1)[1].split("}", 1)[0]
    result_css = home.split(".ss-hero-result {", 1)[1].split("}", 1)[0]
    cell_css = home.split(
        ".ss-hero-preview-table th,.ss-hero-preview-table td {", 1
    )[1].split("}", 1)[0]
    numeric_alignment_css = home.split(
        ".ss-hero-preview-table th:last-child,\n"
        "    .ss-hero-preview-table td:last-child {",
        1,
    )[1].split("}", 1)[0]
    assert "min-height:0" in preview_css
    assert "padding:16px" in preview_css
    assert "table-layout:fixed" in table_css
    assert "border-collapse:separate" in table_css
    assert ".ss-hero-preview-table col.stock {width:27%;" in home
    assert ".ss-hero-preview-table col.sentiment {width:43%;" in home
    assert ".ss-hero-preview-table col.posts {width:30%;" in home
    assert "padding:9px 12px" in cell_css
    assert "vertical-align:middle" in cell_css
    assert "font-variant-numeric:tabular-nums" in count_css
    assert "text-align:right" in numeric_alignment_css
    assert "margin-top:14px" in result_css
    assert "padding-top:12px" in result_css
    assert "min-height:420px" not in home


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
        test_scan_header_and_rows_share_one_alignment_contract,
        test_selected_rows_and_actions_do_not_change_column_geometry,
        test_master_detail_stacks_before_the_shortlist_becomes_cramped,
        test_mobile_result_metadata_stacks_labels_without_shrinking_targets,
        test_desktop_result_panel_is_contained_and_top_aligned,
        test_public_preview_uses_compact_aligned_table_geometry,
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
