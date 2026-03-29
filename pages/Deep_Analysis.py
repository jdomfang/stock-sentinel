import streamlit as st
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
from utils.scan_intent import get_query_params

_profile = require_active_account()

open_page(
    title="Deep Analysis",
    subtitle="Full breakdown: sentiment, catalysts, risks, and an on-demand projection.",
)

st.subheader("Stock Analysis")

# If we arrived via Home -> Auth redirect, the ticker may be in query params.
_qp = get_query_params()
_qp_ticker = (_qp.get("ticker") or "").strip().upper()
if _qp_ticker and not st.session_state.get("prefill_deep_ticker"):
    st.session_state["prefill_deep_ticker"] = _qp_ticker

ticker_default = (st.session_state.pop("prefill_deep_ticker", None) or "NVDA").strip() or "NVDA"

ticker = st.text_input(
    "Stock Ticker",
    ticker_default,
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
        ai_summary = generate_ai_summary(analysis_results)

        # Fetch financial data (best-effort)
        current_price = "Unavailable"
        projected_gain = "Unavailable"
        hold_days = "Unavailable"
        price_points = 0

        try:
            stock_data = get_stock_data(ticker.upper())
            if stock_data.get("error") is None and stock_data.get("prices"):
                prices = stock_data.get("prices") or []
                price_points = len(prices)
                last_px = prices[-1]
                if isinstance(last_px, (int, float)):
                    current_price = f"${last_px:.2f}"
                projection = simple_projection(prices, ai_summary["avg_sentiment"], days=30)
                if projection.get("error") is None:
                    p10 = projection.get("gain_p10")
                    p90 = projection.get("gain_p90")
                    projected_gain = f"{p10:.1f}–{p90:.1f}%" if (p10 is not None and p90 is not None) else f"{float(projection.get('avg_gain', 0.0)):.1f}%"
                    hold_days = f"{int(projection.get('suggested_hold_days', 0))} days"
        except Exception:
            pass

        # ── Recommendation panel (matches Discovery deep panel + Home demo exactly) ──
        rec = ai_summary.get("recommendation", "—")
        conf = ai_summary.get("confidence", "—")
        rec_color = (
            "rgba(56,189,248,.95)" if "buy" in rec.lower()
            else "rgba(239,68,68,.90)" if "avoid" in rec.lower()
            else "rgba(245,158,11,.90)"
        )

        # Panel header
        st.markdown(
            f"""
            <style>
            .da-metrics [data-testid="stMetric"] {{
              padding: 11px 12px !important;
              border-radius: 13px !important;
              min-height: 78px !important;
              background: linear-gradient(180deg, rgba(15,23,42,.90), rgba(15,23,42,.73)) !important;
              border: 1px solid rgba(148,163,184,0.18) !important;
              box-shadow: 0 9px 20px rgba(0,0,0,.21) !important;
            }}
            .da-metrics [data-testid="stMetric"] label {{
              font-size: 0.73rem !important;
              margin-bottom: 5px !important;
              white-space: normal !important;
              line-height: 1.14 !important;
              letter-spacing: 0.008em;
              color: rgba(229,231,235,.66) !important;
            }}
            .da-metrics [data-testid="stMetric"] [data-testid="stMetricValue"] {{
              font-size: 1.11rem !important;
              line-height: 1.06 !important;
              white-space: nowrap;
              font-weight: 755 !important;
              color: rgba(248,250,252,.98) !important;
            }}
            .da-metrics [data-testid="column"]:nth-child(1) [data-testid="stMetric"] {{
              border-color: rgba(56,189,248,0.28) !important;
            }}
            .da-metrics [data-testid="column"]:nth-child(2) [data-testid="stMetric"] {{
              border-color: rgba(125,211,252,0.18) !important;
            }}
            .da-metrics [data-testid="column"]:nth-child(3) [data-testid="stMetric"] {{
              border-color: rgba(148,163,184,0.24) !important;
            }}
            .da-metrics {{
              margin-top: 0.25rem;
            }}
            </style>
            <div style="
              margin-top:1.0rem;
              border:1px solid rgba(56,189,248,.30);
              border-radius:16px 16px 0 0;
              padding:18px 20px 14px 20px;
              background:linear-gradient(180deg,rgba(56,189,248,.07),rgba(15,23,42,.95));
              display:flex;
              align-items:center;
              justify-content:space-between;
              gap:12px;
            ">
              <div>
                <div style="font-size:0.72rem;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:rgba(56,189,248,.80);margin-bottom:3px;">Deep Analysis · {sector.title()}</div>
                <div style="font-size:1.55rem;font-weight:850;letter-spacing:-0.02em;color:rgba(248,250,252,.98);">{ticker.upper()}</div>
              </div>
              <div style="text-align:right;">
                <div style="font-size:0.72rem;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:rgba(148,163,184,.65);margin-bottom:3px;">Signal</div>
                <div style="font-size:1.30rem;font-weight:850;color:{rec_color};">{rec}</div>
                <div style="font-size:0.80rem;color:rgba(148,163,184,.75);margin-top:2px;">Confidence: {conf}</div>
              </div>
            </div>
            <div style="border:1px solid rgba(56,189,248,.20);border-top:none;border-radius:0 0 16px 16px;padding:16px 20px 20px 20px;background:rgba(15,23,42,.88);margin-bottom:1.5rem;">
            """,
            unsafe_allow_html=True,
        )

        # Premium metric cards (same class as Home demo)
        _mc = "border-radius:12px;padding:13px 15px;background:rgba(15,23,42,.70);border:1px solid rgba(148,163,184,.15);flex:1;"
        _ml = "font-size:0.72rem;font-weight:700;letter-spacing:0.05em;text-transform:uppercase;color:rgba(148,163,184,.65);margin-bottom:4px;"
        _mv = "font-size:1.10rem;font-weight:800;color:rgba(248,250,252,.95);"
        st.markdown(
            f'<div style="display:flex;gap:10px;margin:10px 0 14px 0;flex-wrap:wrap;">'
            f'<div style="{_mc}"><div style="{_ml}">Price</div><div style="{_mv}">{current_price}</div></div>'
            f'<div style="{_mc}"><div style="{_ml}">Proj. Gain 30d</div><div style="{_mv}">{projected_gain}</div></div>'
            f'<div style="{_mc}"><div style="{_ml}">Hold Period</div><div style="{_mv}">{hold_days}</div></div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # Data quality line
        _total_mentions = sum(r.get("mention_count", 0) for r in analysis_results.values())
        st.markdown(
            f'<div style="color:rgba(148,163,184,.55);font-size:0.75rem;margin-bottom:12px;">'
            f'{_total_mentions} mentions analysed · {price_points} price points</div>',
            unsafe_allow_html=True,
        )

        # Rationale
        st.markdown(
            '<div style="font-size:0.85rem;font-weight:700;color:rgba(148,163,184,.75);letter-spacing:0.04em;text-transform:uppercase;margin-bottom:8px;">Rationale</div>',
            unsafe_allow_html=True,
        )
        for bullet in ai_summary["rationale"]:
            st.markdown(f"- {bullet}")
        if current_price != "Unavailable" and projected_gain != "Unavailable" and hold_days != "Unavailable":
            st.markdown(f"- Price {current_price}; projected {projected_gain} over 30d; suggested hold {hold_days}.")
        elif current_price == "Unavailable":
            st.markdown("- Price/projection data unavailable.")

        # Close the panel wrapper div
        st.markdown('</div>', unsafe_allow_html=True)

        with st.expander("📦 Full Analysis Details", expanded=False):
            coverage_rows = []
            for prompt_name, result in (analysis_results or {}).items():
                timeframe = (ANALYSIS_PROMPTS.get(prompt_name, {}) or {}).get("timeframe", "")
                evidence = int(result.get("mention_count", 0) or 0)
                overall = (result.get("overall_sentiment") or "").lower()
                if overall == "error":
                    strength, tilt = "Unavailable", "Unavailable"
                elif evidence == 0:
                    strength, tilt = "No Signal", "Neutral"
                else:
                    strength = "Strong" if evidence > 5 else "Weak"
                    tilt = overall.title() if overall in ("bullish", "bearish", "neutral") else "Neutral"
                coverage_rows.append((prompt_name, timeframe, evidence, strength, tilt))

            if coverage_rows:
                tilt_color = {"Bullish": "rgba(56,189,248,.95)", "Bearish": "rgba(239,68,68,.90)", "Neutral": "rgba(148,163,184,.80)"}
                rows_html = ""
                for prompt_name, timeframe, evidence, strength, tilt in coverage_rows:
                    tc = tilt_color.get(tilt, "rgba(148,163,184,.80)")
                    rows_html += (
                        f'<tr style="border-bottom:1px solid rgba(148,163,184,.10);">'
                        f'<td style="padding:9px 10px;color:rgba(229,231,235,.90);font-size:0.82rem;">{prompt_name}</td>'
                        f'<td style="padding:9px 10px;color:rgba(148,163,184,.70);font-size:0.82rem;">{timeframe}</td>'
                        f'<td style="padding:9px 10px;color:rgba(148,163,184,.80);font-size:0.82rem;text-align:center;">{evidence}</td>'
                        f'<td style="padding:9px 10px;color:rgba(148,163,184,.80);font-size:0.82rem;">{strength}</td>'
                        f'<td style="padding:9px 10px;font-size:0.82rem;font-weight:700;color:{tc};">{tilt}</td>'
                        f'</tr>'
                    )
                st.markdown(
                    f'<table style="width:100%;border-collapse:collapse;background:rgba(15,23,42,.60);border-radius:10px;overflow:hidden;">'
                    f'<thead><tr style="border-bottom:1px solid rgba(148,163,184,.20);">'
                    f'<th style="padding:8px 10px;text-align:left;font-size:0.72rem;font-weight:700;letter-spacing:0.05em;text-transform:uppercase;color:rgba(148,163,184,.60);">Analysis Type</th>'
                    f'<th style="padding:8px 10px;text-align:left;font-size:0.72rem;font-weight:700;letter-spacing:0.05em;text-transform:uppercase;color:rgba(148,163,184,.60);">Timeframe</th>'
                    f'<th style="padding:8px 10px;text-align:center;font-size:0.72rem;font-weight:700;letter-spacing:0.05em;text-transform:uppercase;color:rgba(148,163,184,.60);">Evidence</th>'
                    f'<th style="padding:8px 10px;text-align:left;font-size:0.72rem;font-weight:700;letter-spacing:0.05em;text-transform:uppercase;color:rgba(148,163,184,.60);">Strength</th>'
                    f'<th style="padding:8px 10px;text-align:left;font-size:0.72rem;font-weight:700;letter-spacing:0.05em;text-transform:uppercase;color:rgba(148,163,184,.60);">Tilt</th>'
                    f'</tr></thead><tbody>{rows_html}</tbody></table>',
                    unsafe_allow_html=True,
                )
            else:
                st.caption("No coverage data available.")

            st.markdown('<div style="height:1rem"></div>', unsafe_allow_html=True)
            st.markdown('<div style="font-size:0.85rem;font-weight:700;color:rgba(148,163,184,.75);letter-spacing:0.04em;text-transform:uppercase;margin-bottom:8px;">Detailed Analysis</div>', unsafe_allow_html=True)

            for prompt_name, config in ANALYSIS_PROMPTS.items():
                st.markdown(f"**{prompt_name}** · {config['timeframe']}")
                if prompt_name in analysis_results:
                    result = analysis_results[prompt_name]
                    col1, col2, col3 = st.columns(3)
                    col1.metric("Sentiment Score", f"{result['sentiment_score']:.3f}")
                    col2.metric("Overall Sentiment", result["overall_sentiment"].title())
                    col3.metric("Mentions Found", result["mention_count"])
                    st.markdown(f"**Key Insights:** {result['insights']}")
                    if result["key_themes"]:
                        st.markdown(f"**Key Themes:** {', '.join(result['key_themes'])}")
                    if result["sample_tweets"]:
                        st.markdown("**Sample Posts:**")
                        for i, tweet in enumerate(result["sample_tweets"], 1):
                            st.text(f"{i}. {tweet}")
                else:
                    st.caption("Analysis unavailable for this type.")
                st.markdown("<hr style='border:none;border-top:1px solid rgba(148,163,184,.12);margin:10px 0;'>", unsafe_allow_html=True)

close_page()
