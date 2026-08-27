"""Nonpaying full-breakdown view for a delivered Deep Analyze result.

This page owns presentation only. It reads the result already held in session
state and cannot run analysis, consume a credit, refund a credit, or persist a
second result.
"""

from __future__ import annotations

import html
from pathlib import Path as _Path
import sys as _sys

import streamlit as st

_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

from utils.guard import require_active_account
from utils.navigation import render_sidebar_navigation, render_top_nav
from utils.ui import (
    apply_theme,
    close_page,
    render_evidence_check,
    render_full_analysis_expander,
    render_recommendation_panel,
    render_workflow_hint,
)


st.set_page_config(
    page_title="Analysis Result - Stock Sentinel",
    page_icon="🔎",
    layout="wide",
    initial_sidebar_state="collapsed",
)

render_sidebar_navigation()
apply_theme()

from utils.auth import flush_pending_rt_save

flush_pending_rt_save()
profile = require_active_account(after_auth_page="Analysis_Result")
credits = int((profile or {}).get("credits") or 0)
render_top_nav(active="deep_analyze", credits=credits)

st.markdown(
    """
    <style>
      div[data-testid="stMainBlockContainer"] {
        max-width:1240px;margin:0 auto;
        padding-left:clamp(16px,3vw,32px);padding-right:clamp(16px,3vw,32px);
      }
      .ss-result-back {margin:.15rem 0 .85rem;}
      .ss-result-header {margin:0 0 .8rem;max-width:900px;}
      .ss-result-header .eyebrow {
        color:var(--accent);font-size:.7rem;font-weight:780;
        letter-spacing:.09em;text-transform:uppercase;
      }
      .ss-result-header h1 {
        margin:.25rem 0 .2rem;font-size:var(--ss-font-page-title);
        letter-spacing:-.04em;line-height:1.05;
      }
      .ss-result-header p {margin:0;color:var(--muted);font-size:.9rem;}
      .st-key-full_result_breakdown {
        margin-top:1rem;padding:14px;border:1px solid rgba(148,163,184,.16);
        border-radius:var(--radius-panel);background:rgba(8,15,30,.55);
      }
      .st-key-full_result_breakdown details {
        border:0!important;background:transparent!important;
      }
      .st-key-full_result_breakdown details > summary {display:none!important;}
      @media (max-width:720px) {
        .ss-result-header h1 {font-size:2.3rem;}
        .st-key-full_result_breakdown {padding:10px;}
      }
    </style>
    <div class="clawd-app-wrapper">
    """,
    unsafe_allow_html=True,
)

result_origin = str(
    st.session_state.get("analysis_result_origin") or "market_scan"
).strip().lower()
if result_origin == "deep_analyze":
    back_page = "pages/Deep_Analysis.py"
    back_label = "← Back to Deep Analyze"
else:
    back_page = "pages/Discovery.py"
    back_label = "← Back to Market Scan results"

with st.container(key="result_back"):
    st.page_link(
        back_page,
        label=back_label,
    )


def _bail() -> None:
    """Close shared page chrome before ending an empty-result request."""
    close_page()
    st.stop()

ticker = str(st.session_state.get("selected_ticker") or "").strip().upper()
sector = str(st.session_state.get("selected_sector") or "").strip()
card = st.session_state.get("deep_analysis_card") or {}
analysis_results = st.session_state.get("deep_analysis_results") or {}

if not ticker or not card:
    render_workflow_hint(
        title="No completed analysis is open",
        message=(
            "A full breakdown appears only after Deep Analyze has delivered "
            "a result. Opening this page never uses a credit."
        ),
        steps=[
            "Return to Market Scan or Deep Analyze.",
            "Run one clearly labelled one-credit analysis.",
            "Open View full breakdown from the delivered result.",
        ],
    )
    _bail()

evidence = card.get("evidence") or {}
movement = card.get("movement") or {}
price_points = int(evidence.get("price_points") or 0)
independent_voices = evidence.get("independent_voices")
raw_mentions = evidence.get("mentions")
if independent_voices is not None:
    shown_mentions = int(independent_voices or 0)
    suffix = "s" if shown_mentions != 1 else ""
    evidence_label = f"{shown_mentions} independent source{suffix}"
elif raw_mentions is not None:
    shown_mentions = int(raw_mentions or 0)
    suffix = "s" if shown_mentions != 1 else ""
    evidence_label = f"{shown_mentions} post{suffix} analyzed"
else:
    shown_mentions = 0
    evidence_label = "Evidence count unavailable"

tile_values = {
    str(tile.get("key") or ""): str(tile.get("value") or "Unavailable")
    for tile in (card.get("tiles") or [])
}
horizon_days = int(movement.get("horizon_days") or 0)
horizon = (
    f"{horizon_days} trading days"
    if horizon_days else "Short-term horizon"
)
summary = {
    "recommendation": card.get("verdict") or "—",
    "confidence": card.get("confidence") or "—",
    "avg_sentiment": card.get("avg_sentiment"),
    "rationale": card.get("rationale") or [],
}

st.markdown(
    f'<header class="ss-result-header">'
    f'<div class="eyebrow">Deep Analyze · delivered result</div>'
    f'<h1>{html.escape(ticker)}</h1>'
    f'<p>Review the decision factors, evidence coverage, and conditions that '
    f'could change this recommendation.</p></header>',
    unsafe_allow_html=True,
)

render_recommendation_panel(
    ticker=ticker,
    sector=sector,
    ai_summary=summary,
    current_price=tile_values.get("last_price", "Unavailable"),
    projected_gain=tile_values.get("range_30d", "Unavailable"),
    drawdown_first=tile_values.get("drawdown_first", "Unavailable"),
    mentions=shown_mentions,
    price_points=price_points,
    horizon=horizon,
    freshness="Analysis generated for this request",
    evidence_label=evidence_label,
    would_change=card.get("would_change") or [],
)

if card.get("pillars"):
    st.markdown("## Decision factors")
    render_evidence_check(
        card,
        ticker,
        show_header=False,
        show_change=False,
    )

if analysis_results:
    with st.container(key="full_result_breakdown"):
        st.markdown("### Evidence by signal")
        render_full_analysis_expander(
            analysis_results,
            key_suffix=f"_result_{ticker}",
            expanded=True,
            label="Evidence by signal",
        )
else:
    st.caption(
        "Detailed signal excerpts are unavailable for this earlier result; "
        "the delivered recommendation remains available above."
    )

close_page()
