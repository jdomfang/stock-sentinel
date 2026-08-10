import streamlit as st
import streamlit.components.v1 as components

# Ensure project root is on sys.path (avoids collisions with any installed `utils` package on Streamlit Cloud)
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
import json
import pandas as pd
from collections import defaultdict, deque
import logging

from utils.navigation import render_sidebar_navigation, render_top_nav
from utils.ui import apply_theme, close_page, render_recommendation_panel, render_full_analysis_expander, render_evidence_check
from utils.sentiment import extract_tickers_detailed, analyze_sentiment_batch
from utils import x_metrics
from utils.finance import get_ticker_master_list, get_stock_data, get_last_close_prices_best_effort
from utils import corpus_cache
from utils import sector_query
from utils.projections import simple_projection
from utils.deep_analysis import ANALYSIS_PROMPTS, run_deep_analysis, generate_ai_summary


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
render_top_nav()
apply_theme()


# ── Hero rendered BEFORE guard so logged-out users see the full header ──
st.markdown(
    """
    <style>
    .discovery-pre-hero .hero-title {
      font-size: clamp(42px, 5.1vw, 3.55rem); font-weight: 850;
      letter-spacing: -0.035em; line-height: 1.08; margin: 0 0 8px 0;
    }
    .discovery-pre-hero .hero-subtitle {
      color: var(--muted); font-size: clamp(15px, 1.35vw, 1.05rem);
      line-height: 1.45; margin: 0; max-width: 760px;
    }
    .discovery-pre-hero {
      margin: -8.10rem 0 2px 0; padding: 0 2px 2px 2px;
      max-width: 1100px;
    }
    </style>
    <div class="discovery-pre-hero">
      <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px;">
        <span style="display:inline-flex;align-items:center;gap:6px;background:rgba(56,189,248,.06);border:1px solid rgba(56,189,248,.18);border-radius:999px;padding:5px 12px;font-size:0.80rem;font-weight:600;color:rgba(229,231,235,.80);">📡 Real-time social sentiment</span>
        <span style="display:inline-flex;align-items:center;gap:6px;background:rgba(56,189,248,.06);border:1px solid rgba(56,189,248,.18);border-radius:999px;padding:5px 12px;font-size:0.80rem;font-weight:600;color:rgba(229,231,235,.80);">🏦 4,000+ US stocks</span>
        <span style="display:inline-flex;align-items:center;gap:6px;background:rgba(56,189,248,.06);border:1px solid rgba(56,189,248,.18);border-radius:999px;padding:5px 12px;font-size:0.80rem;font-weight:600;color:rgba(229,231,235,.80);">⚡ Signal in under 60 seconds</span>
      </div>
      <div class="hero-title">Finding short-term opportunities shouldn't feel like a full-time job.</div>
      <div class="hero-subtitle">Pick a sector and we identify US stocks gaining unusual attention in your selected sector-fast.</div>
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

    /* Titles */
    .discovery-title {
      font-size: 2.0rem;
      font-weight: 750;
      letter-spacing: -0.02em;
      margin: 0;
      line-height: 1.15;
    }
    .discovery-subtitle {
      color: var(--muted);
      margin-top: 0.25rem;
      margin-bottom: 1.0rem;
      font-size: 0.98rem;
    }

    /* Hero (match Home) */
    /* Hero - exact match to Home */
    .hero {
      margin: -11.0rem 0 2px 0;
      padding: 0 2px 2px 2px;
    }
    .hero-title {
      font-size: clamp(42px, 5.1vw, 3.55rem);
      font-weight: 850;
      letter-spacing: -0.035em;
      line-height: 1.08;
      margin: 0 0 8px 0;
      max-width: 880px;
    }
    .hero-subtitle {
      color: var(--muted);
      font-size: clamp(15px, 1.35vw, 1.05rem);
      line-height: 1.45;
      margin: 0;
      max-width: 760px;
    }
    /* Mobile: remove aggressive negative hero offset (header is different on phones) */
    @media (max-width: 640px) {
      /* Pull the hero up to compensate for Streamlit's extra empty blocks on initial mobile render */
      .hero { margin: -6.8rem 0 10px 0; }
      .hero-title { font-size: clamp(34px, 9.5vw, 44px); }
      .hero-subtitle { font-size: 1.00rem; }
    }
    .hero-chips {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin: 12px 0 10px 0;
    }
    .chip {
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 7px 11px;
      background: rgba(2,6,23,.30);
      color: rgba(229,231,235,.92);
      font-size: 0.92rem;
      backdrop-filter: blur(6px);
    }
    .chip b { color: rgba(229,231,235,.98); }

    /* Subtle label color-coding (keeps values neutral; avoids implying outcomes) */
    .hero-chips .chip:nth-child(1) b { color: rgba(56,189,248,.95); }  /* Signal (accent) */
    .hero-chips .chip:nth-child(2) b { color: rgba(34,197,94,.92); }   /* Projected gain (good) */
    .hero-chips .chip:nth-child(3) b { color: rgba(245,158,11,.92); }  /* Volatility (warn) */
    .hero-chips .chip:nth-child(4) b { color: rgba(148,163,184,.95); } /* Suggested hold (muted) */
    .hero-caveat {
      color: rgba(229,231,235,.70);
      font-size: 0.92rem;
      margin-top: 4px;
    }

    /* Generic card */
    .card {
      border: 1px solid var(--border);
      background: linear-gradient(180deg, rgba(15,23,42,.92), rgba(15,23,42,.75));
      border-radius: 14px;
      padding: 16px;
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
    button[data-testid="stBaseButton-secondary"],
    .stButton > button[kind="secondary"] {
      background: rgba(56,189,248,.08) !important;
      background-color: rgba(56,189,248,.08) !important;
      color: rgba(56,189,248,.95) !important;
      border: 1px solid rgba(56,189,248,0.40) !important;
      font-weight: 700 !important;
      opacity: 1 !important;
      transition: all 0.15s ease !important;
    }
    button[data-testid="stBaseButton-secondary"]:hover,
    .stButton > button[kind="secondary"]:hover {
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

    /* Dataframe */
    .stDataFrame { width: 100%; }

    /* Force KPI grid to fill full container width */
    [data-testid="stMarkdownContainer"]:has(> div[style*="grid-template-columns:1fr 1fr"]) {
      width: 100% !important;
      display: block !important;
    }
    [data-testid="stMarkdownContainer"] > div[style*="grid-template-columns:1fr 1fr"] {
      width: 100% !important;
    }

    /* Validated ticker rows */
    .ticker-row {
      padding: 0.55rem 0.85rem;
      border: 1px solid rgba(148,163,184,0.12);
      border-radius: 12px;
      margin-bottom: 0.40rem;
      background: rgba(15, 23, 42, 0.55);
      transition: background 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease;
      cursor: pointer;
    }
    .ticker-row:hover {
      background: rgba(15, 23, 42, 0.92) !important;
      border-color: rgba(56, 189, 248, 0.45) !important;
      box-shadow: 0 0 0 1px rgba(56,189,248,.12), 0 4px 16px rgba(56,189,248,.07) !important;
    }
    /* Full row highlight on hover — override Streamlit column defaults */
    .ticker-row:hover [data-testid="column"] p,
    .ticker-row:hover [data-testid="column"] span {
      color: rgba(248,250,252,.98) !important;
    }

    /* Top Signal elevated card */
    .ticker-row--top-signal {
      border: 1px solid rgba(56,189,248,.45) !important;
      background: linear-gradient(180deg, rgba(56,189,248,.06), rgba(15,23,42,.85)) !important;
      box-shadow: 0 0 0 1px rgba(56,189,248,.18), 0 8px 24px rgba(56,189,248,.08) !important;
      position: relative;
    }
    .ticker-row--top-signal::before {
      content: "TOP SIGNAL";
      position: absolute;
      top: -10px;
      left: 14px;
      font-size: 0.62rem;
      font-weight: 800;
      letter-spacing: 0.10em;
      color: rgba(56,189,248,.95);
      background: #020617;
      padding: 0 6px;
      border-radius: 4px;
    }

    .ticker-row [data-testid="column"]:nth-child(2) p {
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 28ch;
    }

    /* Hide Streamlit "Made with" footer */
    footer { visibility: hidden; }
    </style>
    """,
    unsafe_allow_html=True,
)

# JS-only UI fix: Streamlit selectbox dropdown can render with forced white background on some builds.
# This mutation observer applies a dark background + readable text whenever the dropdown menu appears.
components.html(
    """
    <script>
    (function () {
      const APPLY_TO = (doc) => {
        // Fix selectbox dropdown
        const ul = doc.querySelector('ul[data-testid="stSelectboxVirtualDropdown"]');
        if (ul) {
          ul.style.setProperty('background-color', '#0F172A', 'important');
          ul.style.setProperty('color', '#E5E7EB', 'important');
          ul.querySelectorAll('li, li *').forEach((el) => {
            el.style.setProperty('color', '#E5E7EB', 'important');
            el.style.setProperty('opacity', '1', 'important');
          });
        }

        // Deep Analyze buttons - teal tinted style via JS (overrides Streamlit forced white)
        doc.querySelectorAll('button[data-testid="stBaseButton-secondary"]').forEach((btn) => {
          const base = () => {
            btn.style.setProperty('background-image', 'none', 'important');
            btn.style.setProperty('background-color', 'rgba(56,189,248,.10)', 'important');
            btn.style.setProperty('border', '1px solid rgba(56,189,248,0.42)', 'important');
            btn.style.setProperty('color', 'rgba(56,189,248,.95)', 'important');
            btn.style.setProperty('font-weight', '700', 'important');
            btn.style.setProperty('opacity', '1', 'important');
            btn.style.setProperty('filter', 'none', 'important');
            btn.querySelectorAll('p, span').forEach((t) => {
              t.style.setProperty('color', 'rgba(56,189,248,.95)', 'important');
            });
          };

          const hover = () => {
            btn.style.setProperty('background-image', 'none', 'important');
            btn.style.setProperty('background-color', 'rgba(56,189,248,.22)', 'important');
            btn.style.setProperty('border', '1px solid rgba(56,189,248,.75)', 'important');
            btn.style.setProperty('color', 'rgba(255,255,255,.98)', 'important');
            btn.style.setProperty('box-shadow', '0 0 14px rgba(56,189,248,.22)', 'important');
            btn.querySelectorAll('p, span').forEach((t) => {
              t.style.setProperty('color', 'rgba(255,255,255,.98)', 'important');
            });
          };

          if (btn.matches(':hover') || btn.matches(':focus')) hover();
          else base();

          if (!btn.dataset.clawdHoverBound) {
            btn.dataset.clawdHoverBound = '1';
            btn.addEventListener('mouseenter', hover);
            btn.addEventListener('mouseleave', base);
            btn.addEventListener('focus', hover);
            btn.addEventListener('blur', base);
          }
        });

        // Restore primary button (Scan X) gradient
        doc.querySelectorAll('button[data-testid="stBaseButton-primary"], button[kind="primary"]').forEach((btn) => {
          btn.style.setProperty('background-image', 'linear-gradient(180deg, rgba(56,189,248,.95), rgba(14,116,144,.95))', 'important');
          btn.style.setProperty('background-color', 'transparent', 'important');
          btn.style.setProperty('border', '1px solid rgba(56,189,248,.45)', 'important');
          btn.style.setProperty('color', '#001018', 'important');
          btn.style.setProperty('font-weight', '650', 'important');
          btn.style.setProperty('opacity', '1', 'important');
        });
      };

      const APPLY = () => {
        // Always apply to current document
        APPLY_TO(document);

        // Also try to apply to parent if accessible
        try {
          if (window.parent && window.parent.document) APPLY_TO(window.parent.document);
        } catch (e) {}
      };

      const obs = new MutationObserver(() => APPLY());
      obs.observe(document.documentElement, { childList: true, subtree: true });
      window.addEventListener('load', APPLY);
      setTimeout(APPLY, 250);
      setTimeout(APPLY, 1000);
      setInterval(APPLY, 750);
    })();
    </script>
    """,
    height=0,
)

st.markdown('<div class="clawd-app-wrapper discovery-wrapper">', unsafe_allow_html=True)

if "selected_ticker" not in st.session_state:
    st.session_state.selected_ticker = None
if "selected_sector" not in st.session_state:
    st.session_state.selected_sector = None
if "deep_analysis_results" not in st.session_state:
    st.session_state.deep_analysis_results = None
if "df_valid" not in st.session_state:
    st.session_state.df_valid = None
if "df_unvalidated" not in st.session_state:
    st.session_state.df_unvalidated = None
if "scan_corpus_age_s" not in st.session_state:
    st.session_state.scan_corpus_age_s = 0.0

# Scan controls (align with Home card styling)
with st.container(key="discovery_scan_card"):
    st.markdown(
        '<div class="cap-title">Market Scan</div>',
        unsafe_allow_html=True,
    )

    sel_col, btn_col, _pad = st.columns([1.4, 0.9, 1.7])

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
            label_visibility="collapsed",
        )

    with btn_col:
        st.markdown("<div style='height:1.68rem'></div>", unsafe_allow_html=True)
        scan_clicked = st.button(
            "Sentinel Scan",
            type="primary",
            use_container_width=True,
        )

    # Last scan context line
    _last_sector = st.session_state.get("selected_sector")
    _last_count = len(st.session_state.df_valid) if st.session_state.get("df_valid") is not None else None
    if _last_sector and _last_count is not None:
        st.markdown(
            f'<div style="color:rgba(148,163,184,.58);font-size:0.78rem;margin-top:-0.35rem;margin-bottom:0.25rem;">'
            f'Last scan: <b style="color:rgba(148,163,184,.80);">{_last_sector}</b> · {_last_count} stocks found</div>',
            unsafe_allow_html=True,
        )

# Posts per basket request. Small on purpose: X's minimum is 10, requests are
# free, and only returned posts are billed -- so fetching 25 four times costs
# exactly what fetching 100 once costs, while letting the loop stop the moment
# it has what it needs. This is the one number here worth tuning from real data.
BASKET_PER_PAGE = 25

# |mean margin| below this reads as Neutral. A PLACEHOLDER: nobody has labelled
# a sample of these posts yet, so this is a starting point to be tuned from
# ground truth, not a measured boundary. Deliberately not derived from the old
# 0.55 confidence threshold, which applied to a different quantity.
SENTIMENT_MARGIN = 0.15

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
def _get_checkout_url(user_id: str) -> str | None:
    """Call Railway payments API to create a Stripe checkout session."""
    try:
        base = st.secrets.get("PAYMENTS_API_BASE_URL", "").rstrip("/")
        secret = st.secrets.get("PAYMENTS_API_SHARED_SECRET", "")
        if not base or not secret:
            return None
        import requests as _req
        r = _req.post(
            f"{base}/create-checkout-session",
            json={"user_id": user_id},
            headers={"X-Payments-Shared-Secret": secret},
            timeout=8,
        )
        if r.status_code == 200:
            return r.json().get("checkout_url")
    except Exception:
        pass
    return None


def _upgrade_modal(reason: str, event_type: str = "scan") -> None:
    """Show a premium upgrade modal inline when out of credits."""
    user = st.session_state.get("auth.user") or {}
    uid = (user.get("id") if isinstance(user, dict) else getattr(user, "id", None)) or ""

    if event_type == "scan":
        icon, title, what_you_get = "📡", "Unlock more scans", [
            "Scan any sector for momentum signals",
            "Processed from real X data in seconds",
            "Shortlist of validated US tickers",
        ]
    else:
        icon, what_you_get = "🔍", [
            "Full sentiment breakdown",
            "Confidence score + trend context",
            "Catalysts, red flags & projections",
            "Clear Buy / Watch / Avoid signal",
        ]
        title = f"Unlock Deep Analysis"

    st.markdown(
        f"""
        <div style="
          border:1px solid rgba(56,189,248,.35);
          background:linear-gradient(180deg,rgba(56,189,248,.06),rgba(15,23,42,.92));
          border-radius:16px;
          padding:24px 24px 20px 24px;
          margin:1rem 0;
          box-shadow:0 0 0 1px rgba(56,189,248,.15),0 12px 32px rgba(56,189,248,.08);
        ">
          <div style="font-size:1.4rem;font-weight:800;color:rgba(248,250,252,.98);margin-bottom:6px;">{icon} {title}</div>
          <div style="color:rgba(148,163,184,.85);font-size:0.93rem;margin-bottom:14px;">{reason}</div>
          <ul style="list-style:none;padding:0;margin:0 0 18px 0;">
            {"".join(f'<li style="color:rgba(229,231,235,.90);font-size:0.93rem;margin-bottom:6px;">✓ {item}</li>' for item in what_you_get)}
          </ul>
        </div>
        """,
        unsafe_allow_html=True,
    )

    checkout_url = _get_checkout_url(uid) if uid else None
    col_a, col_b = st.columns([1.2, 2.8])
    with col_a:
        if checkout_url:
            st.link_button("Buy credits →", checkout_url, type="primary", use_container_width=True)
        else:
            st.button("Buy credits →", type="primary", disabled=True, use_container_width=True)
    with col_b:
        st.caption("Secure checkout via Stripe. Credits never expire.")


if scan_triggered:
    # Must be logged in to scan.
    if not st.session_state.get("auth.user"):
        st.error("Please log in to scan.")
        st.stop()

    # Open a request scope BEFORE the charge, so the debit, every downstream X
    # and Supabase call, and any refund all log under one id -- and so the
    # usage_events row carries it too. This is the correlation key that did not
    # exist when a scan died mid-run and had to be reconstructed by timestamp.
    _rid = new_request_id()
    logger.info("scan requested sector=%s", sector)

    _credit = consume_credit("scan", {"sector": sector, "page": "discovery"})
    if not _credit.ok:
        logger.info("scan refused reason=%s", _credit.reason)
        _upgrade_modal("You've used all your scan credits.", event_type="scan")
        st.stop()

    # Set when X refuses to serve us. Drives the refund below: the user paid for
    # a scan, so if the upstream never delivered any posts they must not be
    # charged for it. Observed in production as a 402 credits-depleted.
    _x_api_error: str | None = None

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
        # Load X Bearer Token from secrets
        x_bearer_token = st.secrets["X_BEARER_TOKEN"]
        # The query is generated from this sector's tickers further down; the
        # hand-written topic-word blocks that used to live here are gone. They
        # were measured at 4% precision in utilities and 23% in technology,
        # against 86-88% for generated baskets in the same hours.

        # Goal-driven scan:
        # - Scan page 1
        # - If page 1 yields 10 validated tickers, stop
        # - Else keep scanning pages until 10 validated OR 300 tweets safety cap
        from utils.deep_analysis import search_x_tweets_page

        SAFETY_CAP_TWEETS = 300
        PER_PAGE = 100
        TARGET_VALIDATED = 10

        # Load comprehensive ticker database once
        ticker_master_list = get_ticker_master_list()
        if not ticker_master_list:
            st.error("❌ Could not load ticker database. Please check the data directory.")
            st.stop()

        # Nasdaq sector strings (stored in Supabase) for strict matching.
        #
        # Imported rather than defined here. The query generator and this
        # validation step must agree on what "utilities" means, and two
        # hand-maintained copies of the same mapping would silently diverge --
        # the generator would ask about one set of tickers while validation
        # accepted another. tests/test_sector_query.py pins the contents to
        # exactly what this block held before it moved.
        ui_to_nasdaq_sectors = sector_query.UI_TO_NASDAQ
        selected_nasdaq_sectors = ui_to_nasdaq_sectors.get((sector or "").lower(), set())

        # Aggregate data by ticker (incremental across pages)
        ticker_data = defaultdict(lambda: {
            'mentions': 0,
            # P(positive) - P(negative) per scoring post. Continuous and signed,
            # so aggregating it cannot tie the way a vote over discrete labels
            # does, and it keeps the difference between a 0.48/0.44 coin flip
            # and a 0.90/0.05 conviction that a single label collapses.
            'margins': [],
            'sample_tweets': []
        })

        # Tweet ids already counted. Baskets overlap -- a post naming tickers
        # from two baskets is returned by both requests -- so without this a
        # single post inflates its tickers' mention counts, and mentions decide
        # the displayed top 10.
        _seen_post_ids: set = set()

        # (text, tickers) for every ticker-bearing post, kept so sentiment can
        # run ONCE after the ranking rather than per page during the fetch.
        _scored_corpus: list = []

        validated_set = set()
        checked_set = set()
        company_by_ticker = {}

        next_token = None
        total_posts = 0

        def _try_validate_from_current_ranking():
            """Validate tickers in mention-rank order until we have 10 validated or no more candidates."""

            # Build ranking from current ticker_data
            ranking = sorted(
                ticker_data.items(),
                key=lambda kv: kv[1].get('mentions', 0),
                reverse=True,
            )
            for ticker, info in ranking:
                if len(validated_set) >= TARGET_VALIDATED:
                    break
                if ticker in checked_set:
                    continue
                checked_set.add(ticker)

                t_up = (ticker or '').upper()
                if t_up not in ticker_master_list:
                    continue

                ticker_info = ticker_master_list[t_up]
                ticker_sector = (ticker_info.get('sector') or '').strip()
                if not selected_nasdaq_sectors:
                    # If we can't map the UI sector to a Nasdaq sector string, be strict and reject.
                    continue

                # Strict match: only accept if the Nasdaq sector matches one of the mapped sectors.
                if ticker_sector not in selected_nasdaq_sectors:
                    continue

                validated_set.add(ticker)
                company_by_ticker[ticker] = ticker_info.get('name', ticker)

        # A no-op placeholder so the try/finally below always has something
        # callable. The real closure is defined after the corpus cache, which
        # is AFTER the query-build failure path can st.stop() -- without this,
        # the finally raises NameError into a bare except and the failure is
        # invisible. Overwritten with the real recorder further down.
        def _record_metrics(displayed: list[str]) -> None:
            return None

        progress_bar = st.progress(0)
        status_text = st.empty()
        status_text.markdown(f"**📡 Scanning X for {sector} momentum...**")

        # ── Build the query from this sector's own tickers ───────────────────
        #
        # NO FALLBACK, DELIBERATELY. If the baskets cannot be built we refund
        # and stop rather than quietly running the old topic query, which was
        # measured at a quarter of this precision. Falling back would hand the
        # user a scan we know is bad without telling them -- the exact silent
        # degradation this codebase keeps paying for.
        _baskets: list[str] = []
        _basket_error: str | None = None
        try:
            _baskets = sector_query.build_baskets(sector, "cashtag")
        except Exception as _e:
            _basket_error = f"{type(_e).__name__}: {str(_e)[:160]}"
            logger.exception("basket generation failed for %s", sector)

        if not _baskets:
            # The credit is returned here. The try/finally below would also
            # refund, but naming the reason makes the ledger row diagnosable
            # instead of "aborted or errored".
            reason = _basket_error or f"no live tickers for sector {sector!r}"
            logger.error("cannot build a query for %s: %s", sector, reason)

            # Clear the progress chrome, or a 0% bar and "Scanning X for
            # finance momentum..." sit above the error panel forever.
            progress_bar.empty()
            status_text.empty()

            # Only promise a refund that actually happened. refund_credit
            # returns False without raising when its RPC fails, and telling a
            # user in writing that they were not charged when they were is
            # worse than the original failure. The try/finally below is the
            # backstop either way.
            _refunded = refund_credit(
                "scan", _credit.event_id, f"query build failed: {reason[:120]}")
            _credit_line = ("Your credit was not used."
                            if _refunded
                            else "We are returning your credit; it may take a moment to appear.")
            st.markdown(
                f"""
                <div style="border:1px solid rgba(245,158,11,.28);border-radius:14px;padding:18px 20px;
                  background:rgba(245,158,11,.05);margin:0.5rem 0;text-align:center;">
                  <div style="font-size:1.2rem;margin-bottom:6px;">🧺</div>
                  <div style="font-weight:700;color:rgba(251,191,36,.95);font-size:0.95rem;margin-bottom:4px;">Could not build the scan for this sector</div>
                  <div style="color:rgba(148,163,184,.75);font-size:0.82rem;">{_credit_line} This is usually temporary — try again shortly.</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.stop()

        # One identity for the whole basket set, so the corpus cache key and the
        # telemetry's query_hash change whenever the generated query changes --
        # which happens nightly as dollar-volume rankings shift.
        query = "cashtag-baskets|" + "|".join(_baskets)
        logger.info("🧺 %d basket(s), %d tickers, sector=%s",
                    len(_baskets), sum(b.count("$") for b in _baskets), sector)

        # ── Shared corpus cache ──────────────────────────────────────────────
        # X bills per POST RETURNED, so the cost of this scan is the number of
        # tweets it pulls. The result is not user-specific -- it is a function
        # of (sector, 24h window) -- and there are only ten sectors, so one
        # fetch can serve every user who scans that sector for the next six
        # hours. Cost stops scaling with users and becomes bounded by the
        # catalogue.
        #
        # The RAW corpus is cached, not the finished table: the tweets are what
        # cost money, and re-scoring them is free. A sentiment fix can then be
        # replayed over posts already paid for.
        _cached = corpus_cache.get("sector", sector, 24, query)
        _cached_pages: list[list[dict]] | None = None
        if _cached is not None:
            # Replay at the SAME page size the corpus was bought at, or the
            # early-stop gates fire at different points than they did live and
            # a cache hit can produce a different top-10 than the scan that
            # paid for it. Basket mode buys in BASKET_PER_PAGE chunks.
            _replay_size = BASKET_PER_PAGE if _baskets else PER_PAGE
            _cached_pages = corpus_cache.chunk_pages(_cached["tweets"], _replay_size)
        _fetched_pages: list[list[dict]] = []

        # Effectiveness telemetry. Every number it reports is derived from posts
        # already paid for, so this adds no X spend and no API calls.
        _tally = x_metrics.ScanTally()
        # A mutable holder, not a bare flag: this block is module-level Streamlit
        # script code, so a nested def cannot rebind an enclosing name.
        _metrics_state = {"written": False}

        def _is_valid_ticker(sym: str) -> bool:
            """The SAME rule _try_validate_from_current_ranking applies.

            Passed into the tally so an uncapped validatable count can be
            computed. It must mirror the real predicate exactly, or the headline
            measurement diverges from what the scan actually accepts.
            """
            info = ticker_master_list.get((sym or "").upper())
            if not info or not selected_nasdaq_sectors:
                return False
            return (info.get("sector") or "").strip() in selected_nasdaq_sectors

        def _record_metrics(displayed: list[str]) -> None:
            """One row per scan, written whichever way the scan ends.

            Called on the empty-result path too: a scan that bought 99 posts and
            displayed nothing is 100% waste, and that is precisely the data
            point worth having. Recording only successful scans would bias the
            waste number downward exactly where it matters most.
            """
            if _metrics_state["written"]:
                return          # one row per scan, whichever path got here first
            _metrics_state["written"] = True
            x_metrics.record_scan(
                event_id=getattr(_credit, "event_id", None),
                subject=sector,
                query=query,
                tally=_tally,
                validated=validated_set,
                displayed=displayed,
                posts_billed=sum(len(p) for p in _fetched_pages),
                pages_fetched=len(_fetched_pages),
                from_cache=_cached is not None,
                # Uncapped validation, so the 10-cap cannot hide the answer.
                is_valid=_is_valid_ticker,
                stop_reason=(
                    "validated_target" if len(validated_set) >= TARGET_VALIDATED
                    else "safety_cap" if total_posts >= SAFETY_CAP_TWEETS
                    else "exhausted"
                ),
                corpus_key=corpus_cache.make_key("sector", sector, 24, query),
            )

        def _next_page(token: str | None) -> dict:
            """Serve page N from the cached corpus, or buy it from X.

            Replaying the corpus page by page rather than handing the loop one
            flat list keeps the early-stop, the safety cap and the per-page
            sentiment batching below completely untouched -- the same tweets
            produce the same tickers, so a replayed scan stops exactly where
            the original did.
            """
            idx = pages - 1  # `pages` is incremented before this is called
            if _cached_pages is not None:
                if idx < len(_cached_pages):
                    return {
                        "success": True,
                        "tweets": _cached_pages[idx],
                        # Synthetic: on a hit X is never called, so this token
                        # only has to be truthy enough to drive the loop.
                        "next_token": "cached" if idx + 1 < len(_cached_pages) else None,
                    }
                return {"success": True, "tweets": [], "next_token": None}

            if _fetcher is not None:
                res = _fetcher.next_page()
                # The outer loop only asks "is there more work?", so hand it a
                # sentinel rather than a token belonging to one specific basket.
                return dict(res, next_token="more" if res.get("has_more") else None)

            res = search_x_tweets_page(
                query=query, max_results=PER_PAGE, timeframe="24h", next_token=token
            )
            if res.get("success"):
                # Record what we bought so it can be stored once the scan ends.
                _fetched_pages.append(res.get("tweets") or [])
            return res

        _fetcher = None
        # NOT on a corpus-cache hit. _next_page returns cached pages and never
        # consults the fetcher, but prefetch_first_pass() goes to the network
        # the moment it is constructed -- so a replayed scan was buying an
        # entire sector (up to 700 posts in finance) and throwing it away, with
        # posts_billed still reporting 0 because nothing was delivered. The
        # whole point of the corpus cache is that a repeat scan costs nothing.
        if _baskets and _cached_pages is None:
            _fetcher = sector_query.BasketFetcher(
                _baskets,
                fetch=lambda q, n, tok: search_x_tweets_page(
                    query=q, max_results=n, timeframe="24h", next_token=tok),
                per_page=BASKET_PER_PAGE,
                # Enough for one full pass over every basket plus some depth.
                # The default of 20 could not cover finance (27 baskets),
                # consumer (22) or healthcare (20) even once, so their tail
                # tickers were unreachable regardless of budget.
                max_requests=len(_baskets) + 6,
            )
            # Baskets are independent queries, so a wide sector's first pass
            # goes out concurrently. Finance has 27 baskets; serialising them
            # is ~30s of a paid scan spent inside the window where a user
            # re-click aborts the run. No-op at 3 baskets or fewer.
            _fetcher.prefetch_first_pass(post_budget=SAFETY_CAP_TWEETS)
            # The fetcher owns the pages it bought; _fetched_pages is what the
            # corpus write and posts_billed read, so point them at the same list.
            _fetched_pages = _fetcher.pages

        with st.spinner(f"Searching {sector} stocks on X..."):
            pages = 0
            while total_posts < SAFETY_CAP_TWEETS:
                pages += 1
                # Page fetch (always 100 max)
                progress_bar.progress(15); status_text.markdown(f"**📡 Scanning X for {sector} momentum...**")
                res = _next_page(next_token)
                if not res.get('success'):
                    _api_err = res.get('error') or 'X API request failed'
                    _x_api_error = _api_err
                    st.markdown(
                        f"""
                        <div style="border:1px solid rgba(245,158,11,.28);border-radius:14px;padding:18px 20px;
                          background:rgba(245,158,11,.05);margin:0.5rem 0;text-align:center;">
                          <div style="font-size:1.2rem;margin-bottom:6px;">📡</div>
                          <div style="font-weight:700;color:rgba(251,191,36,.95);font-size:0.95rem;margin-bottom:4px;">X data feed unavailable</div>
                          <div style="color:rgba(148,163,184,.75);font-size:0.82rem;">{_api_err[:200]}</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                    break

                page_tweets = res.get('tweets') or []
                next_token = res.get('next_token')
                progress_bar.progress(45); status_text.markdown("**🔍 Filtering noise, validating tickers...**")

                if not page_tweets:
                    # In BASKET mode an empty page is normal, not the end.
                    # sector_universe sorts by dollar volume, so baskets 2..N
                    # hold only quiet names -- returning zero is the expected
                    # case for them. Breaking here would abandon every basket
                    # after the first quiet one, which silently defeats the whole
                    # point of covering the sector. Keep going while the fetcher
                    # still has work.
                    #
                    # An empty page does not increment total_posts, so `continue`
                    # relies entirely on BasketFetcher's own max_requests bound
                    # to terminate -- the `while total_posts < SAFETY_CAP_TWEETS`
                    # guard above cannot stop this path on its own.
                    if next_token:
                        continue
                    break

                # The sector-relevance post-filter is gone with topic mode. It
                # substring-matched tweets against a sector's TOPIC WORDS, and a
                # post reading "$NEE up 3% on the open" contains none of them --
                # it would have discarded exactly what we asked and paid for.
                # Relevance is now guaranteed by the query itself: every post
                # matched a cashtag we named.

                # Enforce safety cap at processed-post level
                # DROP POSTS ALREADY SEEN IN ANOTHER BASKET.
                #
                # A single topic query paginating with next_token could never
                # return the same post twice. Baskets can: a post naming $NVDA
                # (basket 1) and a quieter name (basket 3) matches both queries
                # and comes back from both requests. Without this, that post
                # increments ticker_data[t]['mentions'] twice and contributes
                # its sentiment twice -- and mentions is the sort key that
                # decides which ten tickers the user sees.
                #
                # The duplicate is still BILLED; X charged for both copies. This
                # only stops it being counted twice.
                _fresh = []
                for tw in page_tweets:
                    tid = tw.get("id")
                    if tid is not None and tid in _seen_post_ids:
                        continue
                    if tid is not None:
                        _seen_post_ids.add(tid)
                    _fresh.append(tw)
                if len(_fresh) != len(page_tweets):
                    logger.info("deduped %d post(s) already seen in another basket",
                                len(page_tweets) - len(_fresh))
                page_tweets = _fresh
                if not page_tweets:
                    if next_token:
                        continue
                    break

                remaining = SAFETY_CAP_TWEETS - total_posts
                if remaining <= 0:
                    break
                if len(page_tweets) > remaining:
                    page_tweets = page_tweets[:remaining]

                total_posts += len(page_tweets)

                # Ticker extraction first (regex, cheap), then ONE batched
                # sentiment call for the whole page. This used to score tweets
                # one at a time -- up to ~500 sequential forward passes per scan
                # -- which is what killed the 2026-08-01 healthcare scan mid-loop
                # and spent a credit for nothing. Batching per page rather than
                # per scan keeps peak memory bounded and preserves the
                # between-page progress updates below.
                # EXTRACTION ONLY. No sentiment here.
                #
                # Ranking is by mention count and validation is a ticker_master
                # lookup -- neither needs FinBERT. Scoring inside this loop meant
                # scoring every ticker-bearing post to display ten tickers:
                # measured at 119 of 138 posts in utilities, 86 of 98 in tech,
                # about 17 and 12 seconds of inference respectively. Deferring it
                # until the top ten is known cuts that by 42-45% and removes it
                # from the fetch path entirely.
                for tweet in page_tweets:
                    text = tweet.get('text', '')
                    # _detailed reports WHERE each symbol came from -- $CAT is
                    # unambiguous, bare CAT is also an English word. `legacy` is
                    # the exact list extract_tickers has always returned.
                    _d = extract_tickers_detailed(text)
                    _tally.record(_d["cashtag"], _d["bare"],
                                  _d["cashtag_counts"], _d["bare_counts"])
                    tickers = _d["legacy"]
                    if not tickers:
                        continue
                    _scored_corpus.append((text, tickers, _d["cashtag"]))
                    for ticker in tickers:
                        ticker_data[ticker]['mentions'] += 1
                        if len(ticker_data[ticker]['sample_tweets']) < 3:
                            short_text = text[:150] + "..." if len(text) > 150 else text
                            ticker_data[ticker]['sample_tweets'].append(short_text)

                # After each page, try to validate enough tickers.
                progress_bar.progress(70); status_text.markdown("**⚡ Building your shortlist...**")
                _try_validate_from_current_ranking()

                logger.info(
                    "📄 Discovery pagination pages=%s posts=%s validated=%s first_pass=%s",
                    pages,
                    total_posts,
                    len(validated_set),
                    _fetcher.first_pass_done if _fetcher else True,
                )

                # STOP ONLY AFTER EVERY BASKET HAS BEEN SAMPLED.
                #
                # Baskets are ordered by dollar volume, and dollar volume does
                # not predict chatter. Measured on utilities: four of the six
                # most-discussed tickers sat outside basket 1 -- $AVA (14
                # mentions, basket 2) and $AWX (12, basket 4) would never have
                # been seen by a scan that stopped once basket 1 filled ten
                # slots. Stopping on the ticker target alone returns exactly the
                # names that are NOT unusual.
                _first_pass = _fetcher.first_pass_done if _fetcher else True
                if _first_pass and len(validated_set) >= TARGET_VALIDATED:
                    break

                if not next_token:
                    break

        progress_bar.progress(100); status_text.empty(); progress_bar.empty()

        logger.info(f"🎯 Posts processed (capped): {total_posts}")

        # Store what we bought, so the next scan of this sector is free.
        #
        # Only on a clean run: a corpus truncated by an X failure would be
        # frozen in and served to everyone for the next six hours, turning one
        # transient error into a sustained bad result. A genuinely EMPTY result
        # is stored, though -- "this sector had no chatter" is a real answer
        # that cost real money to learn, and without a negative entry the next
        # user pays to learn it again.
        if _cached is None and not _x_api_error:
            corpus_cache.put(
                "sector", sector, 24, query,
                tweets=[t for pg in _fetched_pages for t in pg],
                pages_fetched=len(_fetched_pages),
                stop_reason=(
                    "validated_target" if len(validated_set) >= TARGET_VALIDATED
                    else "safety_cap" if total_posts >= SAFETY_CAP_TWEETS
                    else "exhausted"
                ),
            )

        # Age must survive the rerun that renders the table below.
        st.session_state.scan_corpus_age_s = _cached["age_s"] if _cached else 0.0

        if total_posts == 0:
            _record_metrics([])
            if _x_api_error:
                # Upstream failure, zero posts: the user paid and got nothing.
                if refund_credit("scan", _credit.event_id, f"x api: {_x_api_error[:120]}"):
                    st.info("Your scan credit was not used.")
            else:
                # A genuinely empty result is an answer, not a failure -- the
                # scan ran and the sector simply had no chatter. Still charged.
                st.warning("No posts returned from X for this query.")
            st.stop()

        # (status message removed - results table speaks for itself)

        # ── Sentiment, once, on the shortlist only ───────────────────────────
        #
        # Ranking never used sentiment: it is mention count, and validation is a
        # table lookup. So the ten tickers are already decided here, and only
        # their posts need scoring. Measured: 119 of 138 posts scored before,
        # 69 after (utilities); 86 of 98 before, 47 after (tech) -- 42-45% less
        # inference for an identical result set.
        _shortlist = [t for t, _ in sorted(
            ((t, d['mentions']) for t, d in ticker_data.items() if t in validated_set),
            key=lambda kv: -kv[1],
        )[:TARGET_VALIDATED]]
        _shortlist_set = set(_shortlist)

        _relevant = [(text, tks, cash) for text, tks, cash in _scored_corpus
                     if any(t in _shortlist_set for t in tks)]
        if _relevant:
            progress_bar.progress(85)
            status_text.markdown("**🧠 Reading the mood on your shortlist...**")
        logger.info("🧠 scoring %d of %d ticker-bearing posts (shortlist of %d)",
                    len(_relevant), len(_scored_corpus), len(_shortlist))

        try:
            _sent = analyze_sentiment_batch([t for t, _, _ in _relevant]) if _relevant else []
        finally:
            # Re-armed at 85% above; clear it either way or the results table
            # renders beneath a progress bar frozen mid-scan, which is exactly
            # the "nothing is happening" signal that makes users re-click.
            progress_bar.empty()
            status_text.empty()

        # SINGLE-TICKER POSTS ONLY DRIVE DIRECTION.
        #
        # FinBERT scores the whole post and is never told which ticker the
        # question is about, so a post naming two tickers gives both the same
        # verdict -- "Virginia Gov's skepticism threatens $NEE's $D" is one
        # sentiment stamped on two companies whose fortunes the post treats as
        # opposed. Measured at 13% of ticker-bearing posts. They still count as
        # ATTENTION, which is what the scan is actually for; they just do not
        # get a vote on direction.
        for (text, tks, cash), res in zip(_relevant, _sent):
            # Attribute only when the post has exactly ONE unambiguous subject.
            #
            # Gating on the full `legacy` list was far too aggressive: it
            # carries up to 5 BARE uppercase words on top of the cashtags, so
            # "one shortlist cashtag + any capitalised token" counted as
            # multi-ticker. Measured on the cached corpora that excluded 63% of
            # utilities and 47% of tech posts -- 4x the 13% this rule is meant
            # to catch -- and left 7 of 10 rows unscored.
            #
            # A cashtag is an explicit claim about a security; a bare word is a
            # guess. Exactly one distinct cashtag means exactly one subject.
            uniq_cash = {c for c in cash}
            if len(uniq_cash) != 1:
                continue
            ticker = next(iter(uniq_cash))
            if ticker in _shortlist_set:
                ticker_data[ticker]['margins'].append(float(res.get('margin') or 0.0))

        # Convert to final DataFrame
        if ticker_data:
            rows = []
            for ticker, info in ticker_data.items():
                margins = info.get('margins') or []
                n = len(margins)
                mean_margin = (sum(margins) / n) if n else 0.0

                # EVIDENCE FLOOR. The old pill was a plurality vote over
                # discrete labels, which (a) tied on corpus order -- $ET came
                # back {Neutral: 2, Bearish: 2} and the winner was whichever
                # post arrived first -- and (b) rendered the same bold badge for
                # one post as for fourteen, which was 52% of tickers. A mean
                # margin cannot tie, and the floor stops a single post being
                # presented as a verdict.
                if n == 0:
                    overall_sentiment = 'Unscored'
                elif n == 1:
                    overall_sentiment = 'Single mention'
                elif n == 2:
                    overall_sentiment = 'Limited signal'
                elif mean_margin >= SENTIMENT_MARGIN:
                    overall_sentiment = 'Bullish'
                elif mean_margin <= -SENTIMENT_MARGIN:
                    overall_sentiment = 'Bearish'
                else:
                    overall_sentiment = 'Neutral'

                rows.append({
                    'Ticker': ticker,
                    'Mentions': info['mentions'],
                    'Avg Sentiment Score': round(mean_margin, 3),
                    'Evidence': n,
                    'Overall Sentiment': overall_sentiment,
                    'Sample Tweets': ' | '.join(info['sample_tweets']),
                    'Company Name': company_by_ticker.get(ticker, 'N/A'),
                    'Valid': ticker in validated_set,
                })

            df = pd.DataFrame(rows).sort_values('Mentions', ascending=False)

            # Final output: Top 10 validated tickers only
            df_valid = df[df['Valid'] == True].copy()
            df_valid = df_valid.drop(columns=['Valid'])
            df_valid = df_valid.head(TARGET_VALIDATED)

            # Classification needs the FINAL top-10: whether a post
            # "contributed" depends on which tickers actually got displayed,
            # which is not known until here.
            _record_metrics([str(t) for t in df_valid["Ticker"].tolist()])

            # Every valid ticker, not just the displayed ten. These are the
            # observations that will eventually answer whether this sentiment
            # measurement predicts anything -- Deep Analyze has run four times
            # in its history and cannot supply them, while one scan yields ~10
            # for posts already bought. Paired with the price at scan time,
            # because neither the posts nor the price can be recovered later.
            try:
                from utils import scan_log
                from utils.sentiment import MODEL_NAME as _mn
                scan_log.record(
                    sector, rows,
                    [str(t) for t in df_valid["Ticker"].tolist()],
                    event_id=getattr(_credit, "event_id", None),
                    corpus_key=corpus_cache.make_key("sector", sector, 24, query),
                    model=_mn,
                )
            except Exception:
                logger.warning("scan_log call failed", exc_info=True)

            st.session_state.df_valid = df_valid
            st.session_state.df_unvalidated = None  # not shown
            st.session_state.selected_sector = sector
            st.session_state.selected_ticker = None
            st.session_state.deep_analysis_results = None

            # Results are durable in session_state: the scan ran and produced an
            # answer. An empty answer is still an answer -- the sector genuinely
            # had no validated chatter -- so it is charged, as before.
            _delivered = True

            if len(df_valid) == 0:
                st.warning("⚠️ No validated stock tickers found. Try a different sector/time window.")
            else:
                pass  # (status message removed)
        else:
            # Posts were fetched and scored, they just contained no tickers.
            # Work was done and an answer given, so this stays charged.
            #
            # Metrics MUST be recorded here. This is a 100%-waste scan -- every
            # post bought, nothing usable out -- and it is the single most
            # informative data point about query quality. Recording only scans
            # that produced a table would bias the waste number downward exactly
            # where it matters most.
            _record_metrics([])
            _delivered = True
            st.warning("⚠️ No stock tickers found in the posts. Try a different search query.")

        # Note: detailed pagination + stop reasons are logged by utils.deep_analysis.search_x_tweets

    except KeyError:
        refund_credit("scan", _credit.event_id, "missing API credentials")
        st.markdown(
            """
            <div style="border:1px solid rgba(239,68,68,.30);border-radius:16px;padding:24px;
              background:rgba(239,68,68,.05);margin:1rem 0;text-align:center;">
              <div style="font-size:1.5rem;margin-bottom:8px;">🔑</div>
              <div style="font-weight:700;color:rgba(248,113,113,.95);font-size:1.0rem;margin-bottom:4px;">Configuration error</div>
              <div style="color:rgba(148,163,184,.80);font-size:0.88rem;">Missing API credentials. Contact support if this keeps happening.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    except requests.exceptions.RequestException:
        refund_credit("scan", _credit.event_id, "network failure reaching X")
        st.markdown(
            """
            <div style="border:1px solid rgba(245,158,11,.28);border-radius:16px;padding:24px;
              background:rgba(245,158,11,.05);margin:1rem 0;text-align:center;">
              <div style="font-size:1.5rem;margin-bottom:8px;">📡</div>
              <div style="font-weight:700;color:rgba(251,191,36,.95);font-size:1.0rem;margin-bottom:4px;">Connection issue</div>
              <div style="color:rgba(148,163,184,.80);font-size:0.88rem;">Couldn't reach the data source. Check your connection and try again.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

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
        try:
            _record_metrics([])
        except Exception:
            pass

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

# KPI strip (UI only)
if st.session_state.df_valid is not None:
    try:
        dfv = st.session_state.df_valid
        dfn = st.session_state.df_unvalidated

        total_valid = int(len(dfv)) if dfv is not None else 0
        total_other = int(len(dfn)) if dfn is not None else 0
        total_unique = total_valid + total_other
        # Average only over rows that CLEARED the evidence floor.
        #
        # Averaging everything let this headline assert "Bullish (0.20)" over a
        # table where most rows say "Single mention", and it averaged in the 0.0
        # of every Unscored ticker -- dragging the number toward Neutral for a
        # reason that has nothing to do with sentiment.
        _dfv_scored = None
        if dfv is not None and "Avg Sentiment Score" in dfv.columns and len(dfv) > 0:
            if "Evidence" in dfv.columns:
                _dfv_scored = dfv[dfv["Evidence"] >= 3]
            else:
                _dfv_scored = dfv
        avg_sent = (float(_dfv_scored["Avg Sentiment Score"].mean())
                    if _dfv_scored is not None and len(_dfv_scored) > 0 else 0.0)
        _n_scored = 0 if _dfv_scored is None else len(_dfv_scored)

        if _n_scored == 0:
            sent_label = "Not enough signal"
            sent_color = "rgba(148,163,184,.75)"
            sent_border = "rgba(148,163,184,.20)"
        elif avg_sent >= SENTIMENT_MARGIN:
            sent_label = f"Bullish ({avg_sent:.2f})"
            sent_color = "rgba(56,189,248,.95)"
            sent_border = "rgba(56,189,248,.28)"
        elif avg_sent <= -SENTIMENT_MARGIN:
            sent_label = f"Bearish ({avg_sent:.2f})"
            sent_color = "rgba(239,68,68,.90)"
            sent_border = "rgba(239,68,68,.25)"
        else:
            sent_label = f"Neutral ({avg_sent:.2f})"
            sent_color = "rgba(148,163,184,.90)"
            sent_border = "rgba(148,163,184,.22)"

        _card_style = (
            "border-radius:14px;padding:14px 16px;height:72px;"
            "display:flex;flex-direction:column;justify-content:center;"
            "background:linear-gradient(180deg,rgba(15,23,42,.90),rgba(15,23,42,.72));"
        )
        _label_style = "color:rgba(148,163,184,.75);font-size:0.75rem;font-weight:600;letter-spacing:0.04em;text-transform:uppercase;margin-bottom:5px;"
        _value_style = "font-size:1.30rem;font-weight:800;letter-spacing:-0.02em;color:rgba(248,250,252,.98);"

        # Use st.columns for guaranteed even width (Streamlit grid = truly equal)
        kc1, kc2 = st.columns(2)
        kc1.markdown(
            f'<div style="{_card_style}border:1px solid rgba(148,163,184,.18);">'
            f'<div style="{_label_style}">Stocks found</div>'
            f'<div style="{_value_style}">{total_valid}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        kc2.markdown(
            f'<div style="{_card_style}border:1px solid {sent_border};">'
            f'<div style="{_label_style}">Avg Sentiment</div>'
            f'<div style="font-size:1.10rem;font-weight:750;color:{sent_color};">{sent_label}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.markdown("<div style='height:0.25rem'></div>", unsafe_allow_html=True)
    except Exception:
        # Never let UI extras break the page
        pass

def _render_deep_panel(ticker, sector, deep_results):
    """Render the deep analysis panel inline below a ticker row.

    Adjudicates from the SAME evidence ledger as pages/Deep_Analysis.py. This
    path used to run generate_ai_summary instead, so clicking Deep Analyze on a
    scan row and typing the same ticker on the other page produced two
    different products at one credit price -- and only the typed path ever
    reached verdict_log, making that table a biased sample of one entry route.
    """
    from utils.deep_analysis import ANALYSIS_PROMPTS
    from utils.ui import render_evidence_check

    _sink = st.session_state.get("deep_analysis_sink") or {}
    _v = None
    try:
        if _sink.get("ticker_corpus") is not None:
            from utils.evidence import build_ledger
            from utils.sentiment import analyze_sentiment_batch as _sc
            from utils.verdict import adjudicate as _adj

            _al = _sink.get("alias") or ""
            _led = []
            for _k, _ch in (("ticker_corpus", "social_base"),
                            ("influencer_corpus", "newswire")):
                _ps = _sink.get(_k) or []
                if not _ps:
                    continue
                _ds = _sc([(x.get("text") or "")[:512] for x in _ps])
                _by = {str(x["id"]): d for x, d in zip(_ps, _ds)
                       if x.get("id") is not None}
                _seen = {r.post_id for r in _led}
                _led += [r for r in build_ledger(_ps, ticker, alias=_al,
                                                 channel=_ch, scores=_by)
                         if r.post_id not in _seen]
            try:
                from utils import seed as _sd_mod
                _sp = _sd_mod.fetch(ticker, sector,
                                    exclude_ids={r.post_id for r in _led})
                if _sp:
                    _dd = _sc([(x.get("text") or "")[:512] for x in _sp])
                    _bi = {str(x["id"]): d for x, d in zip(_sp, _dd)
                           if x.get("id") is not None}
                    _led += build_ledger(_sp, ticker, alias=_al,
                                         channel="discovery_seed", scores=_bi)
            except Exception:
                logger.warning("discovery: seed reuse failed", exc_info=True)
    except Exception:
        logger.warning("discovery: ledger build failed; using legacy path",
                       exc_info=True)
        _led = []

    _price, _proj, _hold, _pts = "Unavailable", "Unavailable", "Unavailable", 0
    _prices = _vols = None
    _last = None
    _proj_r: dict = {}
    try:
        _sd = get_stock_data(ticker)
        if _sd.get("error") is None and _sd.get("prices"):
            _prices = _sd["prices"]
            _vols = _sd.get("volumes") or None
            _pts = len(_prices)
            _lp = _prices[-1]
            if isinstance(_lp, (int, float)):
                _price = f"${_lp:.2f}"
                _last = float(_lp)
    except Exception:
        logger.warning("discovery: price fetch failed", exc_info=True)

    if _led:
        try:
            _v = _adj(_led, _prices, _vols)
        except Exception:
            logger.warning("discovery: adjudication failed", exc_info=True)
            _v = None

    if _v is not None:
        _ai = {"recommendation": _v.recommendation, "confidence": _v.confidence,
               "avg_sentiment": _v.social.direction, "rationale": [_v.reason]}
    else:
        _ai = generate_ai_summary(deep_results)

    try:
        if _prices:
            # Identical treatment to pages/Deep_Analysis.py. This call site was
            # missed once already, and the result was the same ticker on the
            # same day showing one range here and a different one there, with
            # this page still printing the bare "N days" hold string that was
            # removed as misleading -- the median day-to-+5% among WINNING paths
            # only, hit rate hidden -- and applying the sentiment tilt with no
            # quality gate at all.
            # Buy only, per the design: a tilted band under an Avoid reads as
            # a short-side price target the system has no basis for.
            _dq_ok = ((_v.quality.tier in ("moderate", "high")
                       and _v.recommendation == "Buy") if _v is not None else
                      len({str(_t) for _r in deep_results.values()
                           for _t in (_r.get("tweet_ids") or [])}) >= 8)
            _proj_r = simple_projection(_prices, _ai["avg_sentiment"], days=30,
                                        quality_ok=_dq_ok)
            if _proj_r.get("error") is None:
                _proj = (f"{_proj_r['scenario_bear']:.1f}% to "
                         f"{_proj_r['scenario_bull']:.1f}%")
                _lo, _hi = _proj_r.get("review_window_days", (14, 28))
                _hold = f"{_lo // 7}–{_hi // 7} weeks"
    except Exception:
        logger.warning("discovery: projection failed", exc_info=True)

    try:
        _uids = {tid for _r in deep_results.values() for tid in (_r.get("tweet_ids") or [])}
        _mentions_ct = len(_uids)
    except Exception:
        _mentions_ct = 0

    _rec = _ai.get("recommendation", "—")
    _conf = _ai.get("confidence", "—")
    _avg_sent = float(_ai.get("avg_sentiment", 0.0))
    _rec_color = "rgba(56,189,248,.95)" if "buy" in _rec.lower() else "rgba(239,68,68,.90)" if "avoid" in _rec.lower() else "rgba(245,158,11,.90)"
    _conf_color = "rgba(56,189,248,.90)" if _conf.lower()=="high" else "rgba(245,158,11,.90)" if _conf.lower()=="moderate" else "rgba(148,163,184,.80)"
    _sent_color = "rgba(56,189,248,.95)" if _avg_sent>=0.10 else "rgba(239,68,68,.88)" if _avg_sent<=-0.10 else "rgba(148,163,184,.85)"
    _sent_lbl = f"Bullish ({_avg_sent:+.2f})" if _avg_sent>=0.10 else f"Bearish ({_avg_sent:+.2f})" if _avg_sent<=-0.10 else f"Neutral ({_avg_sent:+.2f})"
    _sector_lbl = (" · "+sector.title()) if sector and sector.lower() not in ("unknown","") else ""
    _rec_sub = {"buy":"Strong upside signal","watch":"Hold — monitor closely","avoid":"Risk outweighs reward"}.get(_rec.lower(),"")
    _conf_sub = {"high":"Strong data backing","moderate":"Reasonable evidence","low":"Thin data — use caution"}.get(_conf.lower(),"")
    _bar_pct = min(100, int(abs(_avg_sent)*250 + {"high":30,"moderate":15,"low":0}.get(_conf.lower(),0)))
    _conf_bar = {"high":90,"moderate":55,"low":25}.get(_conf.lower(),30)

    def _bar(pct, color):
        return f'<div style="width:100%;height:4px;background:rgba(148,163,184,.12);border-radius:999px;margin-top:6px;"><div style="width:{pct}%;height:4px;background:{color};border-radius:999px;"></div></div>'

    _mc = "border-radius:12px;padding:14px 16px 12px 16px;background:rgba(15,23,42,.75);flex:1;min-width:0;display:flex;flex-direction:column;gap:4px;"
    _rationale_html = "".join(f'<li style="margin-bottom:5px;color:rgba(229,231,235,.85);font-size:0.88rem;line-height:1.45;">{b}</li>' for b in _ai.get("rationale",[]))

    _fc = "border-radius:10px;padding:10px 14px;background:rgba(15,23,42,.55);border:1px solid rgba(148,163,184,.12);flex:1;"
    _fl = "font-size:0.68rem;font-weight:700;letter-spacing:0.05em;text-transform:uppercase;color:rgba(148,163,184,.55);margin-bottom:3px;"
    _fv = "font-size:1.00rem;font-weight:800;color:rgba(248,250,252,.92);"
    _price_row = f'<div style="display:flex;gap:8px;margin-bottom:14px;"><div style="{_fc}"><div style="{_fl}">Last Price</div><div style="{_fv}">{_price}</div></div><div style="{_fc}"><div style="{_fl}">Proj. Gain 30d</div><div style="{_fv}">{_proj}</div></div><div style="{_fc}"><div style="{_fl}">Hold Period</div><div style="{_fv}">{_hold}</div></div></div>' if _price != "Unavailable" or _proj != "Unavailable" else ""

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
            f'<td style="padding:9px 10px;color:rgba(229,231,235,.90);font-size:0.80rem;">{_pn}</td>'
            f'<td style="padding:9px 10px;color:rgba(148,163,184,.70);font-size:0.80rem;">{_tf}</td>'
            f'<td style="padding:9px 10px;text-align:center;color:rgba(148,163,184,.80);font-size:0.80rem;">{_ev}</td>'
            f'<td style="padding:9px 10px;color:rgba(148,163,184,.80);font-size:0.80rem;">{_st2}</td>'
            f'<td style="padding:9px 10px;font-size:0.80rem;font-weight:700;color:{_tc};">{_tl}</td>'
            f'</tr>'
        )
    _cov_table = (
        f'<table style="width:100%;border-collapse:collapse;background:rgba(15,23,42,.60);border-radius:10px;overflow:hidden;margin-bottom:16px;">'
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
            f'<div style="display:flex;gap:8px;margin:8px 0 6px 0;">'
            f'<div style="flex:1;background:rgba(15,23,42,.55);border-radius:8px;padding:8px 10px;border:1px solid rgba(148,163,184,.10);">'
            f'<div style="font-size:0.65rem;color:rgba(148,163,184,.55);text-transform:uppercase;letter-spacing:0.05em;">Sentiment Score</div>'
            f'<div style="font-size:0.92rem;font-weight:700;color:rgba(248,250,252,.90);">{float(_sc):.3f}</div></div>'
            f'<div style="flex:1;background:rgba(15,23,42,.55);border-radius:8px;padding:8px 10px;border:1px solid rgba(148,163,184,.10);">'
            f'<div style="font-size:0.65rem;color:rgba(148,163,184,.55);text-transform:uppercase;letter-spacing:0.05em;">Overall</div>'
            f'<div style="font-size:0.92rem;font-weight:700;color:{_tc};">{_tl}</div></div>'
            f'<div style="flex:1;background:rgba(15,23,42,.55);border-radius:8px;padding:8px 10px;border:1px solid rgba(148,163,184,.10);">'
            f'<div style="font-size:0.65rem;color:rgba(148,163,184,.55);text-transform:uppercase;letter-spacing:0.05em;">Mentions</div>'
            f'<div style="font-size:0.92rem;font-weight:700;color:rgba(248,250,252,.90);">{_ev}</div></div>'
            f'</div>'
        )

        _themes_html = ""
        if _themes:
            _chips = "".join(f'<span style="display:inline-block;background:rgba(56,189,248,.10);border:1px solid rgba(56,189,248,.20);border-radius:999px;padding:2px 9px;font-size:0.70rem;color:rgba(148,163,184,.85);margin:2px 3px 2px 0;">{t}</span>' for t in _themes)
            _themes_html = f'<div style="margin:6px 0 8px 0;"><span style="font-size:0.72rem;font-weight:700;color:rgba(148,163,184,.55);text-transform:uppercase;letter-spacing:0.05em;">Themes: </span>{_chips}</div>'

        _tweets_html = ""
        if _samples:
            _tweet_items = "".join(f'<div style="border-left:2px solid rgba(56,189,248,.25);padding:5px 10px;margin-bottom:6px;color:rgba(229,231,235,.75);font-size:0.78rem;line-height:1.45;font-style:italic;">{i}. {t}</div>' for i, t in enumerate(_samples, 1))
            _tweets_html = f'<div style="margin-top:6px;"><div style="font-size:0.72rem;font-weight:700;color:rgba(148,163,184,.55);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px;">Sample posts:</div>{_tweet_items}</div>'

        _insights_html = f'<div style="font-size:0.78rem;color:rgba(148,163,184,.65);margin-bottom:4px;"><b>Insights:</b> {_ins}</div>' if _ins else ""
        _detail_sections += (
            f'<div style="border-top:1px solid rgba(148,163,184,.10);padding:12px 0;">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;">'
            f'<span style="font-size:0.84rem;font-weight:700;color:rgba(229,231,235,.90);">{_pn}</span>'
            f'<span style="font-size:0.72rem;color:rgba(148,163,184,.55);">{_tf}</span>'
            f'</div>'
            f'{_metrics}'
            f'{_insights_html}'
            f'{_themes_html}'
            f'{_tweets_html}'
            f'</div>'
        )

    _panel_html = f"""<div style="
      width:100%;box-sizing:border-box;
      background:rgba(2,6,23,0.97);
      border:1px solid rgba(56,189,248,.25);
      border-radius:16px;
      box-shadow:0 8px 40px rgba(0,0,0,.55);
      overflow:hidden;
      font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
    ">
      <div style="padding:16px 20px 12px 20px;border-bottom:1px solid rgba(56,189,248,.15);background:linear-gradient(180deg,rgba(56,189,248,.07),rgba(2,6,23,0));display:flex;align-items:center;justify-content:space-between;gap:12px;">
        <div>
          <div style="font-size:0.68rem;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:rgba(56,189,248,.75);">Deep Analysis{_sector_lbl}</div>
          <div style="font-size:1.40rem;font-weight:850;letter-spacing:-0.02em;color:rgba(248,250,252,.98);">{ticker}</div>
        </div>
        <div style="text-align:right;">
          <div style="font-size:0.68rem;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:rgba(148,163,184,.50);">Signal</div>
          <div style="font-size:1.25rem;font-weight:850;color:{_rec_color};">{_rec}</div>
          <div style="font-size:0.72rem;color:rgba(148,163,184,.60);">Confidence: {_conf}</div>
        </div>
      </div>
      <div style="padding:16px 20px 20px 20px;">
        <div style="display:flex;gap:8px;margin-bottom:14px;">
          <div style="{_mc}border:1px solid {_rec_color.replace('.95',',.28').replace('.90',',.25')};"><div style="font-size:0.68rem;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:rgba(148,163,184,.55);">Recommendation</div><div style="font-size:1.05rem;font-weight:850;color:{_rec_color};">{_rec}</div><div style="font-size:0.72rem;color:rgba(148,163,184,.55);">{_rec_sub}</div>{_bar(_bar_pct,_rec_color)}</div>
          <div style="{_mc}border:1px solid rgba(148,163,184,.15);"><div style="font-size:0.68rem;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:rgba(148,163,184,.55);">Confidence</div><div style="font-size:1.05rem;font-weight:850;color:{_conf_color};">{_conf}</div><div style="font-size:0.72rem;color:rgba(148,163,184,.55);">{_conf_sub}</div>{_bar(_conf_bar,_conf_color)}</div>
          <div style="{_mc}border:1px solid rgba(148,163,184,.15);"><div style="font-size:0.68rem;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:rgba(148,163,184,.55);">Market Mood</div><div style="font-size:1.05rem;font-weight:850;color:{_sent_color};">{_sent_lbl.split(" ")[0]}</div><div style="font-size:0.72rem;color:rgba(148,163,184,.55);">Score {_avg_sent:+.3f}</div>{_bar(min(100,int(abs(_avg_sent)*280)),_sent_color)}</div>
        </div>
        {_price_row}
        <div style="color:rgba(148,163,184,.45);font-size:0.72rem;margin-bottom:14px;">{_mentions_ct} posts analysed · {_pts} price points</div>
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

    components.html(_panel_html, height=750, scrolling=True)

    # The pillar readout, and the verdict record. This path previously wrote
    # NEITHER: a scan-row Deep Analyze produced no evidence check and no
    # verdict_log row, so the table built to measure this product only ever saw
    # users who typed the ticker on the other page.
    if _v is not None:
        try:
            render_evidence_check(_v, ticker)
        except Exception:
            logger.warning("discovery: evidence check render failed", exc_info=True)
        try:
            from utils import verdict_log
            from utils.sentiment import MODEL_NAME as _mn
            verdict_log.record(
                ticker, _v.recommendation,
                sector=sector or None, confidence=_v.confidence,
                avg_sentiment=_v.social.direction,
                red_flag_rate=_v.risk.soft_rate,
                disagreement=1.0 if _v.social.conflict else 0.0,
                total_mentions=_mentions_ct,
                price_at_verdict=_last,
                projected_p10=(_proj_r or {}).get("gain_p10"),
                projected_p90=(_proj_r or {}).get("gain_p90"),
                suggested_hold_days=(_proj_r or {}).get("suggested_hold_days"),
                success_rate=(_proj_r or {}).get("success_rate"),
                model=f"{_mn}|ledger|discovery",
            )
        except Exception:
            logger.warning("discovery: verdict_log write failed", exc_info=True)

# ── Results table ──
if st.session_state.df_valid is not None:
    df_valid_display = st.session_state.df_valid.drop(columns=["Mentions", "Sample Tweets", "Evidence"], errors="ignore")

    if len(df_valid_display) > 0:
        st.markdown(
            f'<div style="font-size:1.15rem;font-weight:800;letter-spacing:-0.01em;'
            f'color:rgba(229,231,235,.98);margin:0.35rem 0 0.65rem 0;">'
            f'{sector.title()} · {len(df_valid_display)} stocks</div>',
            unsafe_allow_html=True,
        )

        # Say how old the chatter is whenever it did not come from X just now.
        # The page badges "Real-time social sentiment", and a corpus may be up
        # to six hours old -- unlabelled, that is a claim the product does not
        # keep. Stale-but-labelled is honest; stale-and-silent is the failure
        # mode this codebase keeps rediscovering.
        _age_s = float(st.session_state.get("scan_corpus_age_s") or 0.0)
        if _age_s >= 60:
            _mins = int(_age_s // 60)
            _age_label = f"{_mins // 60}h {_mins % 60}m" if _mins >= 60 else f"{_mins}m"
            st.markdown(
                f'<div style="color:rgba(148,163,184,.62);font-size:0.78rem;'
                f'margin:-0.35rem 0 0.75rem 0;">Market chatter from {_age_label} ago</div>',
                unsafe_allow_html=True,
            )

        # Bullish, Neutral, Bearish, then everything we could not assert.
        # Low-evidence rows sort last so the shortlist leads with the names the
        # scan can actually say something about.
        sentiment_order = {"bullish": 0, "neutral": 1, "bearish": 2,
                           "limited signal": 3, "single mention": 4, "unscored": 5}
        df_valid_display = df_valid_display.copy()
        df_valid_display["_sort"] = df_valid_display["Overall Sentiment"].str.lower().map(lambda x: sentiment_order.get(x, 3))
        df_valid_display = df_valid_display.sort_values("_sort").drop(columns=["_sort"])
        df_valid_display = df_valid_display.reset_index(drop=True)

        header_cols = st.columns([0.9, 1.5, 1.1, 0.95, 0.9])
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

        st.markdown(
            '<div style="display:flex;padding:0 0.85rem;margin-bottom:0.25rem;">'
            '</div>',
            unsafe_allow_html=True,
        )
        header_labels = ["Ticker", "Company", "Last Close", "Signal", "Action"]
        for col, label in zip(header_cols, header_labels):
            col.markdown(
                f'<span style="font-size:0.75rem;font-weight:700;letter-spacing:0.06em;'
                f'text-transform:uppercase;color:rgba(148,163,184,.70);">{label}</span>',
                unsafe_allow_html=True,
            )

        _top_signal_shown = False
        for _, row in df_valid_display.iterrows():
            ticker_symbol = row["Ticker"]
            company_name = row["Company Name"]
            overall_sentiment = row["Overall Sentiment"]
            last_close = last_close_map.get(str(ticker_symbol).upper())
            last_close_display = "N/A" if last_close is None else f"${float(last_close):.2f}"

            _is_selected = ticker_symbol == st.session_state.get("selected_ticker")
            if not _top_signal_shown and overall_sentiment.lower() == "bullish":
                st.markdown("<div class='ticker-row ticker-row--top-signal'>", unsafe_allow_html=True)
                _top_signal_shown = True
            else:
                st.markdown("<div class='ticker-row'>", unsafe_allow_html=True)
            col1, col2, col3, col4, col5 = st.columns(
                [0.9, 1.5, 1.1, 0.95, 0.9]
            )
            with col1:
                st.markdown(f"**{ticker_symbol}**")
            with col2:
                st.markdown(company_name)
            with col3:
                st.markdown(last_close_display)
            with col4:
                st.markdown(_sentiment_pill(overall_sentiment), unsafe_allow_html=True)
            with col5:
                if st.button("Deep Analyze", key=f"deep_analyze_{ticker_symbol}"):
                    # Open a request scope BEFORE charging. Without this the
                    # ContextVar still holds whatever id the last action on this
                    # page set, and consume_credit reuses a non-"-" value as its
                    # idempotency key -- a STALE key returns duplicate_request,
                    # which credits.py maps to ok=True, delivering the analysis
                    # with nothing debited. It also left every log line from this
                    # path stamped "-", so the ledger row pointed at no logs.
                    _drid = new_request_id()
                    _dcredit = consume_credit(
                        "deep_analyze",
                        {"ticker": ticker_symbol, "sector": sector, "page": "discovery"},
                    )
                    if not _dcredit.ok:
                        _upgrade_modal(f"Unlock the full analysis for {ticker_symbol}.", event_type="deep_analyze")
                        st.stop()

                    # Charged work: try/finally, not try/except. Streamlit's
                    # abort raises StopException/RerunException, which derive
                    # from BaseException and bypass every `except Exception`.
                    _ddelivered = False
                    try:
                        # Silently refresh token before long operation to prevent session expiry mid-run
                        refresh_session_if_needed()

                        st.session_state.selected_ticker = ticker_symbol
                        st.session_state.deep_analysis_results = None

                        _deep_error = None
                        _disc_prog = st.progress(0)
                        _disc_status = st.empty()
                        _disc_status.markdown(
                            f'<div style="color:rgba(229,231,235,.85);font-size:0.92rem;font-weight:600;">'
                            f'📡 Gathering market chatter for <b>{ticker_symbol}</b>...</div>',
                            unsafe_allow_html=True,
                        )
                        _disc_prog.progress(10)

                        import threading as _th, time as _tm

                        _disc_holder: dict = {}
                        _disc_done = _th.Event()
                        _disc_sector = st.session_state.get("selected_sector") or ""

                        def _disc_run():
                            # A new thread starts with an empty context, so the
                            # request id reverts to "-" without this.
                            _set_request_id(_drid)
                            try:
                                _disc_sink: dict = {}
                                _disc_holder["result"] = run_deep_analysis(
                                    ticker_symbol,
                                    _disc_sector,
                                    sink=_disc_sink,
                                )
                                _disc_holder["sink"] = _disc_sink
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
                        else:
                            st.session_state.deep_analysis_results = _disc_holder.get("result")
                            # The panel re-renders from session state on every
                            # rerun, so the corpora have to survive with it or
                            # the second render silently drops to the legacy
                            # adjudicator.
                            st.session_state.deep_analysis_sink = _disc_holder.get("sink") or {}
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
                            refund_credit("deep_analyze", _dcredit.event_id,
                                          "deep analysis did not complete")
                            complete_work(_dcredit.event_id, "failed",
                                          "aborted or errored")
            st.markdown("</div>", unsafe_allow_html=True)

            # ── Inline deep panel — renders immediately below this ticker's row ──
            if _is_selected and st.session_state.get("deep_analysis_results"):
                _render_deep_panel(
                    ticker_symbol,
                    st.session_state.get("selected_sector") or "",
                    st.session_state.deep_analysis_results,
                )

        st.caption(f"Click Deep Analyze on any ticker for catalysts, signals, and a Buy/Watch/Avoid recommendation.")
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

# Late-injected CSS to override Streamlit's selectbox dropdown (ensures readability on Windows)
st.markdown(
    """
    <style>
    body ul.st-cx.st-al.st-c1[data-testid="stSelectboxVirtualDropdown"],
    body ul.st-cx.st-al[data-testid="stSelectboxVirtualDropdown"],
    body ul[data-testid="stSelectboxVirtualDropdown"].st-cx,
    body ul.st-cx[data-testid="stSelectboxVirtualDropdown"],
    body ul[data-testid="stSelectboxVirtualDropdown"] {
      background: #0F172A !important;
      background-color: #0F172A !important;
      background-image: none !important;
    }
    body ul[data-testid="stSelectboxVirtualDropdown"] li {
      background: transparent !important;
      background-color: transparent !important;
      color: #E5E7EB !important;
      opacity: 1 !important;
    }
    body ul[data-testid="stSelectboxVirtualDropdown"] li:hover {
      background: rgba(56,189,248,.16) !important;
      background-color: rgba(56,189,248,.16) !important;
    }
    body ul[data-testid="stSelectboxVirtualDropdown"] li * {
      color: #E5E7EB !important;
      opacity: 1 !important;
    }

    /* Deep Analyze button - teal tinted, visible, premium */
    html body button[data-testid="stBaseButton-secondary"],
    div.stButton > button[kind="secondary"][data-testid="stBaseButton-secondary"],
    .stButton > button[kind="secondary"][data-testid="stBaseButton-secondary"],
    button[kind="secondary"][data-testid="stBaseButton-secondary"] {
      background-color: rgba(56,189,248,.10) !important;
      background-image: none !important;
      color: rgba(56,189,248,.95) !important;
      border: 1px solid rgba(56,189,248,0.42) !important;
      font-weight: 700 !important;
      opacity: 1 !important;
      filter: none !important;
    }
    html body button[data-testid="stBaseButton-secondary"]:hover,
    div.stButton > button[kind="secondary"][data-testid="stBaseButton-secondary"]:hover,
    .stButton > button[kind="secondary"][data-testid="stBaseButton-secondary"]:hover,
    button[kind="secondary"][data-testid="stBaseButton-secondary"]:hover {
      background-color: rgba(56,189,248,.22) !important;
      background-image: none !important;
      border-color: rgba(56,189,248,.75) !important;
      color: rgba(255,255,255,.98) !important;
      box-shadow: 0 0 14px rgba(56,189,248,.22) !important;
    }
    html body button[data-testid="stBaseButton-secondary"] p,
    html body button[data-testid="stBaseButton-secondary"] span {
      color: rgba(56,189,248,.95) !important;
    }
    html body button[data-testid="stBaseButton-secondary"]:hover p,
    html body button[data-testid="stBaseButton-secondary"]:hover span {
      color: rgba(255,255,255,.98) !important;
    }

    /* Force Scan X primary button back to gradient */
    html body button[data-testid="stBaseButton-primary"],
    html body .stButton > button[kind="primary"][data-testid="stBaseButton-primary"] {
      background-image: linear-gradient(180deg, rgba(56,189,248,.95), rgba(14,116,144,.95)) !important;
      background-color: transparent !important;
      border: 1px solid rgba(56,189,248,.45) !important;
      color: #001018 !important;
      font-weight: 650 !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
