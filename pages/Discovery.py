import streamlit as st

# Ensure project root is on sys.path (avoids collisions with any installed `utils` package on Streamlit Cloud)
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json
import html
import pandas as pd
import logging

from utils.navigation import render_sidebar_navigation, render_top_nav
from utils.ui import apply_theme, close_page, render_evidence_check
from utils.finance import get_last_close_prices_best_effort
from utils.deep_analysis import ANALYSIS_PROMPTS
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

# Sidebar navigation
render_sidebar_navigation()
render_top_nav(active="market_scan")
apply_theme()


# Compact task header. The public Home page carries the marketing narrative;
# authenticated users arrive here to scan.
st.markdown(
    """
    <div class="discovery-page-header">
      <h1>Market Scan</h1>
      <p>Find stocks gaining unusual social attention, then analyze any candidate for a Buy, Watch, or Avoid recommendation.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

from utils.scan_intent import get_query_params, patch_query_params

# ---- Intent prefill (optional, for direct links) ----
_qp = get_query_params()
_intent_sector = (_qp.get("sector") or "").strip().lower()
_intent_autostart = (_qp.get("autostart") or "").strip().lower() in {"1", "true", "yes", "y", "on"}

# Apply sector intent once (do not stomp user changes on reruns)
if _intent_sector and not st.session_state.get("_intent_sector_applied"):
    st.session_state["discovery_sector"] = _intent_sector
    st.session_state["_intent_sector_applied"] = True

# Determine whether we should auto-run the scan on this load.
# Primary mechanism is session_state (Home -> Auth -> Discovery).
_autostart_scan = bool(st.session_state.pop("_autostart_discovery_scan", False))
if _intent_autostart and not st.session_state.get("_scan_autostart_consumed"):
    st.session_state["_scan_autostart_consumed"] = True
    _autostart_scan = True

from utils.guard import require_active_account
from utils.auth import refresh_session_if_needed, flush_pending_rt_save
flush_pending_rt_save()
from utils.credits import consume_credit, refund_credit, complete_work

_profile = require_active_account()

st.markdown(
    """
    <style>
    /* Discovery page styling; global theme comes from utils.ui.apply_theme() */

    /* Main container spacing (match Home exactly) */
    div[data-testid="stMainBlockContainer"] {
      max-width: 1100px;
      margin: 0 auto;
      padding-left: clamp(16px, 4vw, 28px);
      padding-right: clamp(16px, 4vw, 28px);
      padding-top: 0.25rem;
    }

    /* Kill Streamlit's default top padding that creates the dead gap */
    div[data-testid="stMainBlockContainer"] > div:first-child,
    div[data-testid="stVerticalBlock"] > div:first-child {
      margin-top: 0 !important;
      padding-top: 0 !important;
    }

    /* Streamlit injects extra block padding - neutralise it */
    section[data-testid="stMain"] > div {
      padding-top: 0 !important;
    }

    .discovery-wrapper {
      max-width: 1100px;
      margin: 0 auto;
      padding: 0;
    }

    /* Scan controls card (aligned with Home Market Scan card) */
    .st-key-discovery_scan_card {
      border: 1px solid rgba(148,163,184,0.18);
      background: linear-gradient(180deg, rgba(15,23,42,.92), rgba(15,23,42,.72));
      border-radius: 16px;
      padding: 16px 16px 14px 16px;
      box-shadow: 0 10px 28px rgba(0,0,0,.35);
      margin-bottom: 0.85rem;
    }
    .st-key-discovery_scan_card .cap-title {
      font-weight: 800;
      font-size: 1.00rem;
      margin: 0 0 4px 0;
      color: rgba(229,231,235,.98);
    }
    .st-key-discovery_scan_card .cap-desc {
      margin: 0 0 12px 0;
      color: rgba(229,231,235,.78);
      font-size: 0.93rem;
      line-height: 1.45;
      max-width: 56ch;
    }
    /* Keep dropdown + button aligned on the same baseline */
    .st-key-discovery_scan_card [data-testid="stHorizontalBlock"] {
      align-items: flex-end !important;
      gap: 10px !important;
    }
    .st-key-discovery_scan_card [data-baseweb="select"] > div {
      border-radius: 12px !important;
      min-height: 38px !important;
    }
    .st-key-discovery_scan_card .stButton > button {
      min-height: 38px !important;
      border-radius: 12px !important;
    }

    /* Control row */
    .control-hint {
      color: var(--muted);
      font-size: 0.9rem;
      margin-top: 0.25rem;
    }

    /* Metrics row tweaks */
    [data-testid="stMetric"] {
      border: 1px solid var(--border);
      background: rgba(15,23,42,.65);
      border-radius: 14px;
      padding: 12px 14px;
    }
    [data-testid="stMetric"] label {
      color: var(--muted) !important;
    }

    /* Inputs */
    [data-baseweb="select"] > div,
    [data-baseweb="input"] > div {
      background-color: rgba(2,6,23,.55) !important;
      border-color: var(--border) !important;
      color: var(--text) !important;
    }

    /* Select dropdown menu (Streamlit/Browser differences: BaseWeb + native fallbacks) */
    [data-baseweb="popover"] { z-index: 9999; }

    /* BaseWeb list surfaces */
    [data-baseweb="popover"] [data-baseweb="menu"],
    [data-baseweb="popover"] ul[role="listbox"],
    [data-baseweb="popover"] div[role="listbox"],
    ul[role="listbox"],
    div[role="listbox"],
    [role="listbox"],
    [role="list"],
    [role="menu"] {
      background-color: #0F172A !important;
      border: 1px solid var(--border) !important;
      border-radius: 14px !important;
      overflow: hidden;
      box-shadow: 0 16px 40px rgba(0,0,0,.45) !important;
    }

    /* BaseWeb option rows */
    [role="option"],
    [role="menuitem"] {
      background-color: transparent !important;
      color: #E5E7EB !important;
      opacity: 1 !important;
    }
    [role="option"]:hover,
    [role="menuitem"]:hover {
      background-color: rgba(56,189,248,.16) !important;
    }
    [role="option"][aria-selected="true"] {
      background-color: rgba(56,189,248,.22) !important;
    }

    /* Streamlit selectbox virtual dropdown (this is what you're seeing) */
    /* Streamlit selectbox virtual dropdown (this is what you're seeing) */
    ul[data-testid="stSelectboxVirtualDropdown"],
    [data-testid="stSelectboxVirtualDropdown"] {
      background: #0F172A !important;
      background-color: #0F172A !important;
      border: 1px solid var(--border) !important;
      border-radius: 14px !important;
      box-shadow: 0 16px 40px rgba(0,0,0,.45) !important;
    }

    /* Ensure list items inherit dark background */
    ul[data-testid="stSelectboxVirtualDropdown"] li {
      background: transparent !important;
      background-color: transparent !important;
      color: #E5E7EB !important;
      opacity: 1 !important;
    }
    ul[data-testid="stSelectboxVirtualDropdown"] li:hover {
      background: rgba(56,189,248,.16) !important;
      background-color: rgba(56,189,248,.16) !important;
    }

    /* Force text within options */
    ul[data-testid="stSelectboxVirtualDropdown"] li *,
    ul[data-testid="stSelectboxVirtualDropdown"] * {
      color: #E5E7EB !important;
      opacity: 1 !important;
    }

    /* Native <select> fallback (Windows light theme can force pale options) */
    select {
      background-color: rgba(2,6,23,.55) !important;
      color: #E5E7EB !important;
      border-color: var(--border) !important;
    }
    select option {
      background-color: #0F172A !important;
      color: #E5E7EB !important;
    }

    /* Buttons */
    .stButton > button {
      border-radius: 12px;
      border: 1px solid rgba(56,189,248,0.28);
      background: rgba(15, 23, 42, 0.85);
      background-color: rgba(15, 23, 42, 0.85);
      color: #E5E7EB;
      font-weight: 650;
      opacity: 1;
      filter: none;
    }
    .stButton > button:hover {
      border-color: rgba(56, 189, 248, 0.55);
      background: rgba(15, 23, 42, 1.0);
      background-color: rgba(15, 23, 42, 1.0);
    }

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

    /* Primary CTA */
    /* Primary buttons (Scan X) - must override the generic button rule */
    button[data-testid="stBaseButton-primary"],
    .stButton > button[kind="primary"] {
      background: linear-gradient(180deg, rgba(56,189,248,.95), rgba(14,116,144,.95)) !important;
      background-color: transparent !important;
      border: 1px solid rgba(56,189,248,.45) !important;
      color: #001018 !important;
      font-weight: 650 !important;
    }

    /* Disabled state readability (fix Deep Analyze looking "invisible") */
    .stButton > button:disabled {
      background: rgba(15, 23, 42, 0.55) !important;
      color: rgba(229, 231, 235, 0.70) !important;
      border-color: rgba(56,189,248,0.20) !important;
      opacity: 1 !important;
      filter: none !important;
    }

    /* Release A: task-first hierarchy and native Streamlit result rows. */
    .discovery-page-header {
      max-width: 760px;
      margin: 0 0 1.5rem;
    }
    .discovery-page-header h1 {
      margin: 0 0 0.35rem;
      color: var(--text);
      font-size: clamp(2rem, 4vw, 2.65rem);
      line-height: 1.08;
      letter-spacing: -0.035em;
      font-weight: 760;
    }
    .discovery-page-header p {
      margin: 0;
      color: var(--muted);
      font-size: 1rem;
      line-height: 1.55;
    }
    .st-key-discovery_scan_card {
      padding: 1.1rem 1.15rem 0.95rem;
      border: 1px solid var(--border);
      border-radius: var(--radius-panel);
      background: rgba(15, 23, 42, 0.68);
      box-shadow: none;
      margin-bottom: 1.25rem;
    }
    .scan-balance {
      min-height: 44px;
      display: flex;
      align-items: center;
      color: var(--muted);
      font-size: 0.86rem;
      white-space: nowrap;
    }
    .scan-balance strong {
      color: var(--text);
      font-weight: 720;
    }
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
      padding: 0.72rem 0.15rem;
      border-bottom: 1px solid rgba(148, 163, 184, 0.14);
    }
    [class*="st-key-scan_row_selected_"] {
      padding-left: 0.65rem;
      padding-right: 0.65rem;
      border: 1px solid rgba(56, 189, 248, 0.42);
      border-radius: var(--radius-control);
      background: rgba(56, 189, 248, 0.055);
    }
    .scan-stock-cell strong {
      color: var(--text);
      font-size: 0.96rem;
      letter-spacing: 0.01em;
    }
    .scan-stock-cell span,
    .scan-attention,
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
      text-decoration: none !important;
    }
    .scan-view-result:hover { background: rgba(56,189,248,.18); }
    .selected-analysis-heading {
      margin: 2rem 0 .75rem;
      scroll-margin-top: 1rem;
    }
    .selected-analysis-heading h2 {
      margin: 0 0 .2rem;
      color: var(--text);
      font-size: 1.35rem;
      letter-spacing: -.02em;
    }
    .selected-analysis-heading p {
      margin: 0;
      color: var(--muted);
      font-size: .84rem;
    }

    @media (max-width: 720px) {
      .discovery-page-header { margin-bottom: 1rem; }
      .st-key-discovery_scan_card [data-testid="stHorizontalBlock"] {
        flex-wrap: wrap;
      }
      .st-key-discovery_scan_card [data-testid="column"] {
        flex: 1 1 100% !important;
        width: 100% !important;
      }
      .scan-results-intro {
        align-items: flex-start;
        flex-direction: column;
        gap: 0.35rem;
      }
      [class*="st-key-scan_header_"] { display: none !important; }
      [class*="st-key-scan_row_"] [data-testid="stHorizontalBlock"] {
        flex-wrap: wrap;
        gap: 0.35rem;
      }
      [class*="st-key-scan_row_"] [data-testid="column"] {
        flex: 1 1 calc(50% - 0.5rem) !important;
        width: auto !important;
      }
      [class*="st-key-scan_row_"] [data-testid="column"]:first-child {
        flex-basis: 100% !important;
      }
      [class*="st-key-scan_row_"] [data-testid="column"]:last-child {
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
      [class*="st-key-scan_row_"] .stButton > button {
        width: 100%;
      }
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

# Scan controls (align with Home card styling)
with st.container(key="discovery_scan_card"):
    st.markdown(
        '<h2 class="cap-title">Scan a sector</h2>',
        unsafe_allow_html=True,
    )

    sel_col, btn_col, meter_col = st.columns([1.25, 1.0, 0.8])

    with sel_col:
        SECTOR_OPTIONS = [
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
        ]

        # Prefer query-param intent, otherwise use prior session selection.
        _default_sector = (
            (st.session_state.get("discovery_sector") or "").strip().lower()
            or (st.session_state.get("selected_sector") or "").strip().lower()
            or SECTOR_OPTIONS[0]
        )
        if _default_sector not in SECTOR_OPTIONS:
            _default_sector = SECTOR_OPTIONS[0]

        # Seed the key instead of passing index=. Streamlit renders a visible
        # warning -- in the user's face, not the log -- when a keyed widget has
        # BOTH a default and a value set through the Session State API, which is
        # what the query-param handler above does. Session state is the single
        # source of truth; the guard keeps a user's own selection intact on
        # rerun and only repairs a missing or stale-invalid value.
        if st.session_state.get("discovery_sector") not in SECTOR_OPTIONS:
            st.session_state["discovery_sector"] = _default_sector

        sector = st.selectbox(
            "Sector",
            options=SECTOR_OPTIONS,
            key="discovery_sector",
            label_visibility="visible",
        )

    with btn_col:
        st.markdown("<div style='height:1.68rem'></div>", unsafe_allow_html=True)
        scan_clicked = st.button(
            "Run scan · 1 credit",
            type="primary",
            use_container_width=True,
        )

    with meter_col:
        # The same 1.68rem spacer btn_col uses, so the meter sits on the
        # button's baseline rather than floating above it.
        st.markdown("<div style='height:1.68rem'></div>", unsafe_allow_html=True)
        _credits = int((_profile or {}).get("credits") or 0)
        if _credits <= 1:
            # Purchase affordance appears only when it is useful: blocked or
            # one action away from blocked. The checkout behavior is unchanged.
            billing.render_credit_meter(profile=_profile, key="discovery")
        else:
            _credit_word = "credit" if _credits == 1 else "credits"
            st.markdown(
                f'<div class="scan-balance"><strong>{_credits}</strong>&nbsp;{_credit_word} available</div>',
                unsafe_allow_html=True,
            )

    # Last scan context line
    _last_sector = st.session_state.get("selected_sector")
    _last_count = len(st.session_state.df_valid) if st.session_state.get("df_valid") is not None else None
    if _last_sector and _last_count is not None:
        st.markdown(
            f'<div style="color:var(--muted);font-size:0.78rem;margin-top:0.35rem;">'
            f'Last scan: <b style="color:rgba(148,163,184,.80);">{_last_sector}</b> · {_last_count} stocks found</div>',
            unsafe_allow_html=True,
        )

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

    # NOTHING TO RUN, SO NOTHING TO CHARGE -- checked before the debit.
    # Unlike Deep Analyze this page can still scan locally while the in-process
    # path exists, so an unconfigured core-api is a fallback rather than a
    # refusal. But the local path needs the PORTAL to hold an X token, and the
    # migration is moving that the other way: with neither, every click would
    # take a credit and refund it, once per click, each refund another chance
    # for the RPC to fail and lose it for real.
    # NOTHING TO CALL, SO NOTHING TO CHARGE. Mandatory now that the local
    # path is gone: without it a misconfigured CORE_API_URL would take a
    # credit and refund it, once per click, each refund another chance for the
    # RPC to fail and lose it for real. configured() asks the same question
    # _base() asks, so a bare host or an http:// URL is refused here.
    if not _client.configured():
        logger.error("scan unavailable: core-api not configured")
        st.error("Scanning is temporarily unavailable. No credit has been used.")
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
            f'<div style="color:rgba(229,231,235,.85);font-size:0.92rem;font-weight:600;">'
            f'\U0001F4E1 Scanning X for {sector} momentum...</div>',
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
            (20, "\U0001F4E1 Scanning X for %s momentum..." % sector),
            (40, "\U0001F50D Filtering noise, validating tickers..."),
            (60, "\u26A1 Building your shortlist..."),
            (80, "\U0001F9E0 Reading the mood on your shortlist..."),
            (92, "\U0001F4CA Ranking what people are talking about..."),
        ]
        _i = 0
        while not _done.wait(timeout=1.5):
            if _i < len(_steps):
                _pct, _msg = _steps[_i]
                progress_bar.progress(_pct)
                status_text.markdown(
                    f'<div style="color:rgba(229,231,235,.85);'
                    f'font-size:0.92rem;font-weight:600;">{_msg}</div>',
                    unsafe_allow_html=True)
                _i += 1

        progress_bar.progress(100)
        status_text.empty()
        progress_bar.empty()

        if "error" in _holder:
            _refunded = refund_credit("scan", _credit.event_id,
                                      f"scan failed: {str(_holder['error'])[:120]}")
            st.markdown(
                '<div style="border:1px solid rgba(239,68,68,.25);border-radius:16px;padding:24px;'
                'background:rgba(239,68,68,.04);margin:1rem 0;text-align:center;">'
                '<div style="font-size:1.5rem;margin-bottom:8px;">\u26A0\uFE0F</div>'
                '<div style="font-weight:700;color:rgba(248,113,113,.95);font-size:1.0rem;margin-bottom:4px;">Something went wrong</div>'
                '<div style="color:rgba(148,163,184,.80);font-size:0.88rem;">'
                + ("Your credit was not used." if _refunded
                   else "The scan hit an unexpected error.")
                + '</div></div>', unsafe_allow_html=True)
            _bail()

        # ONE SHAPE, because there is one path. The local Scan and the
        # RemoteScan had to be reconciled here while both existed.
        _r = _holder["remote"]
        _rows, _ok, _err, _kind = _r.rows, _r.ok, _r.error, _r.kind
        _x_err, _age, _posts = _r.x_error, _r.corpus_age_s, _r.posts_seen
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
                            else "We are returning your credit; it may take a moment to appear.")
            st.markdown(
                f"""
                <div style="border:1px solid rgba(245,158,11,.28);border-radius:14px;padding:18px 20px;
                  background:rgba(245,158,11,.05);margin:0.5rem 0;text-align:center;">
                  <div style="font-size:1.2rem;margin-bottom:6px;">\U0001F9FA</div>
                  <div style="font-weight:700;color:rgba(251,191,36,.95);font-size:0.95rem;margin-bottom:4px;">Could not build the scan for this sector</div>
                  <div style="color:rgba(148,163,184,.75);font-size:0.82rem;">{_credit_line} This is usually temporary \u2014 try again shortly.</div>
                </div>
                """,
                unsafe_allow_html=True,
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
            refund_credit("scan", _credit.event_id,
                          _REASONS.get(_kind,
                                       f"scan error: {(_err or '')[:120]}"))
            if _kind == "credentials":
                _tone, _icon, _title, _body = (
                    "239,68,68", "\U0001F511", "Configuration error",
                    "Missing API credentials. Contact support if this keeps happening.")
            elif _kind in ("network", "transport"):
                _tone, _icon, _title, _body = (
                    "245,158,11", "\U0001F4E1", "Connection issue",
                    "Couldn't reach the data source. Check your connection and try again.")
            elif _kind == "ticker_db":
                _tone, _icon, _title, _body = (
                    "239,68,68", "\u274C", "Could not load ticker database",
                    "Please check the data directory.")
            else:
                _tone, _icon, _title, _body = (
                    "239,68,68", "\u26A0\uFE0F", "Something went wrong",
                    "The scan hit an unexpected error. Try again in a moment "
                    "\u2014 this is usually temporary.")
            st.markdown(
                f"""
                <div style="border:1px solid rgba({_tone},.28);border-radius:16px;padding:24px;
                  background:rgba({_tone},.05);margin:1rem 0;text-align:center;">
                  <div style="font-size:1.5rem;margin-bottom:8px;">{_icon}</div>
                  <div style="font-weight:700;color:rgba(248,113,113,.95);font-size:1.0rem;margin-bottom:4px;">{_title}</div>
                  <div style="color:rgba(148,163,184,.80);font-size:0.88rem;">{_body}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            _bail()

        if _x_err:
            st.markdown(
                f"""
                <div style="border:1px solid rgba(245,158,11,.28);border-radius:14px;padding:18px 20px;
                  background:rgba(245,158,11,.05);margin:0.5rem 0;text-align:center;">
                  <div style="font-size:1.2rem;margin-bottom:6px;">\U0001F4E1</div>
                  <div style="font-weight:700;color:rgba(251,191,36,.95);font-size:0.95rem;margin-bottom:4px;">X data feed unavailable</div>
                  <div style="color:rgba(148,163,184,.75);font-size:0.82rem;">{_x_err[:200]}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        if _posts == 0:
            if _x_err:
                # Upstream failure, zero posts: the user paid and got nothing.
                if refund_credit("scan", _credit.event_id, f"x api: {_x_err[:120]}"):
                    st.info("Your credit was not used.")
            else:
                # A genuinely empty result is an answer, not a failure -- the
                # scan ran and the sector simply had no chatter. Still charged,
                # and _delivered says so: without it the finally refunded every
                # single time while this comment claimed the opposite, which
                # made a quiet sector an unlimited supply of free scans paid
                # for at X.
                _delivered = True
                st.warning("No posts returned from X for this query.")
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

            st.session_state.df_valid = df_valid
            st.session_state.df_unvalidated = None  # not shown
            st.session_state.selected_sector = sector
            st.session_state.selected_ticker = None
            st.session_state.deep_analysis_results = None
            st.session_state.deep_analysis_card = None

            # Results are durable in session_state: the scan ran and produced an
            # answer. An empty answer is still an answer -- the sector genuinely
            # had no validated chatter -- so it is charged, as before.
            _delivered = True

            if len(df_valid) == 0:
                st.warning("\u26A0\uFE0F No validated stock tickers found. Try a different sector/time window.")
        else:
            # Posts were fetched and scored, they just contained no tickers.
            # Work was done and an answer given, so this stays charged.
            _delivered = True
            st.warning("\u26A0\uFE0F No stock tickers found in the posts. Try a different search query.")

    # `except KeyError` and `except requests.exceptions.RequestException`
    # used to live here. They are gone rather than left as dead code: scan()
    # returns instead of raising, so neither could ever fire again, and their
    # panels are now selected by the normalised _kind above. A dead handler for a
    # message the user still sees is worse than no handler -- it reads as
    # coverage.
    except Exception:
        logger.exception("Discovery scan failed")
        refund_credit("scan", _credit.event_id, "unhandled scan error")
        st.markdown(
            """
            <div style="border:1px solid rgba(239,68,68,.25);border-radius:16px;padding:24px;
              background:rgba(239,68,68,.04);margin:1rem 0;text-align:center;">
              <div style="font-size:1.5rem;margin-bottom:8px;">⚠️</div>
              <div style="font-weight:700;color:rgba(248,113,113,.95);font-size:1.0rem;margin-bottom:4px;">Something went wrong</div>
              <div style="color:rgba(148,163,184,.80);font-size:0.88rem;">The scan hit an unexpected error. Try again in a moment — this is usually temporary.</div>
            </div>
            """,
            unsafe_allow_html=True,
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

# --- Admin-only: Save demo snapshots for Home (local only; no API calls) ---
# These buttons save the *current* results to data/education/ so Home can show
# a "clean" educational snapshot without running any paid API calls.

ADMIN_MODE = bool(st.secrets.get("ADMIN_MODE", False))

if ADMIN_MODE:
    with st.expander("🛠 Admin: Demo snapshot tools", expanded=False):
        # Save Scan snapshot (validated table)
        if st.session_state.get("df_valid") is not None and len(st.session_state.df_valid) > 0:
            if st.button("💾 Save Scan Snapshot for Home (demo)"):
                try:
                    from pathlib import Path
                    import json

                    out_dir = Path(__file__).resolve().parents[1] / "data" / "education"
                    out_dir.mkdir(parents=True, exist_ok=True)

                    df_out = st.session_state.df_valid.drop(columns=["Valid", "Mentions", "Sample Tweets", "Evidence"], errors="ignore")

                    payload = {
                        "sector": st.session_state.get("selected_sector") or "",
                        "generated_at": "snapshot",
                        "validated_rows": df_out.to_dict(orient="records"),
                    }
                    (out_dir / "scan_latest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
                    st.success("Saved: data/education/scan_latest.json")
                except Exception:
                    st.error("Something went wrong. Please try again later.")
        else:
            st.caption("Run a scan to enable saving scan snapshot.")

        # Save Deep Analyze snapshot
        if st.session_state.get("selected_ticker") and st.session_state.get("deep_analysis_results"):
            if st.button("💾 Save Deep Analyze Snapshot for Home (demo)"):
                try:
                    from pathlib import Path
                    import json

                    out_dir = Path(__file__).resolve().parents[1] / "data" / "education"
                    out_dir.mkdir(parents=True, exist_ok=True)

                    payload = {
                        "ticker": st.session_state.get("selected_ticker") or "",
                        "sector": st.session_state.get("selected_sector") or "",
                        "generated_at": "snapshot",
                        "analysis_results": st.session_state.get("deep_analysis_results") or {},
                    }
                    (out_dir / "deep_latest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
                    st.success("Saved: data/education/deep_latest.json")
                except Exception:
                    st.error("Something went wrong. Please try again later.")
        else:
            st.caption("Run Deep Analyze to enable saving deep snapshot.")

def _render_deep_panel(ticker, sector, deep_results):
    """Render the deep analysis panel inline below a ticker row.

    Adjudicates from the SAME evidence ledger as pages/Deep_Analysis.py. This
    path used to run generate_ai_summary instead, so clicking Deep Analyze on a
    scan row and typing the same ticker on the other page produced two
    different products at one credit price -- and only the typed path ever
    reached verdict_log, making that table a biased sample of one entry route.
    """
    # READ, do not recompute. Everything below this line used to be re-derived
    # from the session-state corpus on every rerun -- roughly 90 posts rescored,
    # a price call, a benchmark call, a fresh adjudication and a fresh Monte
    # Carlo, each time the user changed a sector or clicked a download button.
    # The analysis is now computed once, by the same function the other page
    # calls, and carried in session state.
    # THE CARD, computed by core-api and carried in session state. This panel
    # used to re-derive the whole analysis from the corpus on every rerun, then
    # (briefly) read an in-process Analysis object. Both are gone: the same
    # service answers this button and the Deep Analysis page, so one credit
    # buys one product regardless of which one the user pressed.
    _card = st.session_state.get("deep_analysis_card") or {}
    if not _card:
        st.info("This analysis is from an earlier session. Run Deep Analyze "
                "again to see it.")
        return

    def _esc(value) -> str:
        """Render service/social text literally inside developer-authored HTML."""
        return html.escape(str(value), quote=True)

    _evidence = _card.get("evidence") or {}
    # NOTE: the card carries a `movement` block (targets, band, horizon) and
    # this page draws none of it, while pages/Deep_Analysis.py renders a full
    # Movement Profile table from the same card for the same credit. Not read
    # here on purpose -- adding the table is a product decision, not cleanup --
    # but the asymmetry is real and is recorded here rather than left implicit.
    _pts = _evidence.get("price_points") or 0

    _price = _proj = _hold = "Unavailable"
    for _t in (_card.get("tiles") or []):
        if _t.get("key") == "last_price":
            _price = _t.get("value", "Unavailable")
        elif _t.get("key") == "range_30d":
            _proj = _t.get("value", "Unavailable")
        elif _t.get("key") == "drawdown_first":
            _hold = _t.get("value", "Unavailable")

    # THE NUMBER THAT DECIDED THE CALL, matching pages/Deep_Analysis.py. This
    # page printed the corpus union -- ~90 of 98 posts -- beside a verdict
    # resting on 5 independent voices, while the other page printed the 5.
    _ev_ct = _evidence.get("independent_voices")
    _mentions_ct = _ev_ct if _ev_ct is not None else (_evidence.get("mentions") or 0)

    _rec = str(_card.get("verdict") or "—")
    _conf = str(_card.get("confidence") or "—")
    # None means the fallback reported no score. Rendering it as +0.00 states
    # "Neutral" as a finding, which is what card() now refuses to do for us.
    _avg_raw = _card.get("avg_sentiment")
    _has_sent = _avg_raw is not None
    _avg_sent = float(_avg_raw) if _has_sent else 0.0
    if "buy" in _rec.lower():
        _rec_color = "rgba(56,189,248,.95)"
        _rec_border_color = "rgba(56,189,248,.28)"
    elif "avoid" in _rec.lower():
        _rec_color = "rgba(239,68,68,.90)"
        _rec_border_color = "rgba(239,68,68,.25)"
    else:
        _rec_color = "rgba(245,158,11,.90)"
        _rec_border_color = "rgba(245,158,11,.25)"
    _conf_color = "rgba(56,189,248,.90)" if _conf.lower()=="high" else "rgba(245,158,11,.90)" if _conf.lower()=="moderate" else "rgba(148,163,184,.80)"
    _sent_color = "rgba(56,189,248,.95)" if _avg_sent>=0.10 else "rgba(239,68,68,.88)" if _avg_sent<=-0.10 else "rgba(148,163,184,.85)"
    # ONE WORD: the mood tile renders _sent_lbl.split(" ")[0].
    _sent_lbl = ("Unscored" if not _has_sent else
                 f"Bullish ({_avg_sent:+.2f})" if _avg_sent>=0.10 else
                 f"Bearish ({_avg_sent:+.2f})" if _avg_sent<=-0.10 else
                 f"Neutral ({_avg_sent:+.2f})")
    _sent_score_txt = f"Score {_avg_sent:+.3f}" if _has_sent else "No score"
    _sector_lbl = (" · " + _esc(sector.title())) if sector and sector.lower() not in ("unknown", "") else ""
    # From the card, not from a fourth copy of these dictionaries. The previous
    # copy was "kept verbatim in step" by hand with two other copies, which is
    # the arrangement that let one page say "Proj. Gain 30d" for months after
    # the other retired the phrase.
    _rec_sub = _card.get("headline", "")
    _conf_sub = _card.get("confidence_note", "")
    _bar_pct = min(100, int(abs(_avg_sent)*250 + {"high":30,"moderate":15,"low":0}.get(_conf.lower(),0)))
    _conf_bar = {"high":90,"moderate":55,"low":25}.get(_conf.lower(),30)

    def _bar(pct, color):
        return f'<div style="width:100%;height:4px;background:rgba(148,163,184,.12);border-radius:999px;margin-top:6px;"><div style="width:{pct}%;height:4px;background:{color};border-radius:999px;"></div></div>'

    _mc = "border-radius:12px;padding:14px 16px 12px 16px;background:rgba(15,23,42,.75);flex:1;min-width:0;display:flex;flex-direction:column;gap:4px;"
    _rationale_html = "".join(
        f'<li style="margin-bottom:5px;color:rgba(229,231,235,.85);font-size:0.88rem;line-height:1.45;">{_esc(b)}</li>'
        for b in _card.get("rationale", [])
    )

    _fc = "border-radius:10px;padding:10px 14px;background:rgba(15,23,42,.55);border:1px solid rgba(148,163,184,.12);flex:1;"
    _fl = "font-size:0.68rem;font-weight:700;letter-spacing:0.05em;text-transform:uppercase;color:rgba(148,163,184,.55);margin-bottom:3px;"
    _fv = "font-size:1.00rem;font-weight:800;color:rgba(248,250,252,.92);"
    # Labels kept in step with utils.ui.render_recommendation_panel, which this
    # markup duplicates. They had drifted: "Proj. Gain 30d" and "Hold Period"
    # were retired there for promising a forecast the band does not make, and
    # survived here -- so the same volatility range was captioned as a projected
    # gain on one page and as a range on the other.
    _price_row = f'<div class="ss-analysis-price" style="display:flex;gap:8px;margin-bottom:14px;flex-wrap:nowrap;"><div style="{_fc}"><div style="{_fl}">Last Price</div><div style="{_fv}">{_esc(_price)}</div></div><div style="{_fc}"><div style="{_fl}">30d range (vol)</div><div style="{_fv}">{_esc(_proj)}</div></div><div style="{_fc}"><div style="{_fl}">Drawdown first</div><div style="{_fv}">{_esc(_hold)}</div></div></div>' if _price != "Unavailable" or _proj != "Unavailable" or _hold != "Unavailable" else ""

    _tilt_color = {"Bullish":"rgba(56,189,248,.95)","Bearish":"rgba(239,68,68,.90)","Neutral":"rgba(148,163,184,.80)"}

    # ── Coverage summary table (all signals at a glance) ──
    _cov_rows = ""
    for _pn, _res in (deep_results or {}).items():
        _tf = (ANALYSIS_PROMPTS.get(_pn,{}) or {}).get("timeframe","")
        _ev = int(_res.get("mention_count",0) or 0)
        _ov = (_res.get("overall_sentiment") or "").lower()
        _st2 = "Unavailable" if _ov=="error" else ("No Signal" if _ev==0 else ("Strong" if _ev>5 else "Weak"))
        _tl = "Unavailable" if _ov=="error" else ("Neutral" if _ev==0 else _ov.title())
        _tc = _tilt_color.get(_tl,"rgba(148,163,184,.80)")
        _cov_rows += (
            f'<tr style="border-bottom:1px solid rgba(148,163,184,.10);">'
            f'<td style="padding:9px 10px;color:rgba(229,231,235,.90);font-size:0.80rem;">{_esc(_pn)}</td>'
            f'<td style="padding:9px 10px;color:rgba(148,163,184,.70);font-size:0.80rem;">{_esc(_tf)}</td>'
            f'<td style="padding:9px 10px;text-align:center;color:rgba(148,163,184,.80);font-size:0.80rem;">{_ev}</td>'
            f'<td style="padding:9px 10px;color:rgba(148,163,184,.80);font-size:0.80rem;">{_esc(_st2)}</td>'
            f'<td style="padding:9px 10px;font-size:0.80rem;font-weight:700;color:{_tc};">{_esc(_tl)}</td>'
            f'</tr>'
        )
    _cov_table = (
        f'<table class="ss-analysis-table" style="width:100%;border-collapse:collapse;background:rgba(15,23,42,.60);border-radius:10px;overflow:hidden;margin-bottom:16px;">'
        f'<thead><tr style="border-bottom:1px solid rgba(148,163,184,.20);">'
        + "".join(f'<th style="padding:8px 10px;text-align:{"center" if h=="Evidence" else "left"};font-size:0.68rem;font-weight:700;letter-spacing:0.05em;text-transform:uppercase;color:rgba(148,163,184,.55);">{h}</th>' for h in ["Signal Type","Timeframe","Evidence","Strength","Tilt"])
        + f'</tr></thead><tbody>{_cov_rows}</tbody></table>'
    ) if _cov_rows else ""

    # ── Detailed per-signal breakdown ──
    _detail_sections = ""
    for _pn, _res in (deep_results or {}).items():
        _tf  = (ANALYSIS_PROMPTS.get(_pn,{}) or {}).get("timeframe","")
        _ev  = int(_res.get("mention_count",0) or 0)
        _ov  = (_res.get("overall_sentiment") or "neutral").lower()
        _sc  = _res.get("sentiment_score", 0.0)
        _ins = _res.get("insights") or ""
        _themes = _res.get("key_themes") or []
        _samples = _res.get("sample_tweets") or []
        _tl = ("Neutral" if _ev==0 else _ov.title())
        _tc = _tilt_color.get(_tl, "rgba(148,163,184,.80)")

        _metrics = (
            f'<div class="ss-analysis-detail-metrics" style="display:flex;gap:8px;margin:8px 0 6px 0;">'
            f'<div style="flex:1;background:rgba(15,23,42,.55);border-radius:8px;padding:8px 10px;border:1px solid rgba(148,163,184,.10);">'
            f'<div style="font-size:0.65rem;color:rgba(148,163,184,.55);text-transform:uppercase;letter-spacing:0.05em;">Sentiment Score</div>'
            f'<div style="font-size:0.92rem;font-weight:700;color:rgba(248,250,252,.90);">{float(_sc):.3f}</div></div>'
            f'<div style="flex:1;background:rgba(15,23,42,.55);border-radius:8px;padding:8px 10px;border:1px solid rgba(148,163,184,.10);">'
            f'<div style="font-size:0.65rem;color:rgba(148,163,184,.55);text-transform:uppercase;letter-spacing:0.05em;">Overall</div>'
            f'<div style="font-size:0.92rem;font-weight:700;color:{_tc};">{_esc(_tl)}</div></div>'
            f'<div style="flex:1;background:rgba(15,23,42,.55);border-radius:8px;padding:8px 10px;border:1px solid rgba(148,163,184,.10);">'
            f'<div style="font-size:0.65rem;color:rgba(148,163,184,.55);text-transform:uppercase;letter-spacing:0.05em;">Mentions</div>'
            f'<div style="font-size:0.92rem;font-weight:700;color:rgba(248,250,252,.90);">{_ev}</div></div>'
            f'</div>'
        )

        _themes_html = ""
        if _themes:
            _chips = "".join(f'<span style="display:inline-block;background:rgba(56,189,248,.10);border:1px solid rgba(56,189,248,.20);border-radius:999px;padding:2px 9px;font-size:0.70rem;color:rgba(148,163,184,.85);margin:2px 3px 2px 0;">{_esc(t)}</span>' for t in _themes)
            _themes_html = f'<div style="margin:6px 0 8px 0;"><span style="font-size:0.72rem;font-weight:700;color:rgba(148,163,184,.55);text-transform:uppercase;letter-spacing:0.05em;">Themes: </span>{_chips}</div>'

        _tweets_html = ""
        if _samples:
            _tweet_items = "".join(f'<div style="border-left:2px solid rgba(56,189,248,.25);padding:5px 10px;margin-bottom:6px;color:rgba(229,231,235,.75);font-size:0.78rem;line-height:1.45;font-style:italic;">{i}. {_esc(t)}</div>' for i, t in enumerate(_samples, 1))
            _tweets_html = f'<div style="margin-top:6px;"><div style="font-size:0.72rem;font-weight:700;color:rgba(148,163,184,.55);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px;">Sample posts:</div>{_tweet_items}</div>'

        _insights_html = f'<div style="font-size:0.78rem;color:rgba(148,163,184,.65);margin-bottom:4px;"><b>Insights:</b> {_esc(_ins)}</div>' if _ins else ""
        _detail_sections += (
            f'<div style="border-top:1px solid rgba(148,163,184,.10);padding:12px 0;">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;">'
            f'<span style="font-size:0.84rem;font-weight:700;color:rgba(229,231,235,.90);">{_esc(_pn)}</span>'
            f'<span style="font-size:0.72rem;color:rgba(148,163,184,.55);">{_esc(_tf)}</span>'
            f'</div>'
            f'{_metrics}'
            f'{_insights_html}'
            f'{_themes_html}'
            f'{_tweets_html}'
            f'</div>'
        )

    _panel_html = f"""<style>
      @media (max-width: 700px) {{
        .ss-analysis-header, .ss-analysis-summary,
        .ss-analysis-price, .ss-analysis-detail-metrics {{ flex-wrap:wrap !important; }}
        .ss-analysis-summary > div, .ss-analysis-price > div,
        .ss-analysis-detail-metrics > div {{ min-width:140px !important; }}
        .ss-analysis-table {{ display:block;overflow-x:auto !important; }}
      }}
    </style><div style="
      width:100%;box-sizing:border-box;
      background:rgba(2,6,23,0.97);
      border:1px solid rgba(56,189,248,.25);
      border-radius:16px;
      box-shadow:0 8px 40px rgba(0,0,0,.55);
      overflow:hidden;
      font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
    ">
      <div class="ss-analysis-header" style="padding:16px 20px 12px 20px;border-bottom:1px solid rgba(56,189,248,.15);background:linear-gradient(180deg,rgba(56,189,248,.07),rgba(2,6,23,0));display:flex;align-items:center;justify-content:space-between;gap:12px;">
        <div>
          <div style="font-size:0.68rem;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:rgba(56,189,248,.75);">Deep Analysis{_sector_lbl}</div>
          <div style="font-size:1.40rem;font-weight:850;letter-spacing:-0.02em;color:rgba(248,250,252,.98);">{_esc(ticker)}</div>
        </div>
        <div style="text-align:right;">
          <div style="font-size:0.68rem;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:rgba(148,163,184,.50);">Signal</div>
          <div style="font-size:1.25rem;font-weight:850;color:{_rec_color};">{_esc(_rec)}</div>
          <div style="font-size:0.72rem;color:rgba(148,163,184,.60);">Confidence: {_esc(_conf)}</div>
        </div>
      </div>
      <div style="padding:16px 20px 20px 20px;">
        <div class="ss-analysis-summary" style="display:flex;gap:8px;margin-bottom:14px;">
          <div style="{_mc}border:1px solid {_rec_border_color};"><div style="font-size:0.68rem;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:rgba(148,163,184,.55);">Recommendation</div><div style="font-size:1.05rem;font-weight:850;color:{_rec_color};">{_esc(_rec)}</div><div style="font-size:0.72rem;color:rgba(148,163,184,.55);">{_esc(_rec_sub)}</div>{_bar(_bar_pct,_rec_color)}</div>
          <div style="{_mc}border:1px solid rgba(148,163,184,.15);"><div style="font-size:0.68rem;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:rgba(148,163,184,.55);">Confidence</div><div style="font-size:1.05rem;font-weight:850;color:{_conf_color};">{_esc(_conf)}</div><div style="font-size:0.72rem;color:rgba(148,163,184,.55);">{_esc(_conf_sub)}</div>{_bar(_conf_bar,_conf_color)}</div>
          <div style="{_mc}border:1px solid rgba(148,163,184,.15);"><div style="font-size:0.68rem;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:rgba(148,163,184,.55);">Market Mood</div><div style="font-size:1.05rem;font-weight:850;color:{_sent_color};">{_esc(_sent_lbl.split(" ")[0])}</div><div style="font-size:0.72rem;color:rgba(148,163,184,.55);">{_esc(_sent_score_txt)}</div>{_bar(min(100,int(abs(_avg_sent)*280)),_sent_color)}</div>
        </div>
        {_price_row}
        <div style="color:rgba(148,163,184,.45);font-size:0.72rem;margin-bottom:14px;">{_esc(_mentions_ct)} posts analysed · {_esc(_pts)} price points</div>
        <div style="font-size:0.78rem;font-weight:700;color:rgba(148,163,184,.60);letter-spacing:0.05em;text-transform:uppercase;margin-bottom:8px;">Why this signal</div>
        <ul style="margin:0 0 18px 16px;padding:0;">{_rationale_html}</ul>
        <details style="margin-top:4px;">
          <summary style="cursor:pointer;font-size:0.80rem;font-weight:700;color:rgba(148,163,184,.60);letter-spacing:0.05em;text-transform:uppercase;padding:10px 0;list-style:none;display:flex;align-items:center;gap:8px;">
            <span style="color:rgba(56,189,248,.70);">▶</span> Full breakdown
          </summary>
          <div style="margin-top:8px;">
            {_cov_table}
            <div style="font-size:0.78rem;font-weight:700;color:rgba(148,163,184,.60);letter-spacing:0.05em;text-transform:uppercase;margin-bottom:4px;">Detailed breakdown</div>
            {_detail_sections}
          </div>
        </details>
      </div>
    </div>"""

    # Render in the page DOM so the result has one scrollbar, responds to the
    # viewport, and remains reachable by the accessible anchor above it.
    st.html(_panel_html)

    # The pillar readout, and the verdict record. This path previously wrote
    # NEITHER: a scan-row Deep Analyze produced no evidence check and no
    # verdict_log row, so the table built to measure this product only ever saw
    # users who typed the ticker on the other page.
    if _card.get("pillars"):
        try:
            # THE CARD, not the Verdict: one renderer, and the remote path
            # has no Verdict object to hand it.
            render_evidence_check(_card, ticker)
        except Exception:
            logger.warning("discovery: evidence check render failed", exc_info=True)

    # NO WRITE HERE. core-api persisted both rows before it answered, under
    # feature="discovery" and route="discovery" -- the tags this page used to
    # apply itself, passed on the request so the cohort is unchanged.
    #
    # The rerun guard that used to live here is unnecessary as a result: this
    # function runs on every rerun that re-selects the row, but it no longer
    # writes anything, so a sector change or a download click cannot append a
    # duplicate. One request, one write, decided by the service.


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
        _result_sector = st.session_state.get("selected_sector") or sector
        st.markdown(
            f'<div class="scan-results-intro">'
            f'<div><h2>{html.escape(str(_result_sector).title())} scan · {len(df_valid_display)} stocks</h2>'
            f'<p>{" · ".join(_summary_parts)}</p></div>'
            f'<div class="scan-results-freshness">{_freshness}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        if st.session_state.pop("_scroll_to_deep_panel", False):
            st.success(
                f"Analysis ready for {st.session_state.get('selected_ticker')}. "
                "Use View result to jump to the completed analysis."
            )

        # Load last close prices with a progress indicator so the user sees activity
        tickers_for_prices = [str(t) for t in df_valid_display["Ticker"].tolist()]
        last_close_map = {}
        _price_prog = st.progress(0)
        _price_status = st.empty()
        _price_status.markdown(
            '<div style="color:rgba(148,163,184,.70);font-size:0.82rem;">💹 Fetching live prices...</div>',
            unsafe_allow_html=True,
        )
        try:
            _price_prog.progress(40)
            last_close_map = get_last_close_prices_best_effort(tickers_for_prices)
            _price_prog.progress(100)
        except Exception as e:
            logger.exception("Last close price lookup failed")
            last_close_map = {}
        finally:
            _price_prog.empty()
            _price_status.empty()

        def _render_scan_header(signal_label: str, parent=st) -> None:
            _header = parent.container(
                key=f"scan_header_{signal_label.lower().replace(' ', '_')}"
            )
            _header_cols = _header.columns([1.8, 0.75, 1.0, 0.7, 0.95])
            for _col, _label in zip(
                _header_cols,
                ["Stock", "Last close", signal_label, "Attention", "Action"],
            ):
                _col.markdown(
                    f'<span style="font-size:0.72rem;font-weight:700;letter-spacing:0.06em;'
                    f'text-transform:uppercase;color:var(--muted);">{_label}</span>',
                    unsafe_allow_html=True,
                )

        if _scored_count:
            st.markdown('<div class="scan-section-label">Sentiment signals</div>', unsafe_allow_html=True)
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
                _low_parent = st.expander(
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
            _row_parent = _low_parent if _is_low_evidence else st
            _row = _row_parent.container(key=f"{_row_prefix}_{_safe_ticker}")
            col1, col2, col3, col4, col5 = _row.columns(
                [1.8, 0.75, 1.0, 0.7, 0.95]
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
                _attention_word = "mention" if _mentions == 1 else "mentions"
                st.markdown(
                    f'<div class="scan-meta-cell"><span class="scan-mobile-label">'
                    f'Attention</span><span class="scan-attention">'
                    f'{_mentions} {_attention_word}</span></div>',
                    unsafe_allow_html=True,
                )
            with col5:
                _has_selected_result = bool(
                    _is_selected and st.session_state.get("deep_analysis_results")
                )
                if _has_selected_result:
                    st.markdown(
                        '<a class="scan-view-result" href="#selected-analysis">'
                        'View result ↓</a>',
                        unsafe_allow_html=True,
                    )
                    _analyze_clicked = False
                else:
                    _analyze_clicked = st.button(
                        "Analyze · 1 credit",
                        key=f"deep_analyze_{ticker_symbol}",
                        use_container_width=True,
                    )
                if _analyze_clicked:
                    # Open a request scope BEFORE charging. Without this the
                    # ContextVar still holds whatever id the last action on this
                    # page set, and consume_credit reuses a non-"-" value as its
                    # idempotency key -- a STALE key returns duplicate_request,
                    # which credits.py maps to ok=True, delivering the analysis
                    # with nothing debited. It also left every log line from this
                    # path stamped "-", so the ledger row pointed at no logs.
                    _drid = new_request_id()
                    # NOTHING TO CALL, SO NOTHING TO CHARGE -- checked before
                    # the debit, exactly as pages/Deep_Analysis.py does. A
                    # misconfiguration must never take a credit and refund it.
                    if not _client.configured():
                        logger.error("deep_analyze unavailable: core-api not configured")
                        st.error("Deep Analysis is temporarily unavailable. "
                                 "No credit has been used.")
                        _bail()
                    _dcredit = consume_credit(
                        "deep_analyze",
                        {"ticker": ticker_symbol, "sector": _result_sector, "page": "discovery"},
                    )
                    if not _dcredit.ok:
                        billing.render_credit_refusal(
                            _dcredit,
                            f"The full analysis for {ticker_symbol} costs "
                            f"1 credit.", key="row")
                        _bail()

                    # Charged work: try/finally, not try/except. Streamlit's
                    # abort raises StopException/RerunException, which derive
                    # from BaseException and bypass every `except Exception`.
                    _ddelivered = False
                    try:
                        # Silently refresh token before long operation to prevent session expiry mid-run
                        refresh_session_if_needed()

                        st.session_state.selected_ticker = ticker_symbol
                        st.session_state.deep_analysis_results = None
                        st.session_state.deep_analysis_card = None

                        _deep_error = None
                        _disc_prog = st.progress(0)
                        _disc_status = st.empty()
                        _disc_status.markdown(
                            f'<div style="color:rgba(229,231,235,.85);font-size:0.92rem;font-weight:600;">'
                            f'📡 Gathering market chatter for <b>{ticker_symbol}</b>...</div>',
                            unsafe_allow_html=True,
                        )
                        _disc_prog.progress(10)

                        import threading as _th

                        _disc_holder: dict = {}
                        _disc_done = _th.Event()
                        _disc_sector = _result_sector

                        def _disc_run():
                            # A new thread starts with an empty context, so the
                            # request id reverts to "-" without this.
                            _set_request_id(_drid)
                            try:
                                # THE SAME SERVICE THE OTHER PAGE CALLS. This
                                # button charges the same deep_analyze credit
                                # for the same product; leaving it in-process
                                # while Deep_Analysis went remote meant a
                                # broken container was loud on one route and
                                # silent on the other, and which button the
                                # user pressed decided what they got.
                                _r = _client.analyze_remote(
                                    ticker_symbol, _disc_sector,
                                    feature="discovery", route="discovery",
                                    event_id=getattr(_dcredit, "event_id", None))
                                if _r.ok:
                                    logger.info(
                                        "discovery deep_analyze served by "
                                        "CORE-API in %.1fs ticker=%s",
                                        _r.elapsed_s or -1, ticker_symbol)
                                    _disc_holder["card"] = _r.card
                                    _disc_holder["result"] = _r.analysis_results
                                else:
                                    _disc_holder["error"] = _r.error
                                    _disc_holder["pre_spend"] = _r.retryable
                            except Exception as _e:
                                _disc_holder["error"] = str(_e)
                                logger.exception(f"Deep analysis error for {ticker_symbol}")
                            finally:
                                _disc_done.set()

                        _disc_thread = _th.Thread(target=_disc_run, daemon=True)
                        _disc_thread.start()

                        _disc_steps = [
                            (20, "📰 Reading what traders are saying..."),
                            (35, "📊 Weighing bullish vs bearish signals..."),
                            (50, "🔍 Cross-referencing sentiment over time..."),
                            (65, "📈 Running price projection models..."),
                            (78, "⚡ Measuring signal strength..."),
                            (88, "🔬 Building your recommendation..."),
                        ]
                        _disc_step_idx = 0
                        while not _disc_done.wait(timeout=1.5):
                            if _disc_step_idx < len(_disc_steps):
                                _dp, _dm = _disc_steps[_disc_step_idx]
                                _disc_prog.progress(_dp)
                                _disc_status.markdown(
                                    f'<div style="color:rgba(229,231,235,.85);font-size:0.92rem;font-weight:600;">{_dm}</div>',
                                    unsafe_allow_html=True,
                                )
                                _disc_step_idx += 1

                        _disc_prog.progress(100)
                        _disc_status.empty()
                        _disc_prog.empty()

                        if "error" in _disc_holder:
                            # Charged before the work started; the work failed.
                            if refund_credit("deep_analyze", _dcredit.event_id,
                                             f"analysis failed: {str(_disc_holder['error'])[:120]}"):
                                _deep_error = (f"Analysis failed for {ticker_symbol}. "
                                               "Your credit was not used — try again in a moment.")
                            else:
                                _deep_error = f"Analysis failed for {ticker_symbol}. Try again in a moment."
                        elif not _disc_holder.get("result"):
                            refund_credit("deep_analyze", _dcredit.event_id, "analysis returned no results")
                            _deep_error = (f"No results for {ticker_symbol}. "
                                           "Your credit was not used — try again in a moment.")
                        elif not _disc_holder.get("card"):
                            # Neither adjudicator produced anything. Falling
                            # through here marked the run delivered, kept the
                            # credit, wrote no row, and showed a grey box the
                            # user cannot tell from a quiet market. The other
                            # page refunds this state; so does this one now.
                            logger.error("no verdict and no legacy summary for %s",
                                         ticker_symbol)
                            refund_credit("deep_analyze", _dcredit.event_id,
                                          "no summary could be produced")
                            _deep_error = (f"No analysis could be produced for "
                                           f"{ticker_symbol}. Your credit was not "
                                           "used — try again in a moment.")
                        else:
                            st.session_state.deep_analysis_results = _disc_holder.get("result")
                            st.session_state.deep_analysis_card = _disc_holder.get("card")
                            # deep_analysis_event_id is GONE with the writes it
                            # existed for. It let the panel key a rerun guard on
                            # the credit event, back when the panel itself wrote
                            # to verdict_log and signal_log on every rerun. The
                            # service writes once per request now, so there is
                            # no duplicate to guard against and nothing read it.
                            st.session_state.selected_ticker = ticker_symbol
                            st.session_state["_scroll_to_deep_panel"] = True
                            # Set BEFORE st.rerun(): it raises RerunException, so
                            # anything after it never runs.
                            _ddelivered = True
                            st.rerun()

                        if _deep_error:
                            st.markdown(
                                f'<div style="border:1px solid rgba(239,68,68,.30);border-radius:12px;padding:14px 16px;'
                                f'background:rgba(239,68,68,.06);color:rgba(248,113,113,.95);margin:0.5rem 0;">'
                                f'⚠️ {_deep_error}</div>',
                                unsafe_allow_html=True,
                            )
                    finally:
                        # NOTE: the success path below calls st.rerun(), which
                        # itself raises RerunException -- so _ddelivered must be
                        # set BEFORE it, or a successful analysis would refund
                        # itself. Idempotent, so it no-ops after an explicit refund.
                        if _ddelivered:
                            complete_work(_dcredit.event_id, "completed",
                                          f"ticker={ticker_symbol}")
                        else:
                            # Close the run ONLY if the refund actually landed --
                            # the same choice the scan path in this file already
                            # makes. refund_credit returns False without raising
                            # when its RPC fails, and reap_orphaned_work scans
                            # status='running' alone, so closing the row as
                            # 'failed' after a failed refund deletes the credit
                            # and disarms the backstop meant to return it.
                            if refund_credit("deep_analyze", _dcredit.event_id,
                                             "deep analysis did not complete"):
                                complete_work(_dcredit.event_id, "failed",
                                              "aborted or errored")
                            else:
                                logger.error(
                                    "refund failed for event %s; leaving "
                                    "work_run open so the reaper retries",
                                    _dcredit.event_id)
        st.markdown(
            '<div class="scan-table-note">Market Scan reports sentiment only: '
            'Bullish, Bearish, or Neutral. Analyze a stock to get a separate '
            'Buy, Watch, or Avoid recommendation.</div>',
            unsafe_allow_html=True,
        )

        # A selected result appears once, below the shortlist. The paid action
        # becomes an in-page View result link, so the same ticker never offers
        # a second charge beside an already delivered result.
        _selected_ticker = st.session_state.get("selected_ticker")
        if _selected_ticker and st.session_state.get("deep_analysis_results"):
            st.markdown(
                f'<section id="selected-analysis" class="selected-analysis-heading" '
                f'tabindex="-1"><h2>{html.escape(str(_selected_ticker))} analysis result</h2>'
                f'<p>Buy, Watch, or Avoid recommendation based on the completed analysis.</p>'
                f'</section>',
                unsafe_allow_html=True,
            )
            _render_deep_panel(
                _selected_ticker,
                _result_sector,
                st.session_state.deep_analysis_results,
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
