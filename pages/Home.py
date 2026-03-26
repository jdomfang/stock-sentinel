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

    /* Keep the header brand in the same blue family as the current primary button */
    .clawd-topnav .clawd-brandtext,
    .clawd-topnav .clawd-brandtext *,
    .clawd-brand .clawd-brandtext,
    .clawd-brand .clawd-brandtext * {
      color: rgba(56,189,248,.95) !important;
      -webkit-text-fill-color: rgba(56,189,248,.95) !important;
    }

    /* Main container: match v3 mockup container */
    div[data-testid="stMainBlockContainer"] {
      max-width: 1100px;
      margin: 0 auto;
      padding-left: clamp(16px, 4vw, 28px);
      padding-right: clamp(16px, 4vw, 28px);
      padding-top: 0.25rem;
    }

    .discovery-wrapper {
      max-width: 1100px;
      margin: 0 auto;
      padding: 0;
    }

    /* Section titles */
    .section-title {
      font-size: 1.35rem;
      font-weight: 800;
      letter-spacing: -0.02em;
      margin: -0.84rem 0 0.26rem 0;
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
      margin-top: -11px;
      margin-bottom: 3px;
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
      margin: -8.10rem 0 2px 0;
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
      margin: 0 0 0 0;
      max-width: 760px;
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

    /* Capability cards: controlled-width row like the mock, but with live copy unchanged */
    .st-key-home_cap_grid [data-testid="stHorizontalBlock"] {
      gap: 0 !important;
      align-items: stretch !important;
    }

    .st-key-home_cap_grid [data-testid="column"] {
      min-width: 0 !important;
      display: flex !important;
      align-self: stretch !important;
    }

    .st-key-home_cap_grid [data-testid="column"] > div {
      width: 100% !important;
    }

    .st-key-home_card_scan,
    .st-key-home_card_analyze {
      border: 1px solid rgba(148,163,184,0.18);
      background: linear-gradient(180deg, rgba(15,23,42,.92), rgba(15,23,42,.72));
      border-radius: 16px;
      padding: 15px 15px 12px 15px;
      box-shadow: 0 10px 28px rgba(0,0,0,.35);
      min-height: 174px;
      width: 100%;
      height: 174px;
      display: grid;
      grid-template-rows: 1fr auto;
    }

    .st-key-home_card_scan_actions,
    .st-key-home_card_analyze_actions {
      margin-top: 0;
      padding-top: 10px;
      align-self: stretch;
      justify-self: stretch;
      width: calc(100% - 30px) !important;
      max-width: calc(100% - 30px) !important;
      overflow: hidden !important;
    }

    .cap-title {
      font-weight: 800;
      font-size: 1.00rem;
      margin: 0;
      color: rgba(229,231,235,.98);
    }
    .cap-desc {
      margin: 6px 0 0 0;
      color: rgba(229,231,235,.78);
      font-size: 0.94rem;
      line-height: 1.45;
      max-width: 40ch;
    }

    .st-key-home_card_scan_actions [data-testid="stHorizontalBlock"],
    .st-key-home_card_analyze_actions [data-testid="stHorizontalBlock"] {
      gap: 8px !important;
      align-items: center !important;
      flex-wrap: nowrap !important;
      width: 100% !important;
      max-width: 100% !important;
      box-sizing: border-box !important;
    }

    .st-key-home_card_scan_actions [data-testid="column"],
    .st-key-home_card_analyze_actions [data-testid="column"] {
      min-width: 0 !important;
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

    .st-key-home_card_scan [data-baseweb="select"] > div,
    .st-key-home_card_analyze [data-baseweb="input"] > div {
      border-radius: 12px !important;
      min-height: 38px !important;
      padding-left: 11px !important;
      padding-right: 11px !important;
      box-shadow: inset 0 1px 0 rgba(255,255,255,.02) !important;
    }

    /* Sector dropdown menu readability */
    ul[data-testid="stSelectboxVirtualDropdown"] {
      background: #0F172A !important;
      color: #E5E7EB !important;
      border: 1px solid rgba(148,163,184,0.18) !important;
    }
    ul[data-testid="stSelectboxVirtualDropdown"] li,
    ul[data-testid="stSelectboxVirtualDropdown"] li *,
    ul[data-testid="stSelectboxVirtualDropdown"] [role="option"],
    ul[data-testid="stSelectboxVirtualDropdown"] [role="option"] * {
      color: #E5E7EB !important;
      -webkit-text-fill-color: #E5E7EB !important;
      opacity: 1 !important;
    }
    ul[data-testid="stSelectboxVirtualDropdown"] li[aria-selected="true"],
    ul[data-testid="stSelectboxVirtualDropdown"] [role="option"][aria-selected="true"] {
      background: rgba(56,189,248,.16) !important;
      color: #F8FAFC !important;
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

    .st-key-home_card_scan .stButton > button,
    .st-key-home_card_analyze .stButton > button {
      border-radius: 12px !important;
      min-height: 38px !important;
      padding: 0.22rem 0.62rem !important;
      font-size: 0.81rem !important;
      max-width: 100% !important;
      box-shadow: 0 8px 20px rgba(14,116,144,.22) !important;
    }

    /* Hide Streamlit "Made with" footer */
    footer { visibility: hidden; }

    /* -----------------------------
       Responsive layout helpers
       Goal: keep ONE layout, but allow Streamlit columns to wrap nicely.
       ----------------------------- */

    /* Allow our wrapped sections to reflow instead of cramming columns */
    .how-grid [data-testid="stHorizontalBlock"],
    .cap-grid [data-testid="stHorizontalBlock"],
    .demo-header [data-testid="stHorizontalBlock"],
    .ticker-row [data-testid="stHorizontalBlock"] {
      flex-wrap: wrap !important;
      gap: 12px !important;
    }

    /* Give Streamlit columns a sane min width so they wrap to 2-up / 1-up naturally */
    .how-grid [data-testid="column"],
    .cap-grid [data-testid="column"],
    .demo-header [data-testid="column"],
    .ticker-row [data-testid="column"] {
      flex: 1 1 260px !important;
      min-width: 260px !important;
    }

    /* On phones, force single-column flow for these sections */
    @media (max-width: 640px) {
      .hero {
        /* Negative margin feels premium on desktop but can collide on small screens */
        margin: -2.5rem 0 14px 0;
      }

      .how-grid [data-testid="column"],
      .cap-grid [data-testid="column"],
      .demo-header [data-testid="column"],
      .ticker-row [data-testid="column"] {
        flex: 1 1 100% !important;
        min-width: 100% !important;
      }

      /* Make the CTA button easier to hit */
      button[data-testid="stBaseButton-primary"],
      .stButton > button[kind="primary"] {
        min-height: 44px !important;
        padding: 0.5rem 0.9rem !important;
        font-size: 0.95rem !important;
      }
    }
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


# --- Hero: same structure as Discovery; wording swapped for Home ---
st.markdown(
    """
    <div class="hero">
      <div class="hero-title">Finding short-term opportunities shouldn't feel like a full-time job.</div>
      <div class="hero-subtitle">We turn noise into signals by analyzing social media sentiment and using market data to validate real momentum.</div>

      <!-- chips + disclaimer removed on request -->
    </div>
    """,
    unsafe_allow_html=True,
)

# --- Capability cards (v3 mockup layout) ---
from utils.auth import is_logged_in

with st.container(key="home_cap_grid"):
    cap1, cap_gap, cap2, _cap_spacer = st.columns([1.0, 0.045, 1.0, 0.85])

    with cap1:
        with st.container(key="home_card_scan"):
            st.markdown(
                """
                <div class="cap-title">Market Scan</div>
                <p class="cap-desc">Pick a sector and we identify US stocks gaining unusual social media attention.</p>
                """,
                unsafe_allow_html=True,
            )

            with st.container(key="home_card_scan_actions"):
                sel_col, btn_col = st.columns([1.22, 0.98])
                with sel_col:
                    home_sector = st.selectbox(
                        "Sector",
                        options=[
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
                        key="home_sector",
                        label_visibility="collapsed",
                    )
                with btn_col:
                    if st.button(
                        "Sentinel Scan",
                        type="primary",
                        key="home_cap_scan",
                        use_container_width=True,
                    ):
                        # Persist intent in session_state so Home -> Auth -> Discovery can
                        # continue seamlessly after login.
                        st.session_state["discovery_sector"] = home_sector
                        st.session_state["_autostart_discovery_scan"] = True
                        st.session_state["_after_auth_page"] = "Discovery"

                        st.switch_page("pages/Discovery.py" if is_logged_in() else "pages/Auth.py")

    with cap2:
        with st.container(key="home_card_analyze"):
            st.markdown(
                """
                <div class="cap-title">Analyze a Stock</div>
                <p class="cap-desc">Enter a ticker and get a clear signal (Buy/Watch/Avoid) with catalysts, risks, and growth projections.</p>
                """,
                unsafe_allow_html=True,
            )

            with st.container(key="home_card_analyze_actions"):
                in_col, btn_col = st.columns([1.08, 1.02])
                with in_col:
                    analyze_ticker = st.text_input(
                        "Ticker",
                        value=st.session_state.get("home_analyze_ticker", ""),
                        placeholder="Ticker — e.g. TSLA",
                        key="home_analyze_ticker",
                        label_visibility="collapsed",
                    )
                with btn_col:
                    if st.button(
                        "Analyze",
                        type="primary",
                        key="home_cap_analyze",
                        use_container_width=True,
                    ):
                        ticker = (analyze_ticker or "").strip().upper()
                        st.session_state["prefill_deep_ticker"] = ticker
                        st.session_state["_after_auth_page"] = "Deep_Analysis"

                        st.switch_page("pages/Deep_Analysis.py" if is_logged_in() else "pages/Auth.py")

st.markdown("<div style='height: 0rem;'></div>", unsafe_allow_html=True)

# (Removed) How it works section - per request


# Demo scan output (match the post-scan layout from Discovery)
st.markdown('<div id="demo-scan" style="margin-top:-1.12rem;"></div>', unsafe_allow_html=True)

# If user clicked "View demo results" in the capability card, scroll here.
if st.session_state.pop("_scroll_demo", False):
    components.html(
        """
        <script>
          const el = window.parent.document.getElementById('demo-scan');
          if (el) el.scrollIntoView({behavior: 'smooth', block: 'start'});
        </script>
        """,
        height=0,
    )

df_demo = _load_demo_scan()
if df_demo.empty:
    st.info("No demo data found yet. You can generate it via scripts/record_demo.py")
else:
    st.markdown('<div class="section-title">Sample scan results <span style="color: rgba(229,231,235,.65); font-weight: 700;">(demo)</span></div>', unsafe_allow_html=True)
    st.markdown('<div class="demo-note">Shortlist for action: This table ranks candidates worth a closer look. If a ticker stands out, click Deep Analyze to see catalysts, red flags, and guidance.</div>', unsafe_allow_html=True)

    # Match the simplified post-scan Discovery table
    st.markdown('<div class="demo-header">', unsafe_allow_html=True)
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
    st.markdown('</div>', unsafe_allow_html=True)

    for _, row in df_demo.iterrows():
        ticker_symbol = row.get("Ticker", "")
        company_name = row.get("Company Name", "")
        overall_sentiment = row.get("Overall Sentiment", "")

        last_close = row.get("Current Price ($)", None)
        try:
            last_close_display = f"${float(last_close):.2f}"
        except (TypeError, ValueError):
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

st.markdown("<div style='height: 0.45rem;'></div>", unsafe_allow_html=True)

# Deep Analysis sample (educational, no API calls)
demo_ticker, demo_sector, demo_results = _load_demo_deep()
if demo_results:
    st.markdown(
        """
        <style>
        .deep-demo-section {
          margin-top: -0.62rem;
        }
        .deep-demo-title {
          font-size: 1.32rem;
          font-weight: 700;
          line-height: 1.15;
          margin: 0 0 0.18rem 0;
          color: rgba(248,250,252,.98);
        }
        .deep-demo-caption {
          color: rgba(229,231,235,.72);
          font-size: 0.92rem;
          line-height: 1.42;
          margin: 0 0 0.22rem 0;
          max-width: 900px;
        }
        </style>
        <div class="deep-demo-section">
          <div class="deep-demo-title">Deep Analyze (demo)</div>
          <div class="deep-demo-caption">Deep Analyze turns the data into a clear recommendation (Buy / Watch / Avoid), confidence, and the key reasons (catalysts and red flags)-so you can decide whether to take a position or stand aside.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    ai_summary = generate_ai_summary(demo_results)

    # Metric cards (bigger so text fits cleanly on Home)
    st.markdown(
        """
        <style>
        .home-metrics [data-testid="stMetric"] {
          padding: 11px 12px !important;
          border-radius: 13px !important;
          min-height: 78px !important;
          background: linear-gradient(180deg, rgba(15,23,42,.90), rgba(15,23,42,.73)) !important;
          border: 1px solid rgba(148,163,184,0.18) !important;
          box-shadow: 0 9px 20px rgba(0,0,0,.21) !important;
        }
        .home-metrics [data-testid="stMetric"] label {
          font-size: 0.73rem !important;
          margin-bottom: 5px !important;
          white-space: normal !important;
          line-height: 1.14 !important;
          letter-spacing: 0.008em;
          color: rgba(229,231,235,.66) !important;
        }
        .home-metrics [data-testid="stMetric"] [data-testid="stMetricValue"] {
          font-size: 1.11rem !important;
          line-height: 1.06 !important;
          white-space: nowrap;
          font-weight: 755 !important;
          color: rgba(248,250,252,.98) !important;
        }
        .home-metrics [data-testid="column"]:nth-child(1) [data-testid="stMetric"] {
          border-color: rgba(56,189,248,0.28) !important;
        }
        .home-metrics [data-testid="column"]:nth-child(2) [data-testid="stMetric"] {
          border-color: rgba(125,211,252,0.18) !important;
        }
        .home-metrics [data-testid="column"]:nth-child(3) [data-testid="stMetric"] {
          border-color: rgba(148,163,184,0.24) !important;
        }
        .home-metrics {
          margin-top: -0.16rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="home-metrics">', unsafe_allow_html=True)
    m1, m2, m3, _msp = st.columns([1.02, 1.02, 1.12, 2.05])
    with m1:
        st.metric("Recommendation", ai_summary["recommendation"])
    with m2:
        st.metric("Confidence", ai_summary.get("confidence") or "")
    with m3:
        st.metric("Weighted Sentiment", f"{ai_summary['avg_sentiment']:.3f}")
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("<div style='margin-top:0.06rem;'><b>Rationale:</b></div>", unsafe_allow_html=True)
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
