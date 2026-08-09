import logging
import streamlit as st
from typing import Dict, List, Any

from utils.obs import install as _install_logging, new_request_id, set_request_id as _set_request_id

_install_logging()
_da_logger = logging.getLogger(__name__)

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

# Apply theme + hero BEFORE guard — logged-out users see the full styled page
from utils.ui import apply_theme
from utils.auth import flush_pending_rt_save
apply_theme()
flush_pending_rt_save()

st.markdown(
    """
    <style>
    div[data-testid="stMainBlockContainer"] {
      max-width: 1100px; margin: 0 auto;
      padding-left: clamp(16px, 4vw, 28px);
      padding-right: clamp(16px, 4vw, 28px);
      padding-top: 0.25rem;
    }
    div[data-testid="stMainBlockContainer"] > div:first-child,
    div[data-testid="stVerticalBlock"] > div:first-child {
      margin-top: 0 !important; padding-top: 0 !important;
    }
    section[data-testid="stMain"] > div { padding-top: 0 !important; }

    .da-hero { margin: -11.0rem 0 10px 0; padding: 0 2px 2px 2px; }
    .da-hero-title {
      font-size: clamp(42px, 5.1vw, 3.55rem); font-weight: 850;
      letter-spacing: -0.035em; line-height: 1.08; margin: 0 0 8px 0;
    }
    .da-hero-sub {
      color: var(--muted); font-size: clamp(15px, 1.35vw, 1.05rem);
      line-height: 1.45; margin: 0 0 0.85rem 0; max-width: 680px;
    }
    /* Mobile: remove aggressive negative hero offset (header is different on phones) */
    @media (max-width: 640px) {
      /* Pull the hero up to compensate for Streamlit's extra empty blocks on initial mobile render */
      .da-hero { margin: -6.8rem 0 10px 0; }
      .da-hero-title { font-size: clamp(34px, 9.5vw, 44px); }
      .da-hero-sub { font-size: 1.00rem; }
    }

    .st-key-da_scan_card {
      background: transparent !important; border: none !important;
      box-shadow: none !important; padding: 0 !important; margin-bottom: 0.25rem;
    }
    /* Kill Streamlit block gap between input row and results panel */
    .st-key-da_scan_card + div[data-testid="stVerticalBlock"],
    .st-key-da_scan_card ~ div {
      margin-top: 0 !important;
    }
    /* Tighten all vertical block gaps on this page */
    section[data-testid="stMain"] [data-testid="stVerticalBlock"] > [data-testid="stVerticalBlock"] {
      gap: 0.25rem !important;
    }
    .st-key-da_scan_card [data-testid="stHorizontalBlock"] {
      align-items: center !important; gap: 10px !important;
    }
    .st-key-da_scan_card [data-baseweb="input"] > div {
      border-radius: 10px !important; min-height: 38px !important;
    }
    .st-key-da_scan_card .stButton > button {
      min-height: 38px !important; border-radius: 10px !important;
      padding-top: 0 !important; padding-bottom: 0 !important;
    }
    </style>
    <div class="clawd-app-wrapper">
    <div class="da-hero">
      <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px;">
        <span style="display:inline-flex;align-items:center;gap:6px;background:rgba(56,189,248,.06);border:1px solid rgba(56,189,248,.18);border-radius:999px;padding:5px 12px;font-size:0.80rem;font-weight:600;color:rgba(229,231,235,.80);">📡 Real-time social sentiment</span>
        <span style="display:inline-flex;align-items:center;gap:6px;background:rgba(56,189,248,.06);border:1px solid rgba(56,189,248,.18);border-radius:999px;padding:5px 12px;font-size:0.80rem;font-weight:600;color:rgba(229,231,235,.80);">🏦 4,000+ US stocks</span>
        <span style="display:inline-flex;align-items:center;gap:6px;background:rgba(56,189,248,.06);border:1px solid rgba(56,189,248,.18);border-radius:999px;padding:5px 12px;font-size:0.80rem;font-weight:600;color:rgba(229,231,235,.80);">⚡ Signal in under 60 seconds</span>
      </div>
      <div class="da-hero-title">Analyze any US stock.</div>
      <div class="da-hero-sub">Enter a ticker and get a clear signal — Buy, Watch, or Avoid — built from real social sentiment and market data.</div>
    </div>
    """,
    unsafe_allow_html=True,
)

from utils.guard import require_active_account
from utils.credits import consume_credit, refund_credit, complete_work
from utils.scan_intent import get_query_params

_profile = require_active_account()

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
        _run_clicked = st.button("Deep Analyze", type="primary", use_container_width=True)

# Auto-sector: Deep analysis can run without sector input. Default to unknown.
sector = "unknown"

# Main analysis button — or auto-triggered from Home
if _run_clicked or (_autorun and _prefill):
    # Validate BEFORE charging. This previously debited a credit and only then
    # checked whether a ticker had been entered, so submitting an empty field
    # cost the user a credit and delivered nothing.
    if not (ticker or _prefill).strip():
        st.error("Please enter a stock ticker.")
    else:
        _run_ticker = (ticker or _prefill).strip().upper()

        # Open the request scope before the charge so debit, work and refund
        # share one id in the logs and in the usage_events row.
        _rid = new_request_id()
        _da_logger.info("deep_analyze requested ticker=%s", _run_ticker)

        _credit = consume_credit("deep_analyze", {"ticker": _run_ticker, "page": "deep_analysis"})
        if not _credit.ok:
            _da_logger.info("deep_analyze refused reason=%s", _credit.reason)
            st.error(_credit.message)
            st.stop()
        # Everything from here is charged work. It must be inside try/finally,
        # not try/except: Streamlit aborts a running script with StopException /
        # RerunException, both of which derive from BaseException, so an
        # `except Exception` never sees them. The abort fires whenever the user
        # clicks again, edits the ticker, or navigates away mid-analysis -- the
        # most common way this page fails -- and previously left the credit spent
        # with nothing rendered and no refund.
        _delivered = False
        try:
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
                # A new thread starts with a FRESH context, so the ContextVar holding
                # the request id reverts to its default here. Without this line every
                # log record produced by the actual analysis -- the majority of them --
                # would be stamped "-" and the id would correlate nothing.
                _set_request_id(_rid)
                try:
                    _result_holder["result"] = run_deep_analysis(_run_ticker, sector)
                except Exception as _e:
                    _da_logger.exception("deep_analyze failed ticker=%s", _run_ticker)
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
                # The credit was taken before the work began. The work failed, so
                # give it back rather than charging for an upstream outage.
                _refunded = refund_credit("deep_analyze", _credit.event_id,
                                          f"analysis failed: {_result_holder['error'][:120]}")
                st.markdown(
                    '<div style="border:1px solid rgba(239,68,68,.30);border-radius:14px;padding:18px 20px;'
                    'background:rgba(239,68,68,.05);text-align:center;margin:0.5rem 0;">'
                    '<div style="font-size:1.2rem;margin-bottom:6px;">⚠️</div>'
                    '<div style="font-weight:700;color:rgba(248,113,113,.95);">Analysis failed</div>'
                    '<div style="color:rgba(148,163,184,.75);font-size:0.85rem;margin-top:4px;">'
                    + ("Your credit was not used — try again in a moment."
                       if _refunded else "Try again in a moment — this is usually temporary.")
                    + '</div></div>',
                    unsafe_allow_html=True,
                )
                st.stop()

            analysis_results = _result_holder.get("result")
            if not analysis_results:
                refund_credit("deep_analyze", _credit.event_id, "analysis returned no results")
                st.stop()

            ai_summary = generate_ai_summary(analysis_results)

            # Financial data (best-effort)
            current_price, projected_gain, hold_days, price_points = "Unavailable", "Unavailable", "Unavailable", 0
            # Captured for the verdict log, which needs the RAW numbers rather
            # than the formatted strings above. Declared out here because the
            # block below is best-effort and may not run at all.
            _last_close: float | None = None
            _proj: dict = {}
            try:
                stock_data = get_stock_data(_run_ticker)
                if stock_data.get("error") is None and stock_data.get("prices"):
                    prices = stock_data["prices"]
                    price_points = len(prices)
                    lp = prices[-1]
                    if isinstance(lp, (int, float)):
                        current_price = f"${lp:.2f}"
                        _last_close = float(lp)
                    proj = simple_projection(prices, ai_summary["avg_sentiment"], days=30)
                    _proj = proj or {}
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

            # The recommendation panel IS the product. Once it has rendered the user
            # has what they paid for, so anything that fails after this point is a
            # presentation bug, not a delivery failure.
            _delivered = True

            # Written AFTER delivery, and unable to affect it. This is the only
            # record that this call was ever made: X's index is 7 days deep and
            # cannot be backfilled, so a verdict not written now can never be
            # scored against what the stock actually did.
            try:
                from utils import verdict_log
                from utils.sentiment import MODEL_NAME
                verdict_log.record(
                    _run_ticker,
                    ai_summary.get("recommendation", ""),
                    sector=sector,
                    confidence=ai_summary.get("confidence"),
                    avg_sentiment=ai_summary.get("avg_sentiment"),
                    total_mentions=_total_mentions,
                    price_at_verdict=_last_close,
                    projected_p10=_proj.get("gain_p10"),
                    projected_p90=_proj.get("gain_p90"),
                    suggested_hold_days=_proj.get("suggested_hold_days"),
                    success_rate=_proj.get("success_rate"),
                    event_id=getattr(_credit, "event_id", None),
                    model=MODEL_NAME,
                )
            except Exception:
                _da_logger.warning("verdict_log call failed", exc_info=True)

            render_full_analysis_expander(analysis_results)
        finally:
            # Backstop for every path the except blocks above cannot reach,
            # including the Streamlit abort and any failure in generate_ai_summary
            # or the render calls, which sit outside the worker's try. Overlaps
            # safely with the explicit refunds: refund_credit is idempotent, so a
            # second attempt returns already_refunded and the more specific reason
            # recorded earlier wins. Does NOT cover an OOM kill -- SIGKILL runs no
            # finally either; that stays the orphan reaper's job.
            if _delivered:
                complete_work(_credit.event_id, "completed", f"ticker={_run_ticker}")
            else:
                refund_credit("deep_analyze", _credit.event_id,
                              "deep analysis did not complete")
                complete_work(_credit.event_id, "failed", "aborted or errored")

close_page()
