import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import time
from typing import Dict, List, Any

from utils.navigation import render_sidebar_navigation, render_top_nav
from utils.ui import open_page, close_page, GENERIC_ERROR_TEXT, safe_ui
from utils.finance import get_stock_data
from utils.projections import simple_projection
from utils.deep_analysis import ANALYSIS_PROMPTS, run_deep_analysis, generate_ai_summary

# Page configuration
st.set_page_config(
    page_title="Deep Analysis - Stock Sentinel",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Sidebar navigation
render_sidebar_navigation()
render_top_nav()

from utils.guard import require_active_account
from utils.credits import consume_credit

_profile = require_active_account()

open_page(
    title="Deep Analysis",
    subtitle="Full breakdown: sentiment, catalysts, risks, and an on-demand projection.",
)

st.subheader("Stock Analysis")

ticker = st.text_input(
    "Stock Ticker",
    "NVDA",
    help="Enter stock ticker symbol (e.g., NVDA, AAPL, TSLA)",
)

# Auto-sector: Deep analysis can run without sector input. Default to unknown.
sector = "unknown"

# Main analysis button
if st.button("🔬 Run Deep Analysis", type="primary"):
    ok, err = consume_credit("deep_analyze")
    if not ok:
        st.error(err)
        st.stop()
    if not ticker.strip():
        st.error("Please enter a stock ticker.")
    else:
        with st.spinner("Running deep analysis... This may take a moment."):
            analysis_results = safe_ui(
                lambda: run_deep_analysis(ticker.upper(), sector),
                context="deep_analysis.run_deep_analysis",
            )
            if not analysis_results:
                st.stop()

        # Display results
        st.success("✅ Deep analysis complete!")

        # AI-powered summary
        ai_summary = generate_ai_summary(analysis_results)
        
        # Fetch financial data for projections (best-effort; always display results or Unavailable)
        current_price = "Unavailable"
        current_price_reason = "Not fetched"
        projected_gain = "Unavailable"
        projected_gain_reason = "Need price data"
        hold_days = "Unavailable"
        hold_days_reason = "Need projection"
        price_points = 0

        try:
            stock_data = get_stock_data(ticker.upper())
            if stock_data.get("error") is None and stock_data.get("prices"):
                prices = stock_data.get("prices") or []
                price_points = len(prices)
                last_px = prices[-1]
                if isinstance(last_px, (int, float)):
                    current_price = f"${last_px:.2f}"
                    current_price_reason = ""
                else:
                    current_price_reason = "Invalid price"

                # Calculate projections
                projection = simple_projection(prices, ai_summary["avg_sentiment"], days=30)
                if projection.get("error") is None:
                    p10 = projection.get("gain_p10")
                    p90 = projection.get("gain_p90")
                    if p10 is not None and p90 is not None:
                        projected_gain = f"{p10:.1f}–{p90:.1f}%"
                    else:
                        projected_gain = f"{float(projection.get('avg_gain', 0.0)):.1f}%"
                    projected_gain_reason = ""

                    hold_days = f"{int(projection.get('suggested_hold_days', 0))} days"
                    hold_days_reason = ""
                else:
                    projected_gain_reason = projection.get("error") or "Projection failed"
                    hold_days_reason = "Projection failed"
            else:
                # Hide raw provider errors from users
                current_price_reason = "Data unavailable"
                projected_gain_reason = "Data unavailable"
                hold_days_reason = "Data unavailable"
        except Exception:
            # Hide raw exception details from users; full trace should be in server logs
            current_price_reason = GENERIC_ERROR_TEXT
            projected_gain_reason = GENERIC_ERROR_TEXT
            hold_days_reason = GENERIC_ERROR_TEXT
        
        st.subheader("🧠 AI-Powered Summary")

        # Top row: Recommendation, Confidence, Sentiment
        col1, col2, col3 = st.columns([1.2, 1, 2])
        with col1:
            st.metric("Recommendation", ai_summary["recommendation"])
        with col2:
            st.metric("Confidence", ai_summary["confidence"])
        with col3:
            st.metric("Weighted Sentiment", f"{ai_summary['avg_sentiment']:.3f}")
        
        # Bottom row: Financial metrics (always show, with Unavailable reasons)
        col4, col5, col6 = st.columns([1.2, 1, 2])
        with col4:
            st.metric("Current Price", current_price)
            if current_price == "Unavailable":
                st.caption(f"Reason: {current_price_reason}")
        with col5:
            st.metric("Projected Gain (30d)", projected_gain)
            if projected_gain == "Unavailable":
                st.caption(f"Reason: {projected_gain_reason}")
        with col6:
            st.metric("Hold Period", hold_days)
            if hold_days == "Unavailable":
                st.caption(f"Reason: {hold_days_reason}")

        st.caption(f"Data quality: {sum(r.get('mention_count', 0) for r in analysis_results.values())} total mentions • {price_points} price points")

        st.markdown("**📋 Rationale:**")
        for bullet in ai_summary["rationale"]:
            st.markdown(f"- {bullet}")

        if current_price != "Unavailable" and projected_gain != "Unavailable" and hold_days != "Unavailable":
            st.markdown(f"- Price {current_price}; projected {projected_gain} over 30d; suggested hold {hold_days}.")
        elif current_price == "Unavailable":
            st.markdown("- Price/projection unavailable.")

        with st.expander("📦 Full Analysis Details", expanded=False):
            # Coverage / data-quality table (lean, non-insight)
            st.subheader("📊 Coverage")

            coverage_rows = []
            for prompt_name, result in (analysis_results or {}).items():
                timeframe = (ANALYSIS_PROMPTS.get(prompt_name, {}) or {}).get("timeframe", "")
                evidence = int(result.get("mention_count", 0) or 0)
                overall = (result.get("overall_sentiment") or "").lower()

                # Strength (quantity). Do NOT conflate with bullish/bearish.
                if overall == "error":
                    strength = "Unavailable"
                elif evidence == 0:
                    strength = "No Signal"
                elif evidence <= 5:
                    strength = "Weak"
                else:
                    strength = "Strong"

                # Tilt (direction)
                if overall == "error":
                    tilt = "Unavailable"
                elif evidence == 0:
                    tilt = "Neutral"
                else:
                    tilt = overall.title() if overall in ("bullish", "bearish", "neutral") else "Neutral"

                coverage_rows.append({
                    "Analysis Type": prompt_name,
                    "Timeframe": timeframe,
                    "Evidence Count": evidence,
                    "Signal Strength": strength,
                    "Sentiment Tilt": tilt,
                })

            df_cov = pd.DataFrame(coverage_rows)

            if not df_cov.empty:
                st.dataframe(
                    df_cov,
                    column_config={
                        "Analysis Type": st.column_config.TextColumn("Analysis Type", width="large"),
                        "Timeframe": st.column_config.TextColumn("Timeframe", width="small"),
                        "Evidence Count": st.column_config.NumberColumn("Evidence Count", width="small"),
                        "Signal Strength": st.column_config.TextColumn("Signal Strength", width="small"),
                        "Sentiment Tilt": st.column_config.TextColumn("Sentiment Tilt", width="small"),
                    },
                    hide_index=True,
                    use_container_width=True,
                )
            else:
                st.caption("No coverage data available.")

            # Detailed analysis sections
            st.subheader("📋 Detailed Analysis")

            for prompt_name, config in ANALYSIS_PROMPTS.items():
                st.markdown(f"### 🔍 {prompt_name}")
                st.markdown(f"**Description:** {config['description']}")
                st.markdown(f"**Timeframe:** {config['timeframe']}")

                if prompt_name in analysis_results:
                    result = analysis_results[prompt_name]

                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("Sentiment Score", f"{result['sentiment_score']:.3f}")
                    with col2:
                        st.metric("Overall Sentiment", result["overall_sentiment"].title())
                    with col3:
                        st.metric("Mentions Found", result["mention_count"])

                    st.markdown(f"**Key Insights:** {result['insights']}")

                    if result["key_themes"]:
                        st.markdown(f"**Tags:** {', '.join(result['key_themes'])}")

                    if result["sample_tweets"]:
                        st.markdown("**Sample Tweets:**")
                        for i, tweet in enumerate(result["sample_tweets"], 1):
                            st.text(f"{i}. {tweet}")
                else:
                    st.error("Analysis failed for this prompt.")

                st.markdown("---")

close_page()
