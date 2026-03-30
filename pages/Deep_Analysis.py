import streamlit as st
from typing import Dict, List, Any

from utils.navigation import render_sidebar_navigation, render_top_nav
from utils.ui import open_page, close_page, GENERIC_ERROR_TEXT, safe_ui, render_recommendation_panel, render_full_analysis_expander
import streamlit.components.v1 as _components
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

# Apply theme BEFORE guard so unauthenticated users see the dark theme on the login gate
from utils.ui import apply_theme
apply_theme()

from utils.guard import require_active_account
from utils.credits import consume_credit
from utils.scan_intent import get_query_params

_profile = require_active_account()

# ── Hero (matches Home/Discovery exactly — no open_page() to avoid double gap) ──
st.markdown(
    """
    <style>
    div[data-testid="stMainBlockContainer"] {
      max-width: 1100px;
      margin: 0 auto;
      padding-left: clamp(16px, 4vw, 28px);
      padding-right: clamp(16px, 4vw, 28px);
      padding-top: 0.25rem;
    }
    div[data-testid="stMainBlockContainer"] > div:first-child,
    div[data-testid="stVerticalBlock"] > div:first-child {
      margin-top: 0 !important;
      padding-top: 0 !important;
    }
    section[data-testid="stMain"] > div { padding-top: 0 !important; }

    .da-hero { margin: -8.10rem 0 10px 0; padding: 0 2px 2px 2px; }
    .da-hero-title {
      font-size: clamp(42px, 5.1vw, 3.55rem);
      font-weight: 850;
      letter-spacing: -0.035em;
      line-height: 1.08;
      margin: 0 0 8px 0;
    }
    .da-hero-sub {
      color: var(--muted);
      font-size: clamp(15px, 1.35vw, 1.05rem);
      line-height: 1.45;
      margin: 0 0 0.85rem 0;
      max-width: 680px;
    }
    @media (max-width: 640px) { .da-hero { margin: -2rem 0 0.75rem 0; } }

    /* Tight inline scan card — no wasted space */
    .st-key-da_scan_card {
      border: 1px solid rgba(148,163,184,0.18);
      background: linear-gradient(180deg, rgba(15,23,42,.92), rgba(15,23,42,.72));
      border-radius: 14px;
      padding: 10px 14px 10px 14px;
      box-shadow: 0 8px 22px rgba(0,0,0,.30);
      margin-bottom: 0.75rem;
    }
    .st-key-da_scan_card [data-testid="stHorizontalBlock"] {
      align-items: center !important; gap: 10px !important;
    }
    .st-key-da_scan_card [data-baseweb="input"] > div {
      border-radius: 10px !important; min-height: 36px !important;
    }
    .st-key-da_scan_card .stButton > button {
      min-height: 36px !important; border-radius: 10px !important;
      padding-top: 0 !important; padding-bottom: 0 !important;
    }
    </style>
    <div class="clawd-app-wrapper">
    <div class="da-hero">
      <div class="da-hero-title">Analyze any US stock.</div>
      <div class="da-hero-sub">Enter a ticker and get a clear signal — Buy, Watch, or Avoid — built from real social sentiment and market data.</div>
    </div>
    """,
    unsafe_allow_html=True,
)

# If we arrived via Home → Auth redirect, the ticker may be in query params.
_qp = get_query_params()
_qp_ticker = (_qp.get("ticker") or "").strip().upper()
if _qp_ticker and not st.session_state.get("prefill_deep_ticker"):
    st.session_state["prefill_deep_ticker"] = _qp_ticker

_prefill = (st.session_state.pop("prefill_deep_ticker", None) or "").strip().upper()
_autorun = bool(st.session_state.pop("_autorun_deep_analysis", False))

# ── Compact scan card ──
with st.container(key="da_scan_card"):
    ticker_col, btn_col, _pad = st.columns([0.55, 0.45, 2.0])
    with ticker_col:
        ticker = st.text_input(
            "Ticker",
            value=_prefill,
            placeholder="e.g. RCAT",
            key="da_ticker_input",
            label_visibility="collapsed",
            max_chars=6,
        )
    with btn_col:
        _run_clicked = st.button("Analyze →", type="primary", use_container_width=True)

# Auto-sector: Deep analysis can run without sector input. Default to unknown.
sector = "unknown"

# Main analysis button — or auto-triggered from Home
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
            f'📡 Gathering market chatter for <b>{_run_ticker}</b>...</div>',
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
            (20, "📰 Reading what traders are saying..."),
            (35, "📊 Weighing bullish vs bearish signals..."),
            (50, "🔍 Cross-referencing sentiment over time..."),
            (65, "📈 Running price projection models..."),
            (78, "⚡ Measuring signal strength..."),
            (88, "🔬 Building your recommendation..."),
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

        ai_summary = generate_ai_summary(analysis_results)

        # Financial data (best-effort)
        current_price, projected_gain, hold_days, price_points = "Unavailable", "Unavailable", "Unavailable", 0
        try:
            stock_data = get_stock_data(_run_ticker)
            if stock_data.get("error") is None and stock_data.get("prices"):
                prices = stock_data["prices"]
                price_points = len(prices)
                lp = prices[-1]
                if isinstance(lp, (int, float)):
                    current_price = f"${lp:.2f}"
                proj = simple_projection(prices, ai_summary["avg_sentiment"], days=30)
                if proj.get("error") is None:
                    p10, p90 = proj.get("gain_p10"), proj.get("gain_p90")
                    projected_gain = f"{p10:.1f}–{p90:.1f}%" if (p10 is not None and p90 is not None) else f"{float(proj.get('avg_gain',0)):.1f}%"
                    hold_days = f"{int(proj.get('suggested_hold_days', 0))} days"
        except Exception:
            pass

        _total_mentions = sum(r.get("mention_count", 0) for r in analysis_results.values())

        # Anchor + auto-scroll so panel comes into view immediately
        import streamlit as _st
        _st.markdown('<div id="da-results-anchor"></div>', unsafe_allow_html=True)
        _components.html(
            '<script>setTimeout(()=>{ const el = window.parent.document.getElementById("da-results-anchor"); if(el) el.scrollIntoView({behavior:"smooth",block:"start"}); }, 200);</script>',
            height=0,
        )

        render_recommendation_panel(
            ticker=_run_ticker,
            sector=sector,
            ai_summary=ai_summary,
            current_price=current_price,
            projected_gain=projected_gain,
            hold_days=hold_days,
            mentions=_total_mentions,
            price_points=price_points,
        )

        render_full_analysis_expander(analysis_results)

close_page()
