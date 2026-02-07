import json
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from utils.navigation import render_sidebar_navigation, render_top_nav
from utils.ui import apply_theme, close_page
from utils.deep_analysis import generate_ai_summary


def _load_demo_scan() -> pd.DataFrame:
    """Load the saved Scan demo.

    Priority:
      1) data/education/scan_latest.json (freshly saved from Discovery)
      2) data/demo/scan_tech.json (fallback)
    """
    root = Path(__file__).resolve().parents[1]
    candidates = [
        root / "data" / "education" / "scan_latest.json",
        root / "data" / "demo" / "scan_tech.json",
    ]

    payload = None
    for p in candidates:
        if not p.exists():
            continue
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
            break
        except Exception:
            payload = None

    if not payload:
        return pd.DataFrame()

    rows = payload.get("validated_rows", []) or []
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _load_demo_deep() -> tuple[str, str, dict]:
    """Load a saved Deep Analyze demo payload (no API calls).

    Priority:
      1) data/education/deep_latest.json (freshly saved from Discovery)
      2) data/demo/deep_NVDA_tech.json (fallback)
    """
    root = Path(__file__).resolve().parents[1]
    candidates = [
        root / "data" / "education" / "deep_latest.json",
        root / "data" / "demo" / "deep_NVDA_tech.json",
    ]

    payload = None
    for p in candidates:
        if not p.exists():
            continue
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
            break
        except Exception:
            payload = None

    if not payload:
        return "", "", {}

    ticker = payload.get("ticker", "") or ""
    sector = payload.get("sector", "") or ""
    results = payload.get("analysis_results", {}) or {}
    return ticker, sector, results


st.set_page_config(
    page_title="Stock Sentinel",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

render_sidebar_navigation()
render_top_nav()
apply_theme()

# --- Home-specific styling (global theme comes from utils.ui.apply_theme) ---
st.markdown(
    """
    <style>
    /* Home page styling; global theme comes from utils.ui.apply_theme() */

    /* Main container spacing */
    div[data-testid="stMainBlockContainer"] {
      max-width: 100%;
      padding-left: 2rem;
      padding-right: 2rem;
      padding-top: 0.25rem;
    }

    .discovery-wrapper {
      max-width: 1240px;
      margin: 0 auto;
      padding: 0 1rem;
    }

    /* Section titles */
    .section-title {
      font-size: 1.35rem;
      font-weight: 800;
      letter-spacing: -0.02em;
      margin: 0.25rem 0 0.75rem 0;
    }

    /* How-it-works cards */
    .how-card {
      border: 1px solid var(--border);
      background: linear-gradient(180deg, rgba(15,23,42,.80), rgba(2,6,23,.35));
      border-radius: 16px;
      padding: 14px 14px 12px 14px;
      min-height: 120px;
    }
    .how-step {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 28px;
      height: 28px;
      border-radius: 999px;
      background: rgba(56,189,248,.15);
      border: 1px solid rgba(56,189,248,.30);
      color: rgba(56,189,248,.98);
      font-weight: 800;
      font-size: 0.90rem;
      margin-right: 10px;
      flex: 0 0 auto;
    }
    .how-head {
      display: flex;
      align-items: center;
      margin-bottom: 8px;
    }
    .how-title {
      font-weight: 800;
      font-size: 1.02rem;
      margin: 0;
      color: rgba(229,231,235,.98);
    }
    .how-desc {
      color: rgba(229,231,235,.78);
      font-size: 0.94rem;
      line-height: 1.45;
      margin: 0;
    }

    /* Demo table tweaks */
    .demo-note {
      color: rgba(229,231,235,.70);
      font-size: 0.92rem;
      margin-top: -2px;
      margin-bottom: 10px;
    }
    .ticker-row {
      border: 1px solid rgba(148,163,184,0.14);
      border-radius: 14px;
      padding: 10px 10px;
      margin: 10px 0;
      background: rgba(2,6,23,.22);
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

    /* Hero (no box) */
    .hero {
      /* Pull hero up so it sits closer to the top nav (top-only tweak; keep below layout unchanged) */
      margin: -4.75rem 0 18px 0;
      padding: 8px 2px 2px 2px;
    }
    .hero-eyebrow {
      color: rgba(56,189,248,.95);
      font-weight: 750;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      font-size: 0.78rem;
      margin-bottom: 10px;
    }
    .hero-title {
      font-size: 2.05rem;
      font-weight: 850;
      letter-spacing: -0.03em;
      line-height: 1.1;
      margin: 0 0 10px 0;
    }
    .hero-subtitle {
      color: var(--muted);
      font-size: 1.05rem;
      line-height: 1.5;
      margin: 0 0 12px 0;
      max-width: 980px;
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

    /* Primary buttons */
    button[data-testid="stBaseButton-primary"],
    .stButton > button[kind="primary"] {
      background: linear-gradient(180deg, rgba(56,189,248,.95), rgba(14,116,144,.95)) !important;
      background-color: transparent !important;
      border: 1px solid rgba(56,189,248,.45) !important;
      color: #001018 !important;
      font-weight: 650 !important;
      padding: 0.25rem 0.65rem !important;
      font-size: 0.85rem !important;
      min-height: 34px !important;
    }

    /* Hide Streamlit "Made with" footer */
    footer { visibility: hidden; }
    </style>
    """,
    unsafe_allow_html=True,
)

# JS-only UI fix (copied from Discovery so Home behaves identically across Streamlit builds)
components.html(
    """
    <script>
    (function () {
      const APPLY_TO = (doc) => {
        const ul = doc.querySelector('ul[data-testid="stSelectboxVirtualDropdown"]');
        if (ul) {
          ul.style.setProperty('background-color', '#0F172A', 'important');
          ul.style.setProperty('color', '#E5E7EB', 'important');
          ul.querySelectorAll('li, li *').forEach((el) => {
            el.style.setProperty('color', '#E5E7EB', 'important');
            el.style.setProperty('opacity', '1', 'important');
          });
        }
      };

      const APPLY = () => {
        try { APPLY_TO(document); } catch (e) {}
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

# --- Hero: same structure as Discovery; wording swapped for Home ---
st.markdown(
    """
    <div class="hero">
      <div class="hero-eyebrow">Stock Sentinel</div>
      <div class="hero-title">Finding short‑term opportunities shouldn’t feel like a full‑time job.</div>
      <div class="hero-subtitle">We turn noise into signals by analyzing social media sentiment and using AI‑driven market data analysis to validate real momentum.</div>

      <!-- chips + disclaimer removed on request -->
    </div>
    """,
    unsafe_allow_html=True,
)

# How it works (polished cards)
st.markdown('<div class="section-title">How it works</div>', unsafe_allow_html=True)

h1, h2, h3 = st.columns(3)
with h1:
    st.markdown(
        """
        <div class="how-card">
          <div class="how-head"><div class="how-step">1</div><div class="how-title">Scan social chatter</div></div>
          <p class="how-desc">Pick a sector and we identify <b>US stocks</b> gaining unusual attention—fast.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
with h2:
    st.markdown(
        """
        <div class="how-card">
          <div class="how-head"><div class="how-step">2</div><div class="how-title">AI sentiment signal</div></div>
          <p class="how-desc">We summarize the tone and confidence so you can triage what’s worth a closer look.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
with h3:
    st.markdown(
        """
        <div class="how-card">
          <div class="how-head"><div class="how-step">3</div><div class="how-title">Deep Analyze guidance</div></div>
          <p class="how-desc">Get a Buy / Watch / Avoid recommendation with key drivers and risk notes.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown("<div style='height: 1.25rem;'></div>", unsafe_allow_html=True)

# Demo scan output (match the post-scan layout from Discovery)
df_demo = _load_demo_scan()
if df_demo.empty:
    st.info("No demo data found yet. You can generate it via scripts/record_demo.py")
else:
    st.markdown('<div class="section-title">Sample scan results <span style="color: rgba(229,231,235,.65); font-weight: 700;">(demo)</span></div>', unsafe_allow_html=True)
    st.markdown('<div class="demo-note">Shortlist for action: This table ranks candidates worth a closer look. If a ticker stands out, click Deep Analyze to see catalysts, red flags, and guidance.</div>', unsafe_allow_html=True)

    # Match the simplified post-scan Discovery table
    header_cols = st.columns([1.1, 1.8, 1.2, 1.1, 1.0])
    header_labels = [
        "Ticker",
        "Company",
        "Last Close",
        "Overall",
        "Deep Analyze",
    ]
    for col, label in zip(header_cols, header_labels):
        col.markdown(f"**{label}**")

    for _, row in df_demo.iterrows():
        ticker_symbol = row.get("Ticker", "")
        company_name = row.get("Company Name", "")
        overall_sentiment = row.get("Overall Sentiment", "")

        last_close = row.get("Last Close", None)
        if isinstance(last_close, (int, float)):
            last_close_display = f"${float(last_close):.2f}"
        else:
            last_close_display = "N/A"

        st.markdown("<div class='ticker-row'>", unsafe_allow_html=True)
        col1, col2, col3, col4, col5 = st.columns([1.1, 1.8, 1.2, 1.1, 1.0])
        with col1:
            st.markdown(f"**{ticker_symbol}**")
        with col2:
            st.markdown(company_name)
        with col3:
            st.markdown(last_close_display)
        with col4:
            st.markdown(overall_sentiment)
        with col5:
            st.button("Deep Analyze", key=f"home_deep_{ticker_symbol}", disabled=True)
        st.markdown("</div>", unsafe_allow_html=True)

    # (removed summary line on request)

st.markdown("<div style='height: 1.25rem;'></div>", unsafe_allow_html=True)

# Deep Analysis sample (educational, no API calls)
demo_ticker, demo_sector, demo_results = _load_demo_deep()
if demo_results:
    st.subheader("Deep Analyze (demo)")
    st.caption(
        "Deep Analyze turns the data into a clear recommendation (Buy / Watch / Avoid), confidence, "
        "and the key reasons (catalysts and red flags)—so you can decide whether to take a position or stand aside."
    )

    ai_summary = generate_ai_summary(demo_results)

    # Metric cards (bigger so text fits cleanly on Home)
    st.markdown(
        """
        <style>
        .home-metrics [data-testid="stMetric"] {
          padding: 10px 12px !important;
          border-radius: 12px !important;
          min-height: 74px !important;
        }
        .home-metrics [data-testid="stMetric"] label {
          font-size: 0.82rem !important;
          margin-bottom: 2px !important;
          white-space: nowrap;
        }
        .home-metrics [data-testid="stMetric"] [data-testid="stMetricValue"] {
          font-size: 1.05rem !important;
          line-height: 1.15 !important;
          white-space: nowrap;
        }
        .home-metrics {
          margin-top: 0px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="home-metrics">', unsafe_allow_html=True)
    m1, m2, m3, _sp = st.columns([1.0, 1.0, 1.0, 5.0])
    with m1:
        st.metric("Recommendation", ai_summary["recommendation"])
    with m2:
        st.metric("Confidence", ai_summary.get("confidence") or "")
    with m3:
        st.metric("Weighted Sentiment", f"{ai_summary['avg_sentiment']:.3f}")
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("**Rationale:**")
    # Match Discovery-style rationale bullets
    for line in ai_summary.get("rationale", [])[:2]:
        st.markdown(f"- {line}")

    # (education line shown at section header)

else:
    st.info("No Deep Analyze demo data found yet. (Expected: data/education/deep_latest.json)")

st.markdown("<div style='height: 1.25rem;'></div>", unsafe_allow_html=True)

# CTA at bottom
cta = st.container()
with cta:
    if st.button("Run your scan", type="primary", use_container_width=False):
        from utils.auth import is_logged_in
        st.switch_page("pages/Discovery.py" if is_logged_in() else "pages/Auth.py")
    # Helper text should be directly below the button
    st.caption("Includes $5.00 in free credits to get started.")

close_page()
