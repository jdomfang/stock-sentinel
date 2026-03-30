import streamlit as st
from typing import Dict, List, Any

from utils.navigation import render_sidebar_navigation, render_top_nav
from utils.ui import open_page, close_page, GENERIC_ERROR_TEXT, safe_ui, render_deep_panel_header
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

# Pull prefill ticker — but don't fall back to NVDA (empty is fine)
_prefill = (st.session_state.pop("prefill_deep_ticker", None) or "").strip().upper()
_autorun = bool(st.session_state.pop("_autorun_deep_analysis", False))

ticker = st.text_input(
    "Stock Ticker",
    value=_prefill,
    placeholder="e.g. TSLA",
    help="Enter stock ticker symbol (e.g., NVDA, AAPL, TSLA)",
)

# Auto-sector: Deep analysis can run without sector input. Default to unknown.
sector = "unknown"

# Main analysis button — or auto-triggered from Home
_run_clicked = st.button("🔬 Run Deep Analysis", type="primary")
if _run_clicked or (_autorun and _prefill):
    ok, err = consume_credit("deep_analyze")
    if not ok:
        st.error(err)
        st.stop()
    if not (ticker or _prefill).strip():
        st.error("Please enter a stock ticker.")
    else:
        _run_ticker = (ticker or _prefill).strip().upper()
        # Multi-step progress display so user knows work is happening
        _da_progress = st.progress(0)
        _da_status = st.empty()
        _da_status.markdown(
            f'<div style="color:rgba(229,231,235,.85);font-size:0.92rem;font-weight:600;margin-bottom:0.25rem;">'
            f'📡 Pulling social data for <b>{_run_ticker}</b>...</div>',
            unsafe_allow_html=True,
        )
        _da_progress.progress(12)

        import threading, time as _time

        _result_holder: dict = {}
        _done_flag = threading.Event()

        def _run():
            try:
                _result_holder["result"] = run_deep_analysis(_run_ticker, sector)
            except Exception as _e:
                _result_holder["error"] = str(_e)
            finally:
                _done_flag.set()

        _t = threading.Thread(target=_run, daemon=True)
        _t.start()

        _steps = [
            (20, "🔍 Scanning X posts for market signals..."),
            (35, "📊 Analysing sentiment across timeframes..."),
            (50, "🧠 Running FinBERT classification on mentions..."),
            (65, "📈 Modelling 30-day price projection..."),
            (78, "⚡ Calculating conviction score..."),
            (88, "🔬 Synthesising Buy / Watch / Avoid signal..."),
        ]
        _step_idx = 0
        _start = _time.time()
        while not _done_flag.wait(timeout=1.5):
            if _step_idx < len(_steps):
                prog, msg = _steps[_step_idx]
                _da_progress.progress(prog)
                _da_status.markdown(
                    f'<div style="color:rgba(229,231,235,.85);font-size:0.92rem;font-weight:600;">{msg}</div>',
                    unsafe_allow_html=True,
                )
                _step_idx += 1

        _da_progress.progress(100)
        _da_status.empty()
        _da_progress.empty()

        if "error" in _result_holder:
            st.markdown(
                '<div style="border:1px solid rgba(239,68,68,.30);border-radius:14px;padding:18px 20px;'
                'background:rgba(239,68,68,.05);text-align:center;margin:0.5rem 0;">'
                '<div style="font-size:1.2rem;margin-bottom:6px;">⚠️</div>'
                '<div style="font-weight:700;color:rgba(248,113,113,.95);">Analysis failed</div>'
                '<div style="color:rgba(148,163,184,.75);font-size:0.85rem;margin-top:4px;">Try again in a moment — this is usually temporary.</div>'
                '</div>',
                unsafe_allow_html=True,
            )
            st.stop()

        analysis_results = _result_holder.get("result")
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

        # ── Recommendation panel ──
        rec = ai_summary.get("recommendation", "—")
        conf = ai_summary.get("confidence", "—")
        rec_color = (
            "rgba(56,189,248,.95)" if "buy" in rec.lower()
            else "rgba(239,68,68,.90)" if "avoid" in rec.lower()
            else "rgba(245,158,11,.90)"
        )

        # Panel header bar
        st.markdown(
            f"""
            <div style="margin-top:1.0rem;border:1px solid rgba(56,189,248,.30);border-radius:16px 16px 0 0;
              padding:18px 20px 14px 20px;
              background:linear-gradient(180deg,rgba(56,189,248,.07),rgba(15,23,42,.95));
              display:flex;align-items:center;justify-content:space-between;gap:12px;">
              <div>
                <div style="font-size:0.72rem;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:rgba(56,189,248,.80);margin-bottom:3px;">Deep Analysis · {sector.title()}</div>
                <div style="font-size:1.55rem;font-weight:850;letter-spacing:-0.02em;color:rgba(248,250,252,.98);">{_run_ticker}</div>
              </div>
              <div style="text-align:right;">
                <div style="font-size:0.72rem;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:rgba(148,163,184,.65);margin-bottom:3px;">Signal</div>
                <div style="font-size:1.30rem;font-weight:850;color:{rec_color};">{rec}</div>
                <div style="font-size:0.80rem;color:rgba(148,163,184,.75);margin-top:2px;">Confidence: {conf}</div>
              </div>
            </div>
            <div style="border:1px solid rgba(56,189,248,.20);border-top:none;border-radius:0 0 16px 16px;
              padding:16px 20px 20px 20px;background:rgba(15,23,42,.88);margin-bottom:1.5rem;">
            """,
            unsafe_allow_html=True,
        )

        # Shared premium recommendation cards (rec / confidence / sentiment with signal bars)
        render_deep_panel_header(
            ticker=_run_ticker,
            sector=sector,
            rec=rec,
            conf=conf,
            avg_sentiment=ai_summary["avg_sentiment"],
        )

        # Financial metric cards
        _mc = "border-radius:12px;padding:13px 15px;background:rgba(15,23,42,.70);border:1px solid rgba(148,163,184,.15);flex:1;"
        _ml = "font-size:0.68rem;font-weight:700;letter-spacing:0.05em;text-transform:uppercase;color:rgba(148,163,184,.60);margin-bottom:4px;"
        _mv = "font-size:1.10rem;font-weight:800;color:rgba(248,250,252,.95);"
        st.markdown(
            f'<div style="display:flex;gap:10px;margin:0 0 14px 0;flex-wrap:wrap;">'
            f'<div style="{_mc}"><div style="{_ml}">Last Price</div><div style="{_mv}">{current_price}</div></div>'
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

        st.markdown('<div style="height:0.5rem"></div>', unsafe_allow_html=True)
        with st.expander("📊 Full breakdown — click to expand signal details", expanded=False):
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
