#!/usr/bin/env python3
"""Focused regression contract for the delivered-result workspace repair."""

from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parent.parent


def read(relative: str) -> str:
    return (REPO / relative).read_text(encoding="utf-8")


def test_completion_is_communicated_without_a_layout_shifting_banner() -> None:
    discovery = read("pages/Discovery.py")
    ui = read("utils/ui.py")
    assert "Analysis ready for" not in discovery
    assert "The completed result is open beside the shortlist" not in discovery
    assert "View result" in discovery
    assert "analysis result</h2>" not in discovery
    assert 'element_id="selected-analysis"' in discovery
    assert 'role="status"' in ui


def test_full_breakdown_is_nonpaying_and_opens_in_place() -> None:
    discovery = read("pages/Discovery.py")
    deep = read("pages/Deep_Analysis.py")
    result = read("pages/Analysis_Result.py")
    ui = read("utils/ui.py")

    assert "render_delivered_analysis_result(" in discovery
    assert "render_delivered_analysis_result(" in deep
    assert 'key=f"delivered_analysis_breakdown_{safe_key}"' in ui
    assert 'label="View full breakdown"' in ui
    assert "render_full_analysis_expander(" in ui
    assert '"pages/Analysis_Result.py"' not in discovery
    assert '"pages/Analysis_Result.py"' not in deep
    assert "consume_credit" not in result
    assert "analyze_remote" not in result


def test_paid_summary_closes_delivery_before_optional_ui() -> None:
    deep = read("pages/Deep_Analysis.py")
    ui = read("utils/ui.py")

    helper = ui.split("def render_delivered_analysis_result", 1)[1]
    assert helper.index("render_recommendation_panel(") < helper.index(
        "on_summary_delivered()"
    )
    assert helper.index("on_summary_delivered()") < helper.index(
        "render_evidence_check("
    )
    assert "on_summary_delivered=_mark_summary_delivered" in deep
    assert deep.index("st.session_state.deep_analysis_card = _card") < deep.index(
        "render_delivered_analysis_result("
    )


def test_compact_result_metrics_finish_on_an_aligned_row() -> None:
    ui = read("utils/ui.py")
    assert (
        ".ss-decision-card.compact .ss-decision-financials > "
        ":last-child:nth-child(odd)"
    ) in ui
    assert "grid-column:1 / -1" in ui
    assert ".ss-decision-card.compact.embedded .ss-decision-context" in ui
    assert "grid-template-columns:repeat(4,minmax(0,1fr))" in ui
    assert ".ss-decision-card.compact.embedded .ss-decision-financials" in ui
    assert "grid-template-columns:repeat(3,minmax(0,1fr))" in ui
    assert "visible_reasons = rationale[:1] if embedded else rationale[:3]" in ui
    assert "if financial_tiles and not embedded else" in ui
    assert "if change_items and not embedded else" in ui
    compact_query = ui.split("@container (max-width:440px)", 1)[1].split(
        "@media (max-width:720px)", 1
    )[0]
    assert ".ss-decision-card.compact .ss-decision-head" in compact_query
    assert "display:block" in compact_query
    assert ".ss-decision-card.compact .ss-decision-signal" in compact_query
    assert "text-align:left" in compact_query


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
    shared_grid_css = discovery.split(
        "@media (min-width:721px)",
        1,
    )[1].split(".scan-stock-cell strong", 1)[0]
    assert "display: grid !important" in shared_grid_css
    assert "grid-template-columns:" in shared_grid_css
    assert "minmax(170px, 1.75fr)" in shared_grid_css
    assert "minmax(132px, 1.15fr)" in shared_grid_css
    assert "flex: none !important" in shared_grid_css
    assert "width: auto !important" in shared_grid_css
    assert "padding-inline: 8px !important" in shared_grid_css


def test_selected_rows_and_actions_do_not_change_column_geometry() -> None:
    discovery = read("pages/Discovery.py")

    selected_css = discovery.split(
        '[class*="st-key-scan_row_selected_"] {', 1
    )[1].split("}", 1)[0]
    assert "padding-left" not in selected_css
    assert "padding-right" not in selected_css
    assert "box-shadow: inset" in selected_css
    assert "border-bottom-color: transparent" in selected_css
    row_css = discovery.split(
        '[class*="st-key-scan_row_"] {', 1
    )[1].split("}", 1)[0]
    assert "padding: 0.72rem 0" in row_css
    assert "0.15rem" not in row_css
    assert 'class="scan-view-result" href="#selected-analysis"' in discovery
    assert 'aria-current="true">View result</a>' in discovery
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
    ui = read("utils/ui.py")
    assert 'role="status"' in ui
    assert 'aria-live="polite"' in ui
    assert 'aria-atomic="true"' in ui


def test_delivered_result_is_full_width_and_precedes_the_shortlist() -> None:
    discovery = read("pages/Discovery.py")

    assert '_workspace.columns([1.28, .82])' not in discovery
    result_container = (
        '_analysis_col = _workspace.container(key="scan_workspace_analysis")'
    )
    shortlist_container = (
        '_results_col = _workspace.container(key="scan_workspace_results")'
    )
    assert result_container in discovery
    assert shortlist_container in discovery
    assert discovery.index(result_container) < discovery.index(shortlist_container)


def test_mobile_result_metadata_stacks_labels_without_shrinking_targets() -> None:
    discovery = read("pages/Discovery.py")

    mobile_rule = discovery.split("@media (max-width: 720px)", 1)[1].split(
        "/* Hide Streamlit", 1
    )[0]
    assert ".scan-meta-cell" in mobile_rule
    assert "padding-inline: 8px !important" in mobile_rule
    assert "flex-direction: column" in mobile_rule
    assert "align-items: flex-start" in mobile_rule
    assert ".scan-social-posts {white-space: nowrap;}" in mobile_rule


def test_desktop_result_panel_is_contained_and_top_aligned() -> None:
    discovery = read("pages/Discovery.py")
    ui = read("utils/ui.py")

    panel_css = discovery.split(
        ".st-key-selected_analysis_panel {", 1
    )[1].split("}", 1)[0]
    assert "position:sticky" not in panel_css
    assert "box-sizing:border-box" in panel_css
    assert "width:100%" in panel_css
    assert "min-width:0" in panel_css
    assert "max-width:100%" in panel_css
    assert "overflow:hidden" not in panel_css
    # The wrapper owns geometry only; the shared canonical card owns all
    # visible chrome, preventing a bordered box nested inside another.
    assert "padding:" not in panel_css
    assert "border:" not in panel_css
    assert "background:" not in panel_css
    assert "def render_delivered_analysis_result(" in ui


def test_breakdown_is_nested_in_the_same_result_surface() -> None:
    discovery = read("pages/Discovery.py")
    ui = read("utils/ui.py")

    result_surface = discovery.split(
        'with st.container(key="selected_analysis_panel"):', 1
    )[1].split("else:\n        st.markdown(", 1)[0]
    assert "render_delivered_analysis_result(" in result_surface
    assert 'key=f"delivered_analysis_breakdown_{safe_key}"' in ui
    assert 'label="View full breakdown"' in ui
    assert '[class*="st-key-delivered_analysis_breakdown_"]' in ui


def test_embedded_result_keeps_semantic_heading_and_short_live_status() -> None:
    ui = read("utils/ui.py")

    assert '<h2 class="ss-decision-ticker">{ticker_safe}</h2>' in ui
    assert 'class="ss-decision-sr-only" role="status"' in ui
    assert "Analysis complete for {ticker_safe}." in ui
    article = ui.split('<article class="ss-decision-card', 1)[1].split(
        "</article>", 1
    )[0]
    assert 'role="status"' not in article.split("<header", 1)[0]


def test_analysis_failures_render_outside_action_cells() -> None:
    discovery = read("pages/Discovery.py")

    assert "def _queue_discovery_analysis(" in discovery
    assert "def _process_pending_discovery_analysis(" in discovery
    assert 'key="discovery_analysis_progress"' in discovery
    action_block = discovery.split("with col5:", 1)[1].split(
        "_results_col.markdown(", 1
    )[0]
    assert "on_click=_queue_discovery_analysis" in action_block
    assert "consume_credit(" not in action_block
    assert "analyze_remote(" not in action_block
    assert "st.error(" not in action_block


def test_scan_analysis_has_one_stable_processing_and_paint_cycle() -> None:
    discovery = read("pages/Discovery.py")
    processor = discovery.split(
        "def _process_pending_discovery_analysis", 1
    )[1].split("# One task command", 1)[0]

    assert '"request_id": new_request_id()' in discovery
    assert "_set_request_id(request_id)" in processor
    assert processor.count('key="discovery_analysis_progress"') == 1
    assert processor.index('key="discovery_analysis_progress"') < processor.index(
        'consume_credit(\n        "deep_analyze"'
    )
    assert "st.rerun()" not in processor
    finally_block = processor.split("finally:", 1)[1].split(
        "# No Streamlit calls occur", 1
    )[0]
    assert "overlay_slot.empty()" not in finally_block
    assert "complete_work(" in finally_block
    assert "refund_credit(" in finally_block
    assert "overlay_slot.empty()" in processor.split(
        "# No Streamlit calls occur", 1
    )[1]

    result_render = discovery.index(
        "render_delivered_analysis_result(\n                        card="
    )
    first_header = discovery.index(
        "def _render_scan_header", result_render
    )
    assert result_render < first_header


def test_market_scan_sector_cannot_be_overwritten_by_independent_analysis() -> None:
    discovery = read("pages/Discovery.py")
    deep = read("pages/Deep_Analysis.py")
    result = read("pages/Analysis_Result.py")

    result_sector = discovery.split("_result_sector = (", 1)[1].split(
        "st.markdown(", 1
    )[0]
    assert 'st.session_state.get("demo_scan_sector")' in result_sector
    assert result_sector.index("demo_scan_sector") < result_sector.index(
        "selected_sector"
    )
    assert "st.session_state.analysis_sector = sector" in deep
    assert "st.session_state.selected_sector = sector" not in deep
    assert "st.session_state.analysis_sector = result_sector" in discovery
    assert "args=(ticker_symbol, _result_sector)" in discovery
    assert 'strip().lower() == "unknown"' in discovery
    assert 'st.session_state.get("analysis_sector")' in result


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
        assert source.index("require_login(") < source.index(
            "render_top_nav("
        ), relative
        assert source.index("render_top_nav(") < source.index(
            "require_active_account("
        ), relative


def main() -> int:
    tests = [
        test_completion_is_communicated_without_a_layout_shifting_banner,
        test_full_breakdown_is_nonpaying_and_opens_in_place,
        test_paid_summary_closes_delivery_before_optional_ui,
        test_compact_result_metrics_finish_on_an_aligned_row,
        test_scan_header_and_rows_share_one_alignment_contract,
        test_selected_rows_and_actions_do_not_change_column_geometry,
        test_delivered_result_is_full_width_and_precedes_the_shortlist,
        test_mobile_result_metadata_stacks_labels_without_shrinking_targets,
        test_desktop_result_panel_is_contained_and_top_aligned,
        test_breakdown_is_nested_in_the_same_result_surface,
        test_embedded_result_keeps_semantic_heading_and_short_live_status,
        test_analysis_failures_render_outside_action_cells,
        test_scan_analysis_has_one_stable_processing_and_paint_cycle,
        test_market_scan_sector_cannot_be_overwritten_by_independent_analysis,
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
