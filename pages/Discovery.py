import streamlit as st

# Ensure project root is on sys.path (avoids collisions with any installed `utils` package on Streamlit Cloud)
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import html
import pandas as pd
import logging

from utils.navigation import render_sidebar_navigation, render_top_nav
from utils.ui import (
    apply_theme,
    close_page,
    processing_state_html,
    render_compact_task_hint,
    render_delivered_analysis_result,
    render_system_state,
)
from utils.finance import get_last_close_prices_best_effort
# NOT the scan. Pagination, ticker validation, sentiment attribution and the
# effectiveness telemetry all moved to utils/scan.py; the seven imports that
# used to sit here were what made this page the only thing able to run one.
# What is left is a credit, a progress bar and a table.
# This page contributes a table, a credit and a panel. The analysis
# behind the panel is the same one pages/Deep_Analysis.py runs.
# NOT the pipeline. Both routes into Deep Analyze -- this page's per-row
# button and pages/Deep_Analysis.py -- now call core-api. The sector SCAN
# above is still local and imports what it needs directly.
from utils import analyze_client as _client
from utils import billing
from utils.demo_snapshots import snapshot_timestamp


st.set_page_config(
    page_title="Market Scan - Stock Sentinel",
    page_icon="🔎",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# Verdicts we are willing to assert. Anything else is a statement about how
# little evidence there is, and must not be dressed like a conclusion --
# 52% of tickers were previously getting a bold Bullish/Bearish badge from a
# SINGLE post, indistinguishable from one backed by fourteen.
_ASSERTED = {"bullish", "bearish", "neutral"}


def _sentiment_pill(label: str) -> str:
    label = (label or "").strip()
    low = label.lower()
    if low == "bullish":
        return '<span style="background:rgba(56,189,248,.18);color:rgba(56,189,248,.98);border:1px solid rgba(56,189,248,.35);padding:3px 10px;border-radius:999px;font-size:0.83rem;font-weight:700;">Bullish</span>'
    if low == "bearish":
        return '<span style="background:rgba(239,68,68,.15);color:rgba(248,113,113,.98);border:1px solid rgba(239,68,68,.30);padding:3px 10px;border-radius:999px;font-size:0.83rem;font-weight:700;">Bearish</span>'
    if low in _ASSERTED:
        return f'<span style="background:rgba(148,163,184,.12);color:rgba(148,163,184,.92);border:1px solid rgba(148,163,184,.25);padding:3px 10px;border-radius:999px;font-size:0.83rem;font-weight:700;">{label or "Neutral"}</span>'
    # Low-evidence: no border, no bold, muted. It reads as a caveat rather than
    # a call, which is what "one post said something" actually is. The ticker
    # still appears -- discovery is the product; the false confidence is not.
    return (f'<span style="color:rgba(148,163,184,.62);font-size:0.78rem;'
            f'font-style:italic;">{label or "Neutral"}</span>')

# Logging is configured centrally. This page used to call basicConfig(force=True),
# which meant whichever page a user landed on first won the root config for the
# whole process -- and re-running it on every Streamlit rerun stacked handlers.
from utils.obs import install as _install_logging, new_request_id, set_request_id as _set_request_id

_install_logging()
logger = logging.getLogger(__name__)

apply_theme()
render_sidebar_navigation()

from utils.scan_intent import get_query_params, patch_query_params
_qp = get_query_params()
_intent_sector = (_qp.get("sector") or "").strip().lower()
_sector_intent_options = {
    "tech", "healthcare", "energy", "finance", "consumer", "utilities",
    "real estate", "industrials", "materials", "communication",
}

from utils.guard import require_active_account, require_login
from utils.auth import (
    ensure_user_scoped_state_owner,
    flush_pending_rt_save,
    refresh_session_if_needed,
)
flush_pending_rt_save()
require_login(after_auth_page="Discovery")
render_top_nav(active="market_scan")
_profile = require_active_account(after_auth_page="Discovery")

# Apply cross-page intent only after the authentication boundary has bound this
# Streamlit session's private state to the current user. Otherwise a stale
# owner guard could correctly clear the state and accidentally clear a fresh
# sector intent that was written just before it.
if (
    _intent_sector in _sector_intent_options
    and not st.session_state.get("_intent_sector_applied")
):
    st.session_state["discovery_sector"] = _intent_sector
    st.session_state["_intent_sector_applied"] = True


# ---- Intent prefill (optional, for direct links) ----
_intent_autostart = (_qp.get("autostart") or "").strip().lower() in {"1", "true", "yes", "y", "on"}

# Determine whether we should auto-run the scan on this load.
# Primary mechanism is session_state (Home -> Auth -> Discovery).
_autostart_scan = bool(st.session_state.pop("_autostart_discovery_scan", False))
if _intent_autostart and not st.session_state.get("_scan_autostart_consumed"):
    st.session_state["_scan_autostart_consumed"] = True
    _autostart_scan = True

from utils.credits import consume_credit, refund_credit, complete_work

st.markdown(
    """
    <style>
    /* Discovery page styling; global theme comes from utils.ui.apply_theme() */

    /* Secondary buttons (Deep Analyze) - stronger presence */
    [class*="st-key-scan_row_"] button[data-testid="stBaseButton-secondary"],
    [class*="st-key-scan_row_"] .stButton > button[kind="secondary"] {
      background: rgba(56,189,248,.08) !important;
      background-color: rgba(56,189,248,.08) !important;
      color: rgba(56,189,248,.95) !important;
      border: 1px solid rgba(56,189,248,0.40) !important;
      font-weight: 700 !important;
      opacity: 1 !important;
      transition: all 0.15s ease !important;
    }
    [class*="st-key-scan_row_"] button[data-testid="stBaseButton-secondary"]:hover,
    [class*="st-key-scan_row_"] .stButton > button[kind="secondary"]:hover {
      background: rgba(56,189,248,.18) !important;
      background-color: rgba(56,189,248,.18) !important;
      border-color: rgba(56,189,248,0.75) !important;
      color: rgba(255,255,255,.98) !important;
      box-shadow: 0 0 12px rgba(56,189,248,.20) !important;
    }

    /* Release A: task-first hierarchy and native Streamlit result rows. */
    .scan-results-intro {
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 1rem;
      margin: 1.65rem 0 0.75rem;
    }
    .scan-results-intro h2 {
      margin: 0 0 0.2rem;
      color: var(--text);
      font-size: 1.35rem;
      letter-spacing: -0.02em;
    }
    .scan-results-intro p,
    .scan-results-freshness {
      margin: 0;
      color: var(--muted);
      font-size: 0.83rem;
    }
    .scan-section-label {
      margin: 1.15rem 0 0.45rem;
      color: var(--muted);
      font-size: 0.76rem;
      font-weight: 750;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    [class*="st-key-scan_row_"] {
      box-sizing: border-box;
      padding: 0.72rem 0;
      border-bottom: 1px solid rgba(148, 163, 184, 0.14);
    }
    [class*="st-key-scan_row_selected_"] {
      border-radius: var(--radius-control);
      border-bottom-color: transparent;
      background: rgba(56, 189, 248, 0.055);
      box-shadow: inset 0 0 0 1px rgba(56, 189, 248, 0.42);
    }
    /* Header and rows use one physical track definition on non-card layouts.
       The media gate prevents these !important desktop resets from winning
       over the mobile flex-card contract through selector specificity. */
    @media (min-width:721px) {
      [class*="st-key-scan_header_"] [data-testid="stHorizontalBlock"],
      [class*="st-key-scan_row_"] [data-testid="stHorizontalBlock"] {
        display: grid !important;
        grid-template-columns:
          minmax(170px, 1.75fr)
          minmax(72px, .72fr)
          minmax(86px, .92fr)
          minmax(68px, .62fr)
          minmax(132px, 1.15fr);
        gap: .5rem !important;
        padding-inline: 8px !important;
        box-sizing: border-box !important;
        align-items: stretch !important;
      }
      [class*="st-key-scan_header_"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
      [class*="st-key-scan_row_"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
        display: flex !important;
        flex: none !important;
        width: auto !important;
        min-width: 0 !important;
        align-items: center !important;
      }
      [class*="st-key-scan_header_"] [data-testid="stColumn"] > div,
      [class*="st-key-scan_row_"] [data-testid="stColumn"] > div {
        width: 100% !important;
        min-width: 0 !important;
      }
      [class*="st-key-scan_row_"] [data-testid="stColumn"]:last-child [data-testid="stElementContainer"],
      [class*="st-key-scan_row_"] [data-testid="stColumn"]:last-child [data-testid="stMarkdownContainer"],
      [class*="st-key-scan_row_"] [data-testid="stColumn"]:last-child .stButton {
        width: 100% !important;
        min-width: 0 !important;
        margin: 0 !important;
      }
      [class*="st-key-scan_row_"] [data-testid="stColumn"]:last-child [data-testid="stMarkdownContainer"] p {
        margin: 0 !important;
      }
    }
    .scan-stock-cell strong {
      color: var(--text);
      font-size: 0.96rem;
      letter-spacing: 0.01em;
    }
    .scan-stock-cell span,
    .scan-social-posts,
    .scan-evidence-state {
      display: block;
      margin-top: 0.16rem;
      color: var(--muted);
      font-size: 0.8rem;
      line-height: 1.35;
    }
    .scan-evidence-state {
      font-style: normal;
    }
    .scan-meta-cell {
      min-height: 44px;
      display: flex;
      align-items: center;
      white-space: nowrap;
      font-variant-numeric: tabular-nums;
    }
    .scan-social-posts {
      margin-top: 0;
      white-space: nowrap;
    }
    .scan-mobile-label { display: none; }
    .scan-table-note {
      margin-top: 0.8rem;
      color: var(--muted);
      font-size: 0.8rem;
      line-height: 1.45;
    }
    .scan-view-result {
      min-height: 44px;
      display: inline-flex;
      width: 100%;
      align-items: center;
      justify-content: center;
      border: 1px solid rgba(56,189,248,.42);
      border-radius: var(--radius-control);
      background: rgba(56,189,248,.10);
      color: rgba(125,211,252,.98) !important;
      font-size: .86rem;
      font-weight: 720;
      white-space: nowrap;
      text-decoration: none !important;
    }
    .scan-view-result:hover { background: rgba(56,189,248,.18); }
    .st-key-selected_analysis_panel {
      box-sizing:border-box;width:100%;min-width:0;max-width:100%;
      margin:0 0 1.25rem;scroll-margin-top:1rem;
    }
    .st-key-selected_analysis_panel > div,
    .st-key-selected_analysis_panel [data-testid="stVerticalBlock"],
    .st-key-selected_analysis_panel [data-testid="stElementContainer"] {
      box-sizing:border-box!important;min-width:0!important;max-width:100%!important;
    }
    .scan-view-result {
      box-sizing:border-box;
    }
    [class*="st-key-scan_row_"] .stButton > button {
      min-height: 44px !important;
      white-space: nowrap !important;
      padding-left: .65rem !important;
      padding-right: .65rem !important;
      font-size: .84rem !important;
    }
    /* A paid row action used to run inside the narrow Action cell. During the
       rerun Streamlit dimmed the old table, while the only progress indicator
       was mounted inside that same cell and effectively disappeared. Keep the
       work state in a stable, centered surface that cannot be mistaken for a
       frozen page. */
    .st-key-discovery_analysis_progress {
      position:fixed!important;z-index:999990!important;
      left:50%!important;top:50%!important;transform:translate(-50%,-50%)!important;
      width:min(520px,calc(100vw - 2rem))!important;
      padding:1.25rem 1.35rem!important;box-sizing:border-box!important;
      border:1px solid rgba(56,189,248,.48)!important;
      border-radius:16px!important;
      background:linear-gradient(145deg,#0b172b,#071120)!important;
      box-shadow:0 28px 80px rgba(0,0,0,.64),0 0 0 9999px rgba(2,6,23,.48)!important;
      opacity:1!important;
    }
    .st-key-discovery_analysis_progress [data-testid="stVerticalBlock"] {
      gap:.7rem!important;
    }
    .st-key-discovery_analysis_progress p {
      color:rgba(226,232,240,.96)!important;
    }

    @media (max-width: 720px) {
      .scan-results-intro {
        align-items: flex-start;
        flex-direction: column;
        gap: 0.35rem;
      }
      [class*="st-key-scan_header_"] { display: none !important; }
      [class*="st-key-scan_row_"] [data-testid="stHorizontalBlock"] {
        display: flex !important;
        flex-wrap: wrap;
        gap: 0.35rem;
        padding-inline: 8px !important;
        box-sizing: border-box !important;
      }
      [class*="st-key-scan_row_"] [data-testid="stColumn"] {
        flex: 1 1 calc(50% - 0.5rem) !important;
        width: auto !important;
      }
      [class*="st-key-scan_row_"] [data-testid="stColumn"]:first-child {
        flex-basis: 100% !important;
      }
      [class*="st-key-scan_row_"] [data-testid="stColumn"]:last-child {
        flex-basis: 100% !important;
      }
      .scan-mobile-label {
        display: block;
        margin-bottom: .16rem;
        color: rgba(148,163,184,.72);
        font-size: .68rem;
        font-weight: 720;
        letter-spacing: .055em;
        text-transform: uppercase;
      }
      .scan-meta-cell {
        min-height: 0;
        flex-direction: column;
        align-items: flex-start;
        white-space: normal;
      }
      .scan-social-posts {white-space: nowrap;}
      [class*="st-key-scan_row_"] .stButton > button {
        width: 100%;
      }
      .st-key-selected_analysis_panel {margin-bottom:1rem;}
    }

    /* Hide Streamlit "Made with" footer */
    footer { visibility: hidden; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown('<div class="clawd-app-wrapper discovery-wrapper">', unsafe_allow_html=True)


def _bail() -> None:
    """Stop the script WITHOUT leaving the page half-drawn.

    The mirror of pages/Deep_Analysis._bail, and it exists for the same reason:
    st.stop() raises StopException, which unwinds past the close_page() at the
    bottom of this module, so the wrapper div opened just above stays open and
    the footer never renders. Every early exit below this line -- and the two
    on the Deep Analyze button in particular -- is a routine outcome now, not
    an exceptional one.

    The two pages charge from the same ledger and must not differ in how they
    fail; this file already says so about refunds.
    """
    close_page()
    st.stop()

if "selected_ticker" not in st.session_state:
    st.session_state.selected_ticker = None
if "selected_sector" not in st.session_state:
    st.session_state.selected_sector = None
if "deep_analysis_results" not in st.session_state:
    st.session_state.deep_analysis_results = None
    st.session_state.deep_analysis_card = None
if "df_valid" not in st.session_state:
    st.session_state.df_valid = None
if "df_unvalidated" not in st.session_state:
    st.session_state.df_unvalidated = None
if "scan_corpus_age_s" not in st.session_state:
    st.session_state.scan_corpus_age_s = 0.0


def _queue_discovery_analysis(ticker: str, sector_name: str) -> None:
    """Turn a row click into work handled outside the table layout."""
    st.session_state["_pending_discovery_analysis"] = {
        "ticker": str(ticker).strip().upper(),
        "sector": str(sector_name or "unknown").strip().lower(),
        # A rerun between debit and worker start must replay the same ledger
        # request rather than charge a second time.
        "request_id": new_request_id(),
    }


def _process_pending_discovery_analysis(pending: dict) -> dict | None:
    """Run one paid analysis in a stable, page-level progress surface.

    The former implementation lived inside the row's Action column. On a
    Streamlit rerun that cell was stale-dimmed along with the table, making a
    legitimate long-running request look frozen. Processing remains
    synchronous for ledger safety, but its UI now has a fixed, prominent
    location and the completed card is committed during the same rerun.
    """
    ticker_symbol = str((pending or {}).get("ticker") or "").strip().upper()
    result_sector = str(
        (pending or {}).get("sector") or "unknown"
    ).strip().lower()
    if not ticker_symbol:
        st.session_state.pop("_pending_discovery_analysis", None)
        return None

    if not _client.configured():
        logger.error("deep_analyze unavailable: core-api not configured")
        st.session_state.pop("_pending_discovery_analysis", None)
        return {
            "title": "Deep Analysis is temporarily unavailable",
            "message": "No credit has been used.",
            "meta": "Try again in a moment.",
        }

    request_id = str((pending or {}).get("request_id") or new_request_id())
    _set_request_id(request_id)
    # Mount the explanation before either the ledger or auth network call so
    # even their latency cannot leave a dim page with no visible work state.
    overlay_slot = st.empty()
    with overlay_slot.container():
        with st.container(key="discovery_analysis_progress"):
            st.markdown(f"### Analyzing {html.escape(ticker_symbol)}")
            progress = st.progress(5)
            status = st.empty()
            status.markdown(
                processing_state_html("Preparing secure analysis…"),
                unsafe_allow_html=True,
            )

    credit = consume_credit(
        "deep_analyze",
        {"ticker": ticker_symbol, "sector": result_sector, "page": "discovery"},
    )
    if not credit.ok:
        st.session_state.pop("_pending_discovery_analysis", None)
        overlay_slot.empty()
        billing.render_credit_refusal(
            credit,
            f"The full analysis for {ticker_symbol} costs 1 credit.",
            key=f"discovery_{ticker_symbol}",
        )
        return None

    delivered = False
    failure_state = None
    result_holder: dict = {}
    try:
        refresh_session_if_needed()
        progress.progress(10)
        status.markdown(
            processing_state_html(
                f"Gathering market discussion for {ticker_symbol}…"
            ),
            unsafe_allow_html=True,
        )

        import threading as _th

        done = _th.Event()

        def _run_analysis() -> None:
            _set_request_id(request_id)
            try:
                result = _client.analyze_remote(
                    ticker_symbol,
                    result_sector,
                    feature="discovery",
                    route="discovery",
                    event_id=getattr(credit, "event_id", None),
                )
                if result.ok:
                    logger.info(
                        "discovery deep_analyze served by CORE-API "
                        "in %.1fs ticker=%s",
                        result.elapsed_s or -1,
                        ticker_symbol,
                    )
                    result_holder["card"] = result.card
                    result_holder["analysis_results"] = (
                        result.analysis_results or {}
                    )
                    result_holder["metadata"] = {
                        "degraded": result.degraded,
                        "status": result.status,
                        "posts_billed": result.posts_billed,
                        "elapsed_s": result.elapsed_s,
                        "route": "discovery",
                    }
                else:
                    result_holder["error"] = result.error
                    result_holder["pre_spend"] = result.retryable
            except Exception as exc:
                result_holder["error"] = str(exc)
                logger.exception(
                    "Deep analysis error for %s", ticker_symbol
                )
            finally:
                done.set()

        worker = _th.Thread(target=_run_analysis, daemon=True)
        worker.start()
        steps = [
            (20, "Reading what traders are saying…"),
            (35, "Weighing bullish and bearish signals…"),
            (50, "Cross-referencing sentiment over time…"),
            (65, "Running price projection models…"),
            (78, "Measuring signal strength…"),
            (88, "Building your recommendation…"),
        ]
        step_index = 0
        while not done.wait(timeout=1.5):
            if step_index < len(steps):
                amount, message = steps[step_index]
                progress.progress(amount)
                status.markdown(
                    processing_state_html(message),
                    unsafe_allow_html=True,
                )
                step_index += 1
        progress.progress(100)

        if "error" in result_holder:
            refunded = refund_credit(
                "deep_analyze",
                credit.event_id,
                f"analysis failed: {str(result_holder['error'])[:120]}",
            )
            failure_state = {
                "title": f"Analysis failed for {ticker_symbol}",
                "message": (
                    "Your credit was not used."
                    if refunded
                    else "If your credit was not returned, it will be "
                    "released automatically within 15 minutes."
                ),
                "meta": (
                    "Try again in a moment."
                    if refunded and result_holder.get("pre_spend")
                    else ""
                ),
            }
        else:
            card = result_holder.get("card") or {}
            if not card:
                logger.error("no analysis card produced for %s", ticker_symbol)
                refunded = refund_credit(
                    "deep_analyze",
                    credit.event_id,
                    "no summary could be produced",
                )
                failure_state = {
                    "title": f"No analysis could be produced for {ticker_symbol}",
                    "message": (
                        "Your credit was not used."
                        if refunded
                        else "If your credit was not returned, it will be "
                        "released automatically within 15 minutes."
                    ),
                    "meta": "",
                }
            else:
                # The card is the paid product. Detailed excerpts are optional,
                # exactly as they are on the dedicated Deep Analyze route.
                ensure_user_scoped_state_owner()
                st.session_state.deep_analysis_card = card
                st.session_state.deep_analysis_results = (
                    result_holder.get("analysis_results") or {}
                )
                st.session_state.deep_analysis_completed_at = snapshot_timestamp()
                st.session_state.deep_analysis_metadata = (
                    result_holder.get("metadata") or {}
                )
                st.session_state.selected_ticker = ticker_symbol
                st.session_state.analysis_sector = result_sector
                st.session_state["analysis_result_origin"] = "discovery"
                delivered = True
    except Exception:
        logger.exception(
            "Discovery analysis workflow failed for %s", ticker_symbol
        )
        failure_state = {
            "title": f"Analysis failed for {ticker_symbol}",
            "message": (
                "If your credit was not returned, it will be released "
                "automatically within 15 minutes."
            ),
            "meta": "",
        }
    finally:
        st.session_state.pop("_pending_discovery_analysis", None)
        if delivered:
            complete_work(
                credit.event_id, "completed", f"ticker={ticker_symbol}"
            )
        else:
            if refund_credit(
                "deep_analyze",
                credit.event_id,
                "deep analysis did not complete",
            ):
                complete_work(
                    credit.event_id, "failed", "aborted or errored"
                )
            else:
                logger.error(
                    "refund failed for event %s; leaving work_run open so "
                    "the reaper retries",
                    credit.event_id,
                )

    # No Streamlit calls occur inside the charged-work finally block. STOP or
    # RERUN can therefore never interrupt ledger cleanup. On ordinary
    # completion, remove the modal surface and let this same script run render
    # the newly committed result—there is no second stale rerun.
    overlay_slot.empty()
    return failure_state


# The existing paid execution below receives one account-bound row selection.
from utils.ui import load_sector_pulse, render_sector_pulse, PULSE_SECTORS
from utils.scan_intent import queue_pulse_scan, take_pulse_scan, take_research_intent

_research_sector = take_research_intent("scan")
if _research_sector:
    st.session_state["discovery_sector"] = _research_sector
    _autostart_scan = False
    st.session_state.pop("_autostart_discovery_scan", None)
    patch_query_params({"autostart": None, "sector": None})
_pulse_requested_sector = take_pulse_scan()
if _pulse_requested_sector:
    st.session_state["discovery_sector"] = _pulse_requested_sector
sector = st.session_state.get("discovery_sector") or st.session_state.get("selected_sector") or "tech"
if sector not in PULSE_SECTORS:
    sector = "tech"
_credits = int((_profile or {}).get("credits") or 0)
with st.container(key="discovery_pulse_command"):
    intro_col, meter_col = st.columns([2, 1])
    with intro_col:
        st.html('<header class="ss-task-command-intro"><h1>Market Scan</h1><p>Find unusual social attention by sector.</p></header>')
    with meter_col:
        billing.render_credit_meter(profile=_profile, key="discovery")
_pulse = load_sector_pulse()
render_sector_pulse(_pulse, surface="discovery", on_scan=queue_pulse_scan, credits=_credits,
                    selected=st.session_state.get("discovery_sector", ""))
scan_clicked = bool(_pulse_requested_sector)
# Missing observations never prevent independent research. When all ten are
# available the pulse itself is the chooser, without a duplicate toolbar.
if _pulse["missing"]:
    with st.container(key="discovery_scan_card"):
        with st.container(key="discovery_control_row"):
            sel_col, btn_col = st.columns([1.45, 1.0])
            with sel_col:
                if st.session_state.get("discovery_sector") not in PULSE_SECTORS:
                    st.session_state["discovery_sector"] = sector
                sector = st.selectbox("Sector", options=list(PULSE_SECTORS), key="discovery_sector")
            with btn_col:
                scan_clicked = st.button("Run scan · 1 credit", type="primary", use_container_width=True, disabled=_credits <= 0) or scan_clicked
with st.container(key="discovery_direct_analysis"):
    st.page_link("pages/Deep_Analysis.py", label="Already have a company in mind? Open Deep Analyze")

# Basket retrieval is the only path. The hand-written topic queries it
# replaced were measured head-to-head on two sectors, same hour, equal spend:
#
#                  topic precision   basket precision   tickers   n>=3
#   utilities             4%               86%          10 -> 56   1 -> 16
#   technology           23%               88%          29 -> 68   4 -> 18
#
# Basket precision is also STABLE across sectors (86-88%) where topic swung
# 6x depending on whether a sector's jargon happened to discriminate. There is
# deliberately no fallback to topic mode: falling back would silently hand the
# user a scan we have measured as four times worse. A scan that cannot build
# its query fails and refunds instead.

scan_triggered = bool(scan_clicked or _autostart_scan)

if st.session_state.df_valid is None and not scan_triggered:
    render_compact_task_hint(
        title="No scan run yet",
        message="Bullish, Bearish, or Neutral results appear below.",
    )

# Scan button
# _get_checkout_url and _upgrade_modal used to live here, page-private -- which
# is precisely why pages/Deep_Analysis.py had no buy option and dead-ended
# instead. They are in utils/billing.py now, and this page is one of three
# callers rather than the owner.


if scan_triggered:
    # Must be logged in to scan.
    if not st.session_state.get("auth.user"):
        st.error("Please log in to scan.")
        _bail()

    # Open a request scope BEFORE the charge, so the debit, every downstream X
    # and Supabase call, and any refund all log under one id -- and so the
    # usage_events row carries it too. This is the correlation key that did not
    # exist when a scan died mid-run and had to be reconstructed by timestamp.
    _rid = new_request_id()
    logger.info("scan requested sector=%s", sector)

    # NOTHING TO CALL, SO NOTHING TO CHARGE. Mandatory now that the local
    # path is gone: without it a misconfigured CORE_API_URL would take a
    # credit and refund it, once per click, each refund another chance for the
    # RPC to fail and lose it for real. configured() asks the same question
    # _base() asks, so a bare host or an http:// URL is refused here.
    if not _client.configured():
        logger.error("scan unavailable: core-api not configured")
        render_system_state(
            kind="error",
            title="Market Scan is temporarily unavailable",
            message="The analysis service is not available right now.",
            meta="No credit has been used.",
        )
        _bail()

    _credit = consume_credit("scan", {"sector": sector, "page": "discovery"})
    if not _credit.ok:
        logger.info("scan refused reason=%s", _credit.reason)
        billing.render_credit_refusal(
            _credit, "A sector scan costs 1 credit.", key="scan")
        _bail()

    # Set when X refuses to serve us. Drives the refund below: the user paid for
    # a scan, so if the upstream never delivered any posts they must not be
    # charged for it. Observed in production as a 402 credits-depleted.

    # Set to True the moment this scan has produced an answer the user can see.
    # The `finally` below refunds whenever it is still False.
    #
    # This exists because every refund on this page used to live in an
    # `except Exception`, and Streamlit's abort path does NOT raise Exception:
    # StopException and RerunException both derive from BaseException. With
    # runner.fastReruns on (the default), ANY new interaction stops the running
    # script at its next yield point -- and st.progress()/st.markdown() inside
    # the pagination loop are yield points. So the single most likely way a scan
    # dies is a user clicking again because nothing looks like it is happening:
    # the first run was killed with no refund, and the second run charged again.
    # Two credits, one scan. `finally` runs on BaseException; `except` does not.
    _delivered = False

    try:
        progress_bar = st.progress(0)
        status_text = st.empty()
        status_text.markdown(
            processing_state_html(
                f"Scanning recent discussion for {sector} momentum…"
            ),
            unsafe_allow_html=True,
        )
        progress_bar.progress(8)

        # ONE PATH. The in-process branch that sat here was scaffolding for
        # the cutover and is gone: verified warm (posts_billed 0 from cache),
        # cold (260 posts over 11 pages, corpus written back) and failing
        # (kind=ticker_db, right panel, credit refunded, nothing spent).
        #
        # A fallback was never an option here even while it existed. core-api
        # refuses a concurrent scan of one sector with 429 sector-busy
        # precisely BECAUSE another request is buying that corpus right now;
        # substituting a local scan defeats the duplicate-suppression the
        # service exists for and buys the same 300 posts again.
        import threading

        _holder: dict = {}
        _done = threading.Event()

        def _run_scan():
            # A new thread starts with a FRESH context, so the ContextVar
            # holding the request id reverts to its default. Without this
            # every log line the scan produces would be stamped "-".
            _set_request_id(_rid)
            try:
                _r = _client.scan_remote(
                    sector, event_id=getattr(_credit, "event_id", None))
                _holder["remote"] = _r
                if _r.ok:
                    # PROCESSED, not billed, and the label says so. The two
                    # differ by however many posts came back from more than
                    # one basket -- 246 processed against 260 billed on the
                    # first cold scan -- and reading one as the other is how
                    # a free replay looks like a purchase.
                    logger.info(
                        "scan served by CORE-API in %.1fs sector=%s rows=%d "
                        "posts_processed=%d from_cache=%s",
                        _r.elapsed_s or -1, sector, len(_r.rows),
                        _r.posts_seen, _r.from_cache)
                else:
                    logger.error("core-api scan failed (%s): %s",
                                 _r.kind, _r.error)
            except BaseException as _e:      # noqa: BLE001
                # Anything escaping here would die silently in the thread and
                # leave the script waiting on a flag that never sets.
                logger.exception("scan failed sector=%s", sector)
                _holder["error"] = str(_e)
            finally:
                _done.set()

        threading.Thread(target=_run_scan, daemon=True).start()

        # DELIBERATELY these steps, then silence. Streamlit notices an abort
        # only at an st.* call, so ticking for the whole scan would make the
        # entire run abortable -- and an abort after the service has
        # paginated is up to 300 posts bought that nobody sees.
        _steps = [
            (20, "Scanning recent discussion for %s momentum…" % sector),
            (40, "Filtering noise and validating tickers…"),
            (60, "Building your shortlist…"),
            (80, "Reading the mood on your shortlist…"),
            (92, "Ranking unusual attention…"),
        ]
        _i = 0
        while not _done.wait(timeout=1.5):
            if _i < len(_steps):
                _pct, _msg = _steps[_i]
                progress_bar.progress(_pct)
                status_text.markdown(
                    processing_state_html(_msg),
                    unsafe_allow_html=True)
                _i += 1

        progress_bar.progress(100)
        status_text.empty()
        progress_bar.empty()

        if "error" in _holder:
            _refunded = refund_credit("scan", _credit.event_id,
                                      f"scan failed: {str(_holder['error'])[:120]}")
            render_system_state(
                kind="error",
                title="The scan could not be completed",
                message=(
                    "Your credit was not used."
                    if _refunded else
                    "If your credit was not returned, it will be released "
                    "automatically within 15 minutes."
                ),
                # The worker escaped without a service response, so spend
                # status is unknown even if the credit refund succeeded.
                meta="",
            )
            _bail()

        # ONE SHAPE, because there is one path. The local Scan and the
        # RemoteScan had to be reconciled here while both existed.
        _r = _holder["remote"]
        _rows, _ok, _err, _kind = _r.rows, _r.ok, _r.error, _r.kind
        _x_err, _age, _posts = _r.x_error, _r.corpus_age_s, _r.posts_seen
        _retryable = bool(_r.retryable)
        _no_query = (_r.kind == "no_query")

        if _no_query:
            # NO FALLBACK, DELIBERATELY -- see utils/scan.py. The credit is
            # returned here; the finally below would also refund, but naming
            # the reason makes the ledger row diagnosable instead of "aborted
            # or errored".
            #
            # Only promise a refund that actually happened. refund_credit
            # returns False without raising when its RPC fails, and telling a
            # user in writing that they were not charged when they were is
            # worse than the original failure.
            _refunded = refund_credit(
                "scan", _credit.event_id, f"query build failed: {(_err or '')[:120]}")
            _credit_line = ("Your credit was not used."
                            if _refunded
                            else "If your credit was not returned, it will be released "
                                 "automatically within 15 minutes.")
            render_system_state(
                kind="warning",
                title="Could not build this sector scan",
                message=_credit_line,
                meta=("This is usually temporary—try again shortly."
                      if _refunded else ""),
            )
            _bail()

        if _err:
            # ONE PANEL PER FAILURE KIND, as before. These used to be reached
            # by `except KeyError` and `except requests.exceptions.
            # RequestException`; both went unreachable when the pipeline moved
            # behind a function that returns instead of raising, so every
            # failure collapsed into the generic panel and a missing API key
            # started reporting itself as an X outage. scan.error_kind carries
            # the distinction across that boundary.
            logger.error("scan failed for %s (%s): %s", sector, _kind, _err)
            _REASONS = {"credentials": "missing API credentials",
                        "network": "network failure reaching X",
                        # The portal could not reach CORE-API. Labelling that
                        # as an X failure sends every audit of refund reasons
                        # looking at the wrong provider.
                        "transport": "core-api unreachable",
                        "ticker_db": "ticker database unavailable"}
            _refunded = refund_credit(
                "scan", _credit.event_id,
                _REASONS.get(_kind, f"scan error: {(_err or '')[:120]}"),
            )
            if _kind == "credentials":
                _title, _body = (
                    "Configuration error",
                    "Missing API credentials. Contact support if this keeps happening.")
            elif _kind in ("network", "transport"):
                _title, _body = (
                    "Connection issue",
                    "Couldn't reach the data source.")
            elif _kind == "ticker_db":
                _title, _body = (
                    "Could not load ticker database",
                    "Please check the data directory.")
            else:
                _title, _body = (
                    "Something went wrong",
                    "The scan hit an unexpected error.")
            _credit_line = (
                "Your credit was not used."
                if _refunded else
                "If your credit was not returned, it will be released "
                "automatically within 15 minutes."
            )
            render_system_state(
                kind="warning" if _kind in ("network", "transport") else "error",
                title=_title,
                message=f"{_body} {_credit_line}",
                meta=(
                    "Try again in a moment."
                    if _refunded and _retryable
                    and _kind in ("network", "transport")
                    else ""
                ),
            )
            _bail()

        if _x_err and _posts != 0:
            render_system_state(
                kind="warning",
                title="Social data feed unavailable",
                message=_x_err[:200],
                meta="The availability issue is upstream and may be temporary.",
            )

        if _posts == 0:
            if _x_err:
                # Upstream failure, zero posts: the user paid and got nothing.
                _refunded = refund_credit(
                    "scan", _credit.event_id, f"x api: {_x_err[:120]}")
                render_system_state(
                    kind="error",
                    title="Social data feed unavailable",
                    message=(
                        "No scan result was delivered. Your credit was not used."
                        if _refunded else
                        "No scan result was delivered. If your credit was not "
                        "returned, it will be released automatically within "
                        "15 minutes."
                    ),
                    # The upstream call returned no posts; it may already have
                    # incurred provider work, so a second purchase is not
                    # suggested here.
                    meta="",
                )
            else:
                # A genuinely empty result is an answer, not a failure -- the
                # scan ran and the sector simply had no chatter. Still charged,
                # and _delivered says so: without it the finally refunded every
                # single time while this comment claimed the opposite, which
                # made a quiet sector an unlimited supply of free scans paid
                # for at X.
                _delivered = True
                render_system_state(
                    kind="info",
                    title="No recent discussion found",
                    message=(
                        "No posts returned from the social data feed for "
                        "this query."
                    ),
                    meta="Try another sector or return later.",
                )
            _bail()

        # AFTER the bails, as it was. Setting it earlier overwrote the
        # previous scan's age with 0.0 on every failed run -- the old table
        # kept rendering and simply lost its "Market chatter from 3h ago"
        # caption, which is stale-and-silent, the failure this page keeps
        # rediscovering.
        st.session_state.scan_corpus_age_s = _age

        _display = _rows
        # ON THE ROWS, not on _ok. Reaching here means no_query and error are
        # both clear, so _ok is unconditionally True and `_rows or _ok` was
        # always taken -- which made the else dead, silently retired the
        # "No stock tickers found in the posts" message, and started wiping
        # the previous table on a zero-row scan where it used to be left alone.
        if _rows:
            # NO WRITE HERE. core-api persisted scan_sentiment_log and
            # recorded its own x_call_metrics row before it answered -- it was
            # handed this credit's event_id for exactly that reason. Writing
            # again would double every per-ticker observation for one buy, and
            # scan_sentiment_log has no unique constraint to catch it.

            # The DataFrame is built HERE, from rows the service ordered and
            # cut. It is the one thing that cannot cross a service boundary,
            # which is why /scan returns dicts and the page renders them.
            df_valid = pd.DataFrame(_display)

            ensure_user_scoped_state_owner()
            st.session_state.df_valid = df_valid
            st.session_state.df_unvalidated = None  # not shown
            # A fresh scan can return the same ticker set with newer closes.
            # Invalidate the presentation cache by scan generation, not only
            # when the list of symbols changes.
            st.session_state.pop("_scan_last_close_key", None)
            st.session_state.pop("_scan_last_close_map", None)
            st.session_state.selected_sector = sector
            # Keep the source sector with the scan itself. Deep Analyze can be
            # opened independently and may update other route state, but the
            # durable public demo must publish a coherent scan/analysis pair.
            st.session_state.demo_scan_sector = sector
            st.session_state.scan_completed_at = snapshot_timestamp()
            st.session_state.scan_result_metadata = {
                "posts_seen": _r.posts_seen,
                "from_cache": _r.from_cache,
                "corpus_age_s": _r.corpus_age_s,
                "stop_reason": _r.stop_reason,
                "x_error": _r.x_error,
                "elapsed_s": _r.elapsed_s,
            }
            st.session_state.selected_ticker = None
            st.session_state.deep_analysis_results = None
            st.session_state.deep_analysis_card = None
            st.session_state.deep_analysis_completed_at = None
            st.session_state.deep_analysis_metadata = {}
            st.session_state.analysis_sector = None

            # Results are durable in session_state: the scan ran and produced an
            # answer. An empty answer is still an answer -- the sector genuinely
            # had no validated chatter -- so it is charged, as before.
            _delivered = True

            if len(df_valid) == 0:
                render_system_state(
                    kind="info",
                    title="No validated stock tickers found",
                    message="The scan completed without a trustworthy ticker match.",
                    meta="Try a different sector or time window.",
                )
        else:
            # Posts were fetched and scored, they just contained no tickers.
            # Work was done and an answer given, so this stays charged.
            _delivered = True
            render_system_state(
                kind="info",
                title="No stock tickers found",
                message="The retrieved discussion did not contain usable ticker references.",
                meta="Try a different sector.",
            )

    # `except KeyError` and `except requests.exceptions.RequestException`
    # used to live here. They are gone rather than left as dead code: scan()
    # returns instead of raising, so neither could ever fire again, and their
    # panels are now selected by the normalised _kind above. A dead handler for a
    # message the user still sees is worse than no handler -- it reads as
    # coverage.
    except Exception:
        logger.exception("Discovery scan failed")
        _refunded = refund_credit(
            "scan", _credit.event_id, "unhandled scan error")
        render_system_state(
            kind="error",
            title="Something went wrong",
            message=(
                "The scan could not be completed. Your credit was not used."
                if _refunded else
                "The scan could not be completed. If your credit was not "
                "returned, it will be released automatically within 15 minutes."
            ),
            # This catch-all has no reliable pre-spend signal.
            meta="",
        )

    finally:
        # The backstop. Runs on BaseException too, so it covers the paths every
        # `except Exception` above misses: Streamlit stopping the script because
        # the user clicked again, changed the sector, or navigated away.
        #
        # Safe to overlap with the explicit refunds above -- refund_credit is
        # idempotent (the usage_events_refund_of unique index makes a second
        # attempt return already_refunded), so the more specific reason recorded
        # by an except block wins and this becomes a no-op. It only actually
        # refunds when nothing else did.
        #
        # Still does NOT cover an OOM kill: SIGKILL runs no finally either. That
        # remains the orphan reaper's job.
        # Record the buy even when the scan died. The single most likely way a
        # scan ends is the user clicking again mid-run, which raises
        # StopException (a BaseException) and skips every explicit call site
        # above -- and those aborted runs are the MOST wasteful, since 100% of
        # the posts were bought and 0% were used. Excluding exactly the worst
        # cases would bias the waste number downward in the flattering
        # direction. record_scan cannot raise, so this is safe in a finally.
        # NO METRICS BACKSTOP HERE ANY MORE. It existed for the local Scan,
        # whose record_metrics the page had to call from a finally because an
        # abort skipped every explicit site. core-api records its own row
        # inside the request -- it catches BaseException to guarantee it -- so
        # an abort on this side cannot lose one.

        if _delivered:
            complete_work(_credit.event_id, "completed", f"sector={sector}")
        else:
            # Close the run ONLY if the refund actually landed.
            #
            # refund_credit returns False and does not raise when its RPC fails.
            # Closing the run regardless would set work_runs.status='failed',
            # and reap_orphaned_work only scans status='running' -- so a user
            # charged during a Supabase blip would be silently stranded, with
            # the one backstop designed to catch that case disarmed.
            #
            # This is exactly the choice reap_orphaned_work makes for itself:
            # when its own refund fails it leaves the row 'running' so the next
            # pass retries. Left open, the reaper picks this up in <=15 minutes.
            if refund_credit("scan", _credit.event_id, "scan did not complete"):
                complete_work(_credit.event_id, "failed", "aborted or errored")
            else:
                logger.error("refund failed for event %s; leaving work_run open "
                             "so the reaper retries", _credit.event_id)

    # Clear one-shot redirect flags after a scan attempt (success or failure).
    # This keeps refreshes from unexpectedly re-triggering autostart.
    if _intent_autostart:
        patch_query_params({"autostart": None, "next": None})

# ── Results table ──
if st.session_state.df_valid is not None:
    df_valid_display = st.session_state.df_valid.drop(
        columns=["Sample Tweets"], errors="ignore"
    ).copy()

    if len(df_valid_display) > 0:
        # Say how old the chatter is whenever it did not come from X just now.
        # The page badges "Real-time social sentiment", and a corpus may be up
        # to six hours old -- unlabelled, that is a claim the product does not
        # keep. Stale-but-labelled is honest; stale-and-silent is the failure
        # mode this codebase keeps rediscovering.
        _age_s = float(st.session_state.get("scan_corpus_age_s") or 0.0)
        if _age_s >= 60:
            _mins = int(_age_s // 60)
            _age_label = f"{_mins // 60}h {_mins % 60}m" if _mins >= 60 else f"{_mins}m"
            _freshness = f"Market chatter from {_age_label} ago"
        else:
            _freshness = "Updated just now"

        # Market Scan has exactly three sentiment states. Sparse evidence is
        # grouped separately instead of masquerading as a fourth sentiment.
        if "Mentions" not in df_valid_display.columns:
            df_valid_display["Mentions"] = 0
        if "Evidence" not in df_valid_display.columns:
            df_valid_display["Evidence"] = 0
        for _numeric_column in ("Mentions", "Evidence"):
            df_valid_display[_numeric_column] = (
                pd.to_numeric(
                    df_valid_display[_numeric_column], errors="coerce"
                )
                .fillna(0)
                .clip(lower=0)
                .astype(int)
            )
        _labels = df_valid_display["Overall Sentiment"].fillna("").str.lower()
        df_valid_display["_group"] = [
            0 if label in _ASSERTED and evidence >= 3 else 1
            for label, evidence in zip(_labels, df_valid_display["Evidence"])
        ]
        df_valid_display = df_valid_display.sort_values(
            ["_group", "Evidence", "Mentions", "Ticker"],
            ascending=[True, False, False, True],
        )
        df_valid_display = df_valid_display.reset_index(drop=True)

        _scored_count = int((df_valid_display["_group"] == 0).sum())
        _low_count = int((df_valid_display["_group"] == 1).sum())
        _summary_parts = [f"{_scored_count} with a sentiment signal"]
        if _low_count:
            _summary_parts.append(f"{_low_count} need more evidence")
        # Deep Analyze has its own sector context. Never let an independent
        # analysis (whose sector can legitimately be "unknown") relabel an
        # already-completed Market Scan.
        _result_sector = (
            st.session_state.get("demo_scan_sector")
            or st.session_state.get("selected_sector")
            or sector
        )
        if str(_result_sector).strip().lower() == "unknown":
            _result_sector = sector
        st.markdown(
            f'<div class="scan-results-intro">'
            f'<div><h2>{html.escape(str(_result_sector).title())} scan · {len(df_valid_display)} stocks</h2>'
            f'<p>{" · ".join(_summary_parts)}</p></div>'
            f'<div class="scan-results-freshness">{_freshness}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        # Row buttons only enqueue work. Execute it here, outside all table
        # columns, so Streamlit can mount a prominent progress surface before
        # the long request starts.
        _analysis_notice = None
        _pending_analysis = st.session_state.get(
            "_pending_discovery_analysis"
        )
        if _pending_analysis:
            _pending_ticker = str(
                _pending_analysis.get("ticker") or ""
            ).strip().upper()
            _known_scan_tickers = {
                str(value).strip().upper()
                for value in df_valid_display["Ticker"].tolist()
            }
            if _pending_ticker in _known_scan_tickers:
                _analysis_notice = _process_pending_discovery_analysis(
                    _pending_analysis
                )
            else:
                logger.warning(
                    "discarded pending analysis for ticker outside scan: %s",
                    _pending_ticker,
                )
                st.session_state.pop("_pending_discovery_analysis", None)
        if _analysis_notice:
            render_system_state(kind="error", **_analysis_notice)

        # Reuse prices for an unchanged result set. Previously every analysis
        # button rerun refetched all closes before starting the paid request,
        # extending the stale/dim interval with unrelated network work.
        tickers_for_prices = [str(t) for t in df_valid_display["Ticker"].tolist()]
        _price_cache_key = tuple(sorted(
            str(ticker).strip().upper() for ticker in tickers_for_prices
        ))
        if st.session_state.get("_scan_last_close_key") == _price_cache_key:
            last_close_map = dict(
                st.session_state.get("_scan_last_close_map") or {}
            )
        else:
            last_close_map = {}
            _price_prog = st.progress(0)
            _price_status = st.empty()
            _price_status.markdown(
                processing_state_html("Fetching recent closing prices…"),
                unsafe_allow_html=True,
            )
            try:
                _price_prog.progress(40)
                last_close_map = get_last_close_prices_best_effort(
                    tickers_for_prices
                )
                _price_prog.progress(100)
                st.session_state["_scan_last_close_key"] = _price_cache_key
                st.session_state["_scan_last_close_map"] = last_close_map
            except Exception:
                logger.exception("Last close price lookup failed")
                last_close_map = {}
                # Cache the completed attempt for this scan generation. A
                # failed optional price lookup must not rerun immediately
                # after paid analysis and recreate an unexplained dim interval.
                st.session_state["_scan_last_close_key"] = _price_cache_key
                st.session_state["_scan_last_close_map"] = {}
            finally:
                _price_prog.empty()
                _price_status.empty()

        _selected_ticker = st.session_state.get("selected_ticker")
        _scan_tickers = {
            str(value).strip().upper()
            for value in df_valid_display["Ticker"].tolist()
        }
        # A delivered recommendation card is the paid product. Detailed signal
        # excerpts are optional for results returned by older core-api builds,
        # so their absence must never expose a second paid Analyze action.
        _has_delivered_analysis = bool(
            _selected_ticker
            and str(_selected_ticker).strip().upper() in _scan_tickers
            and st.session_state.get("deep_analysis_card")
        )
        _workspace = st.container(key="scan_result_workspace")
        if _has_delivered_analysis:
            # The paid result is one full-width decision surface above the
            # shortlist. This preserves the table's financial-data tracks and
            # gives the evidence disclosure enough room without a nested rail.
            _analysis_col = _workspace.container(key="scan_workspace_analysis")
            _results_col = _workspace.container(key="scan_workspace_results")
        else:
            _results_col, _analysis_col = _workspace, None

        # Populate the result container immediately. Creating an empty slot,
        # painting the whole shortlist, then backfilling a long card above it
        # caused a large post-render layout shift that looked like the page had
        # jumped or become stuck.
        if _has_delivered_analysis and _analysis_col is not None:
            with _analysis_col:
                with st.container(key="selected_analysis_panel"):
                    render_delivered_analysis_result(
                        card=st.session_state.deep_analysis_card,
                        analysis_results=(
                            st.session_state.deep_analysis_results or {}
                        ),
                        ticker=_selected_ticker,
                        sector=_result_sector,
                        key_suffix=f"_discovery_{_selected_ticker}",
                        element_id="selected-analysis",
                        freshness="Analysis generated now",
                    )

        # Header and rows share this one track contract. Keeping the action
        # track wider prevents paid-action labels from wrapping in split view.
        _SCAN_RESULT_COLUMNS = [1.75, 0.72, 0.92, 0.62, 1.15]

        def _render_scan_header(signal_label: str, parent=None) -> None:
            if parent is None:
                parent = _results_col
            _header = parent.container(
                key=f"scan_header_{signal_label.lower().replace(' ', '_')}"
            )
            _header_cols = _header.columns(_SCAN_RESULT_COLUMNS, gap="small")
            for _col, _label in zip(
                _header_cols,
                ["Stock", "Last close", signal_label, "Social posts", "Action"],
            ):
                _col.markdown(
                    f'<span style="font-size:0.72rem;font-weight:700;letter-spacing:0.06em;'
                    f'text-transform:uppercase;color:var(--muted);">{_label}</span>',
                    unsafe_allow_html=True,
                )

        if _scored_count:
            _results_col.markdown(
                '<div class="scan-section-label">Sentiment signals</div>',
                unsafe_allow_html=True,
            )
            _render_scan_header("Sentiment")

        _low_parent = None
        if _low_count:
            _selected_for_expander = st.session_state.get("selected_ticker")
            _selected_is_low = bool(
                _selected_for_expander
                and (
                    (df_valid_display["_group"] == 1)
                    & (df_valid_display["Ticker"] == _selected_for_expander)
                ).any()
            )
        _low_header_shown = False
        for _, row in df_valid_display.iterrows():
            ticker_symbol = row["Ticker"]
            company_name = row["Company Name"]
            overall_sentiment = row["Overall Sentiment"]
            _is_low_evidence = int(row["_group"]) == 1
            _mentions = int(row.get("Mentions") or 0)
            _evidence = int(row.get("Evidence") or 0)
            last_close = last_close_map.get(str(ticker_symbol).upper())
            last_close_display = "N/A" if last_close is None else f"${float(last_close):.2f}"

            _is_selected = ticker_symbol == st.session_state.get("selected_ticker")
            if _is_low_evidence and not _low_header_shown:
                # Create this lazily after every scored row has rendered;
                # Streamlit fixes a container's page position when created.
                _low_parent = _results_col.expander(
                    f"Needs more evidence ({_low_count})",
                    expanded=(not bool(_scored_count)) or _selected_is_low,
                )
                _low_parent.caption(
                    "These stocks had too little directional evidence for a "
                    "Bullish, Bearish, or Neutral scan result."
                )
                _render_scan_header("Evidence state", parent=_low_parent)
                _low_header_shown = True

            _safe_ticker = "".join(
                character if character.isalnum() else "_"
                for character in str(ticker_symbol)
            )
            _ticker_html = html.escape(str(ticker_symbol))
            _company_html = html.escape(str(company_name))
            _row_prefix = "scan_row_selected" if _is_selected else "scan_row"
            _row_parent = _low_parent if _is_low_evidence else _results_col
            _row = _row_parent.container(key=f"{_row_prefix}_{_safe_ticker}")
            col1, col2, col3, col4, col5 = _row.columns(
                _SCAN_RESULT_COLUMNS, gap="small"
            )
            with col1:
                st.markdown(
                    f'<div class="scan-stock-cell"><strong>{_ticker_html}</strong>'
                    f'<span>{_company_html}</span></div>',
                    unsafe_allow_html=True,
                )
            with col2:
                st.markdown(
                    f'<div class="scan-meta-cell"><span class="scan-mobile-label">'
                    f'Last close</span>{last_close_display}</div>',
                    unsafe_allow_html=True,
                )
            with col3:
                if _is_low_evidence:
                    if _evidence <= 0:
                        _evidence_label = "Unscored"
                    elif _evidence == 1:
                        _evidence_label = "Single mention"
                    else:
                        _evidence_label = "Limited signal"
                    st.markdown(
                        f'<div class="scan-meta-cell"><span class="scan-mobile-label">'
                        f'Evidence state</span><span class="scan-evidence-state">'
                        f'{_evidence_label}</span></div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        '<div class="scan-meta-cell"><span class="scan-mobile-label">'
                        'Sentiment</span>' + _sentiment_pill(overall_sentiment) + '</div>',
                        unsafe_allow_html=True,
                    )
            with col4:
                st.markdown(
                    f'<div class="scan-meta-cell"><span class="scan-mobile-label">'
                    f'Social posts</span><span class="scan-social-posts">'
                    f'{_mentions}</span></div>',
                    unsafe_allow_html=True,
                )
            with col5:
                _has_selected_result = bool(
                    _is_selected and st.session_state.get("deep_analysis_card")
                )
                if _has_selected_result:
                    st.markdown(
                        f'<a class="scan-view-result" href="#selected-analysis" '
                        f'aria-label="View {_ticker_html} analysis result" '
                        f'aria-current="true">View result</a>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.button(
                        "Analyze · 1 credit",
                        key=f"deep_analyze_{ticker_symbol}",
                        use_container_width=True,
                        disabled=_credits <= 0,
                        on_click=_queue_discovery_analysis,
                        args=(ticker_symbol, _result_sector),
                    )
        _results_col.markdown(
            '<div class="scan-table-note">Market Scan reports sentiment only: '
            'Bullish, Bearish, or Neutral. Analyze a stock to get a separate '
            'Buy, Watch, or Avoid recommendation.</div>',
            unsafe_allow_html=True,
        )

    else:
        st.markdown(
            """
            <div style="
              border:1px solid rgba(148,163,184,.15);
              border-radius:16px;
              padding:32px 24px;
              text-align:center;
              background:rgba(15,23,42,.45);
              margin:1rem 0;
            ">
              <div style="font-size:2rem;margin-bottom:10px;">🔭</div>
              <div style="font-size:1.05rem;font-weight:700;color:rgba(229,231,235,.90);margin-bottom:6px;">No signals found this scan</div>
              <div style="color:rgba(148,163,184,.75);font-size:0.90rem;max-width:380px;margin:0 auto;">
                Not enough chatter in this sector right now. Try a different sector or run again in a few hours when momentum picks up.
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

# Performance statistics (show in expander) - HIDDEN FROM UI
# with st.expander("📊 Performance & Database Stats"):
#     from utils.finance import get_cache_stats, get_ticker_master_list

#     # Show ticker database stats
#     ticker_db = get_ticker_master_list()
#     db_size = len(ticker_db) if ticker_db else 0

#     col1, col2 = st.columns(2)

#     with col1:
#         st.metric("US Stock Database", f"{db_size} tickers")
#         st.caption("Comprehensive US stock database")

#     with col2:
#         cache_stats = get_cache_stats()
#         st.metric("Price Data Cache", f"{cache_stats['stock_data_cache']['entries']} entries")
#         st.caption("30-minute cache for price data")

#     st.success("✅ **Optimized Performance**: Local database validation eliminates most API calls!")
#     st.info("• Ticker validation: Instant (local database lookup)")
#     st.info("• Price data: Cached for 30 minutes")
#     st.info("• Only price analysis requires API calls")

close_page()
