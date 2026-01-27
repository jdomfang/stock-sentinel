import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import time
from typing import Dict, List, Any

from utils.navigation import render_sidebar_navigation
from utils.finance import get_stock_data, validate_ticker
from utils.projections import simple_projection
from utils.deep_analysis import ANALYSIS_PROMPTS, run_deep_analysis, generate_ai_summary

# Page configuration
st.set_page_config(
    page_title="Deep Analysis - Stock Sentinel",
    page_icon="🔬",
    layout="wide"
)

# Sidebar navigation
render_sidebar_navigation()

st.title("🔬 Deep Analysis")
st.markdown("Comprehensive X sentiment analysis using Abdul Shakoor methodology")

# Input section
st.subheader("Stock Analysis Parameters")
col1, col2 = st.columns([2, 1])

with col1:
    ticker = st.text_input("Stock Ticker", "NVDA", help="Enter stock ticker symbol (e.g., NVDA, AAPL, TSLA)")

with col2:
    sector = st.selectbox(
        "Sector",
        [
            "tech",
            "healthcare",
            "energy",
            "finance",
            "consumer",
            "utilities",
            "real estate",
            "industrials",
            "materials",
            "communication",
        ],
        index=0,
    )

# Main analysis button
if st.button("🔬 Run Deep Analysis", type="primary"):
    if not ticker.strip():
        st.error("Please enter a stock ticker.")
    else:
        with st.spinner("Running deep analysis... This may take a moment."):
            analysis_results = run_deep_analysis(
                ticker.upper(),
                sector,
            )

        # Display results
        st.success("✅ Deep analysis complete!")

        # AI-powered summary
        ai_summary = generate_ai_summary(analysis_results)
        st.subheader("🧠 AI-Powered Summary")

        col1, col2, col3 = st.columns([1.2, 1, 2])
        with col1:
            st.metric("Recommendation", ai_summary["recommendation"])
        with col2:
            st.metric("Confidence", ai_summary["confidence"])
        with col3:
            st.metric("Weighted Sentiment", f"{ai_summary['avg_sentiment']:.3f}")

        st.markdown("**Rationale:**")
        for bullet in ai_summary["rationale"]:
            st.markdown(f"- {bullet}")

        with st.expander("📦 Full Analysis Details", expanded=False):
            # Summary table
            st.subheader("📊 Analysis Summary")

            # Get financial data for projections
            validation = validate_ticker(ticker.upper())
            summary_data = []

            for prompt_name, result in analysis_results.items():
                row = {
                    "Analysis Type": prompt_name,
                    "Sentiment Score": result["sentiment_score"],
                    "Overall Sentiment": result["overall_sentiment"],
                    "Mentions": result["mention_count"],
                    "Key Themes": ", ".join(result["key_themes"]) if result["key_themes"] else "None",
                    "Catalysts": "Check insights below",
                    "Risks": "Check insights below",
                }
                summary_data.append(row)

            # Create summary dataframe
            df_summary = pd.DataFrame(summary_data)

            # Add financial projections if ticker is valid
            if validation.get("valid", False):
                stock_data = get_stock_data(ticker.upper())
                if stock_data["error"] is None and stock_data["prices"]:
                    # Calculate average sentiment across all analyses
                    avg_sentiment = sum(r["sentiment_score"] for r in analysis_results.values()) / len(analysis_results)

                    projection = simple_projection(stock_data["prices"], avg_sentiment, days=30)

                    if projection["error"] is None:
                        df_summary["Projected Gain (%)"] = projection["avg_gain"]
                        df_summary["Suggested Hold (days)"] = projection["suggested_hold_days"]

            st.dataframe(
                df_summary,
                column_config={
                    "Analysis Type": st.column_config.TextColumn("Analysis Type", width="medium"),
                    "Sentiment Score": st.column_config.NumberColumn("Sentiment Score", format="%.3f"),
                    "Overall Sentiment": st.column_config.TextColumn("Overall Sentiment", width="small"),
                    "Mentions": st.column_config.NumberColumn("Mentions", width="small"),
                    "Key Themes": st.column_config.TextColumn("Key Themes", width="medium"),
                    "Catalysts": st.column_config.TextColumn("Catalysts", width="medium"),
                    "Risks": st.column_config.TextColumn("Risks", width="medium"),
                },
                hide_index=True,
                use_container_width=True,
            )

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
                        st.markdown(f"**Key Themes:** {', '.join(result['key_themes'])}")

                    if result["sample_tweets"]:
                        st.markdown("**Sample Tweets:**")
                        for i, tweet in enumerate(result["sample_tweets"], 1):
                            st.text(f"{i}. {tweet}")
                else:
                    st.error("Analysis failed for this prompt.")

                st.markdown("---")

# Information section
st.markdown("---")
st.info(
    """
**How Deep Analysis Works:**
- Runs 4 X searches and derives 8 analysis sections (Abdul Shakoor-inspired lenses)
- Each analysis focuses on different aspects (sentiment, trends, influencers, momentum, news, retail vs institutional, red flags, strategy)
- Uses AI sentiment analysis, weighted signal aggregation, and financial data validation
- Produces a single recommendation (Buy / Watch / Avoid) with rationale for buy-position readiness

**Note:** Analysis is based on recent X discussions and should be combined with fundamental analysis.
"""
)
