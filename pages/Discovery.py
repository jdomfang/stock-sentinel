import streamlit as st
import streamlit.components.v1 as components

# Ensure project root is on sys.path (avoids collisions with any installed `utils` package on Streamlit Cloud)
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
import json
import pandas as pd
from collections import defaultdict
import logging

from utils.navigation import render_sidebar_navigation, render_top_nav
from utils.ui import apply_theme, close_page, render_recommendation_panel, render_full_analysis_expander
from utils.sentiment import extract_tickers, analyze_sentiment_batch
from utils.finance import get_ticker_master_list, get_stock_data, get_last_close_prices_best_effort
from utils.projections import simple_projection
from utils.deep_analysis import ANALYSIS_PROMPTS, run_deep_analysis, generate_ai_summary


def _sentiment_pill(label: str) -> str:
    label = (label or "").strip()
    if label.lower() == "bullish":
        return '<span style="background:rgba(56,189,248,.18);color:rgba(56,189,248,.98);border:1px solid rgba(56,189,248,.35);padding:3px 10px;border-radius:999px;font-size:0.83rem;font-weight:700;">Bullish</span>'
    elif label.lower() == "bearish":
        return '<span style="background:rgba(239,68,68,.15);color:rgba(248,113,113,.98);border:1px solid rgba(239,68,68,.30);padding:3px 10px;border-radius:999px;font-size:0.83rem;font-weight:700;">Bearish</span>'
    else:
        return f'<span style="background:rgba(148,163,184,.12);color:rgba(148,163,184,.92);border:1px solid rgba(148,163,184,.25);padding:3px 10px;border-radius:999px;font-size:0.83rem;font-weight:700;">{label or "Neutral"}</span>'

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

# Code-only setting (X API supports 100 per request; pagination will fetch more)
# Keep cap aligned with deep analysis helper (max_pages=5 => ~500 tweets)
max_results = 500

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

        # Construct search query with sector-specific keywords (Free-tier compatible)
        # Note: Advanced operators like min_faves, filter:, and since: require Basic tier or higher
        # Using only basic operators: Boolean, lang, and -is:retweet for Free tier

        # Add sector-specific keywords to improve relevance (legacy v1).
        sector_keywords = {
            'tech': 'technology OR software OR AI OR chip OR semiconductor OR cloud OR internet',
            'healthcare': 'healthcare OR medical OR pharma OR biotechnology OR drug OR clinical OR FDA',
            'energy': 'energy OR oil OR gas OR renewable OR solar OR wind OR fossil OR petroleum',
            'finance': 'finance OR bank OR financial OR investment OR lending OR credit OR wealth',
            'consumer': 'consumer OR retail OR e-commerce OR shopping OR consumer goods OR discretionary',
            'utilities': 'utilities OR electric OR power OR water OR gas OR infrastructure OR telecom',
            'real estate': 'real estate OR property OR REIT OR housing OR commercial OR residential',
            'industrials': 'industrials OR manufacturing OR industrial OR aerospace OR defense OR construction',
            'materials': 'materials OR mining OR chemical OR steel OR cement OR commodity OR metals',
            'communication': 'communication OR telecom OR media OR entertainment OR broadcasting OR wireless'
        }

        # v2 sector topic blocks: broader retrieval + skip fragile post-fetch substring filtering.
        # Keep each topic list short enough to stay under X query length limits (single request).
        # NOTE: A bare "$" token can trigger X query parse errors (400). Keep intent simple.
        INVEST_INTENT = "(stock OR stocks OR shares)"
        SECTOR_TOPIC_V2 = {
            "materials": (
                '"materials sector" OR "basic materials" OR "materials stocks" OR XLB '
                'OR mining OR metals OR chemicals OR fertilizer '
                'OR steel OR cement OR copper OR gold OR "iron ore"'
            ),
            "tech": (
                '"technology sector" OR "tech sector" OR "tech stocks" OR "software stocks" '
                'OR software OR SaaS OR "cloud computing" OR cloud OR "data center" '
                'OR AI OR "artificial intelligence" OR "machine learning" OR ML '
                'OR semiconductor OR semiconductors OR chip OR chips OR GPU '
                'OR cybersecurity OR "zero trust" '
                'OR XLK OR SMH OR HACK OR CLOU'
            ),
            "healthcare": (
                '"healthcare sector" OR "health care sector" OR "healthcare stocks" OR "biotech stocks" '
                'OR healthcare OR "health care" OR pharma OR pharmaceutical OR biotech OR biotechnology '
                'OR "drug approval" OR FDA OR "clinical trial" OR "phase 1" OR "phase 2" OR "phase 3" '
                'OR "medical device" OR medtech OR "gene therapy" OR "cell therapy" '
                'OR hospital OR hospitals OR insurer OR insurers OR "managed care" '
                'OR XLV OR XBI OR IBB'
            ),
            "energy": (
                '"energy sector" OR "energy stocks" OR oil OR crude OR WTI OR brent '
                'OR gas OR "natural gas" OR LNG OR "liquefied natural gas" '
                'OR refinery OR refining OR "oilfield services" OR "rig count" OR shale OR "E&P" '
                'OR pipeline OR pipelines OR midstream '
                'OR OPEC OR "production cut" '
                'OR renewable OR renewables OR solar OR wind OR "clean energy" '
                'OR XLE OR XOP OR OIH OR TAN OR ICLN'
            ),
            "finance": (
                '"financial sector" OR "finance sector" OR "financial stocks" OR "bank stocks" '
                'OR finance OR financial OR bank OR banks OR banking '
                'OR "interest rates" OR yield OR "yield curve" '
                'OR NIM OR "net interest margin" OR "loan growth" OR credit OR "credit quality" '
                'OR brokerage OR "asset manager" '
                'OR fintech OR payments '
                'OR XLF OR KRE OR KBE'
            ),
            "consumer": (
                '"consumer sector" OR "consumer stocks" '
                'OR consumer OR retail OR "e-commerce" OR ecommerce '
                'OR discretionary OR "consumer discretionary" '
                'OR staples OR "consumer staples" '
                'OR restaurants OR travel OR airlines OR cruise '
                'OR autos OR "EV" OR "electric vehicle" '
                'OR grocery OR beverage '
                'OR XLY OR XLP'
            ),
            "utilities": (
                '"utilities sector" OR "utilities stocks" '
                'OR utilities OR utility OR "electric utility" OR "power grid" OR grid '
                'OR "rate case" OR "regulated utility" OR "regulated utilities" '
                'OR transmission OR distribution '
                'OR "power demand" OR "electric demand" '
                'OR XLU'
            ),
            "real estate": (
                '"real estate sector" OR "real estate stocks" OR REIT OR REITs '
                'OR "commercial real estate" OR CRE OR "office" OR "industrial REIT" '
                'OR multifamily OR apartments OR "single family rental" '
                'OR "cap rate" OR "cap rates" OR refinancing OR "maturity wall" '
                'OR "mortgage rates" OR "housing market" '
                'OR XLRE OR VNQ'
            ),
            "industrials": (
                '"industrials sector" OR "industrial sector" OR "industrials stocks" OR "industrial stocks" '
                'OR industrial OR industrials OR manufacturing OR factory OR factories '
                'OR aerospace OR "aerospace and defense" OR defense OR "defense stocks" '
                'OR machinery OR "heavy equipment" OR logistics OR freight OR shipping '
                'OR "supply chain" OR "backlog" OR "bookings" OR "orders" '
                'OR PMI OR "ISM" '
                'OR XLI OR ITA'
            ),
            "communication": (
                '"communication services" OR "communications sector" OR "telecom sector" OR "media stocks" '
                'OR communication OR communications OR telecom OR wireless OR 5G OR broadband '
                'OR streaming OR "ad revenue" OR advertising OR "ad spend" '
                'OR media OR entertainment '
                'OR "social media" OR "online advertising" '
                'OR XLC'
            ),
        }

        sector_key = sector.lower()
        use_v2 = sector_key in SECTOR_TOPIC_V2

        if use_v2:
            query = f"({SECTOR_TOPIC_V2[sector_key]}) {INVEST_INTENT} lang:en -is:retweet"
        else:
            sector_terms = sector_keywords.get(sector_key, sector)
            query = f"({sector} OR {sector_terms}) stock (bullish OR opportunity OR catalyst OR growth OR earnings) -bearish lang:en -is:retweet"

        logger.info(f"🔍 Starting X search for sector: {sector}")
        logger.info(f"🧠 Using query mode: {'v2' if use_v2 else 'v1'}")
        logger.info(f"🧹 Post-filter enabled: {not use_v2}")
        logger.info(f"📝 Search query: {query}")
        logger.info(f"📊 Max results requested: {max_results}")

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
        # Keep UI sector keys unchanged; map them to the exact sector strings present in ticker_master.
        ui_to_nasdaq_sectors = {
            "tech": {"Technology"},
            "healthcare": {"Health Care"},
            "energy": {"Energy"},
            "finance": {"Finance"},
            # UI has a single "consumer" bucket; Nasdaq splits this into two sectors.
            "consumer": {"Consumer Discretionary", "Consumer Staples"},
            "utilities": {"Utilities"},
            "real estate": {"Real Estate"},
            "industrials": {"Industrials"},
            # Nasdaq uses "Basic Materials" (not "Materials")
            "materials": {"Basic Materials"},
            # Nasdaq dataset we ingested uses "Telecommunications" (not "Communication Services")
            "communication": {"Telecommunications"},
        }
        selected_nasdaq_sectors = ui_to_nasdaq_sectors.get((sector or "").lower(), set())

        # Aggregate data by ticker (incremental across pages)
        ticker_data = defaultdict(lambda: {
            'mentions': 0,
            'sentiment_scores': [],
            'sentiments': [],
            'sample_tweets': []
        })

        validated_set = set()
        checked_set = set()
        company_by_ticker = {}

        next_token = None
        total_sector_relevant = 0

        def _sector_relevant(page_tweets):
            if use_v2:
                return page_tweets
            out = []
            for tw in page_tweets:
                text = (tw.get('text', '') or '').lower()
                if any(keyword.lower() in text for keyword in sector_terms.split(' OR ')) or sector.lower() in text:
                    out.append(tw)
            return out

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

        progress_bar = st.progress(0)
        status_text = st.empty()
        status_text.markdown(f"**📡 Scanning X for {sector} momentum...**")

        with st.spinner(f"Searching {sector} stocks on X..."):
            pages = 0
            while total_sector_relevant < SAFETY_CAP_TWEETS:
                pages += 1
                # Page fetch (always 100 max)
                progress_bar.progress(15); status_text.markdown(f"**📡 Scanning X for {sector} momentum...**")
                res = search_x_tweets_page(query=query, max_results=PER_PAGE, timeframe="24h", next_token=next_token)
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
                    break

                # Sector relevance filter (if applicable)
                page_tweets = _sector_relevant(page_tweets)
                if not page_tweets:
                    if not next_token:
                        break
                    continue

                # Enforce safety cap at *sector-relevant* tweet level
                remaining = SAFETY_CAP_TWEETS - total_sector_relevant
                if remaining <= 0:
                    break
                if len(page_tweets) > remaining:
                    page_tweets = page_tweets[:remaining]

                total_sector_relevant += len(page_tweets)

                # Ticker extraction first (regex, cheap), then ONE batched
                # sentiment call for the whole page. This used to score tweets
                # one at a time -- up to ~500 sequential forward passes per scan
                # -- which is what killed the 2026-08-01 healthcare scan mid-loop
                # and spent a credit for nothing. Batching per page rather than
                # per scan keeps peak memory bounded and preserves the
                # between-page progress updates below.
                _page_hits = []
                for tweet in page_tweets:
                    text = tweet.get('text', '')
                    tickers = extract_tickers(text)
                    if tickers:
                        _page_hits.append((text, tickers))

                _page_sentiments = analyze_sentiment_batch([t for t, _ in _page_hits])

                for (text, tickers), sentiment_result in zip(_page_hits, _page_sentiments):
                    for ticker in tickers:
                        ticker_data[ticker]['mentions'] += 1
                        ticker_data[ticker]['sentiment_scores'].append(sentiment_result['score'])
                        ticker_data[ticker]['sentiments'].append(sentiment_result['sentiment'])

                        if 'raw_labels' not in ticker_data[ticker]:
                            ticker_data[ticker]['raw_labels'] = []
                        ticker_data[ticker]['raw_labels'].append(
                            f"{sentiment_result['label']}:{sentiment_result['score']:.3f}"
                        )

                        if len(ticker_data[ticker]['sample_tweets']) < 3:
                            short_text = text[:150] + "..." if len(text) > 150 else text
                            ticker_data[ticker]['sample_tweets'].append(short_text)

                # After each page, try to validate enough tickers.
                progress_bar.progress(70); status_text.markdown("**⚡ Building your shortlist...**")
                _try_validate_from_current_ranking()

                logger.info(
                    "📄 Discovery pagination pages=%s sector_tweets=%s validated=%s has_next=%s",
                    pages,
                    total_sector_relevant,
                    len(validated_set),
                    bool(next_token),
                )

                # Stop early if page 1 (or subsequent pages) already yields 10 validated
                if len(validated_set) >= TARGET_VALIDATED:
                    break

                if not next_token:
                    break

        progress_bar.progress(100); status_text.empty(); progress_bar.empty()

        logger.info(f"🎯 Sector-relevant tweets processed (capped): {total_sector_relevant}")

        if total_sector_relevant == 0:
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

        # Convert to final DataFrame
        if ticker_data:
            rows = []
            for ticker, info in ticker_data.items():
                if not info.get('sentiment_scores'):
                    continue
                avg_sentiment = sum(info['sentiment_scores']) / len(info['sentiment_scores'])
                sentiment_counts = {}
                for s in info['sentiments']:
                    sentiment_counts[s] = sentiment_counts.get(s, 0) + 1
                overall_sentiment = max(sentiment_counts, key=sentiment_counts.get)

                rows.append({
                    'Ticker': ticker,
                    'Mentions': info['mentions'],
                    'Avg Sentiment Score': round(avg_sentiment, 3),
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
        if _delivered:
            complete_work(_credit.event_id, "completed", f"sector={sector}")
        else:
            refund_credit("scan", _credit.event_id, "scan did not complete")
            complete_work(_credit.event_id, "failed", "aborted or errored")

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

                    df_out = st.session_state.df_valid.drop(columns=["Valid", "Mentions", "Sample Tweets"], errors="ignore")

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
        avg_sent = float(dfv["Avg Sentiment Score"].mean()) if (dfv is not None and "Avg Sentiment Score" in dfv.columns and len(dfv) > 0) else 0.0

        if avg_sent >= 0.15:
            sent_label = f"Bullish ({avg_sent:.2f})"
            sent_color = "rgba(56,189,248,.95)"
            sent_border = "rgba(56,189,248,.28)"
        elif avg_sent <= -0.10:
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
    """Render the deep analysis panel inline below a ticker row."""
    from utils.deep_analysis import ANALYSIS_PROMPTS
    _ai = generate_ai_summary(deep_results)

    _price, _proj, _hold, _pts = "Unavailable", "Unavailable", "Unavailable", 0
    try:
        _sd = get_stock_data(ticker)
        if _sd.get("error") is None and _sd.get("prices"):
            _prices = _sd["prices"]
            _pts = len(_prices)
            _lp = _prices[-1]
            if isinstance(_lp, (int, float)):
                _price = f"${_lp:.2f}"
            _proj_r = simple_projection(_prices, _ai["avg_sentiment"], days=30)
            if _proj_r.get("error") is None:
                _p10, _p90 = _proj_r.get("gain_p10"), _proj_r.get("gain_p90")
                _proj = f"{_p10:.1f}–{_p90:.1f}%" if (_p10 is not None and _p90 is not None) else f"{float(_proj_r.get('avg_gain',0)):.1f}%"
                _hold = f"{int(_proj_r.get('suggested_hold_days', 0))} days"
    except Exception:
        pass

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

# ── Results table ──
if st.session_state.df_valid is not None:
    df_valid_display = st.session_state.df_valid.drop(columns=["Mentions", "Sample Tweets"], errors="ignore")

    if len(df_valid_display) > 0:
        st.markdown(
            f'<div style="font-size:1.15rem;font-weight:800;letter-spacing:-0.01em;'
            f'color:rgba(229,231,235,.98);margin:0.35rem 0 0.65rem 0;">'
            f'{sector.title()} · {len(df_valid_display)} stocks</div>',
            unsafe_allow_html=True,
        )

        # Sort: Bullish first, then Neutral, then Bearish
        sentiment_order = {"bullish": 0, "neutral": 1, "bearish": 2}
        df_valid_display = df_valid_display.copy()
        df_valid_display["_sort"] = df_valid_display["Overall Sentiment"].str.lower().map(lambda x: sentiment_order.get(x, 1))
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
                                _disc_holder["result"] = run_deep_analysis(
                                    ticker_symbol,
                                    _disc_sector,
                                )
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
