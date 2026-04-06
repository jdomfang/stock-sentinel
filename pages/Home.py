import json
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from utils.navigation import render_sidebar_navigation, render_top_nav
from utils.ui import apply_theme, close_page, render_recommendation_panel
from utils.deep_analysis import generate_ai_summary


def _sentiment_pill(label: str) -> str:
    label = (label or "").strip()
    if label.lower() == "bullish":
        return '<span style="background:rgba(56,189,248,.18);color:rgba(56,189,248,.98);border:1px solid rgba(56,189,248,.35);padding:3px 10px;border-radius:999px;font-size:0.83rem;font-weight:700;">Bullish</span>'
    elif label.lower() == "bearish":
        return '<span style="background:rgba(239,68,68,.15);color:rgba(248,113,113,.98);border:1px solid rgba(239,68,68,.30);padding:3px 10px;border-radius:999px;font-size:0.83rem;font-weight:700;">Bearish</span>'
    else:
        return f'<span style="background:rgba(148,163,184,.12);color:rgba(148,163,184,.92);border:1px solid rgba(148,163,184,.25);padding:3px 10px;border-radius:999px;font-size:0.83rem;font-weight:700;">{label or "Neutral"}</span>'


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
from utils.auth import flush_pending_rt_save; flush_pending_rt_save()

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
      margin: -9.8rem 0 2px 0;
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

    /* Mobile: remove aggressive negative hero offset (header is different on phones) */
    /* iPad / tablet */
    @media (min-width: 641px) and (max-width: 1024px) {
      .hero { margin: -11.2rem 0 2px 0; }
    }
    @media (max-width: 640px) {
      .hero { margin: -6.0rem 0 10px 0; }
      .hero-title { font-size: clamp(34px, 9.5vw, 44px); }
      .hero-subtitle { font-size: 1.00rem; }

      /* Fix overlap in the demo section: section title + demo-note were both using negative margins */
      .section-title {
        margin: 0.25rem 0 0.45rem 0;
        font-size: 1.15rem;
      }
      .demo-note {
        margin-top: 0;
        margin-bottom: 10px;
      }
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
      /* Constrain to card inner width (card has 15px padding each side = 30px total) */
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

    /* Mobile: let the select/input + button stack so they don't overflow the card */
    @media (max-width: 640px) {
      .st-key-home_card_scan_actions [data-testid="stHorizontalBlock"],
      .st-key-home_card_analyze_actions [data-testid="stHorizontalBlock"] {
        flex-wrap: wrap !important;
        align-items: stretch !important;
      }

      .st-key-home_card_scan_actions [data-testid="column"],
      .st-key-home_card_analyze_actions [data-testid="column"] {
        flex: 1 1 100% !important;
        min-width: 100% !important;
      }

      .st-key-home_card_scan [data-baseweb="select"],
      .st-key-home_card_analyze [data-baseweb="input"] {
        width: 100% !important;
        max-width: 100% !important;
      }

      .st-key-home_card_scan .stButton > button,
      .st-key-home_card_analyze .stButton > button {
        width: 100% !important;
      }
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
        margin: -6.0rem 0 14px 0;
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

# --- Hero: two-mode (JS dropdown fix merged in to avoid extra filler block) ---
from utils.auth import is_logged_in

if is_logged_in():
    from utils.auth import get_user
    _user = get_user() or {}
    _meta = (_user.get("user_metadata") if isinstance(_user, dict) else getattr(_user, "user_metadata", None)) or {}
    _email = (_user.get("email") if isinstance(_user, dict) else getattr(_user, "email", None)) or ""
    _prefix = _email.split("@")[0] if _email else ""
    _first = _prefix.replace(".", " ").replace("_", " ").split()[0].title() if _prefix else ""
    _display = (
        _meta.get("full_name") or _meta.get("name") or _meta.get("first_name") or _first
    )
    _greeting = f"Welcome back, {_display}." if _display else "Welcome back."

    st.markdown(
        """
        <style>
        .clawd-dashboard-hero {
          margin: -9.2rem 0 0.4rem 0;
        }
        @media (min-width: 641px) and (max-width: 1024px) {
          /* iPad: nav is ~52px, sits at ~80px from top — pull greeting up gently */
          .clawd-dashboard-hero { margin: -7.0rem 0 0.4rem 0; }
        }
        @media (max-width: 640px) {
          .clawd-dashboard-hero { margin: 0.8rem 0 0 0 !important; }
          .st-key-home_cap_grid { margin-top: 0 !important; }
          [data-testid="stVerticalBlock"] { gap: 0.5rem !important; }
        }
        [data-testid="stVerticalBlock"] > [data-testid="stElementContainer"],
        [data-testid="stVerticalBlock"] > [data-testid="stVerticalBlockBorderWrapper"] {
          margin-bottom: 0 !important;
          padding-bottom: 0 !important;
          gap: 0 !important;
        }
        [data-testid="stVerticalBlock"] { gap: 0.55rem !important; }
        .st-key-home_cap_grid { margin-top: -2.8rem !important; }
        @media (max-width: 640px) {
          .clawd-credits-row { flex-direction: column !important; align-items: flex-start !important; }
          .clawd-credits-row > div { width: 100% !important; justify-content: space-between !important; }
        }
        </style>
        """ + f"""
        <div class="clawd-dashboard-hero">
          <div style="font-size:clamp(32px,3.8vw,2.8rem);font-weight:850;letter-spacing:-0.035em;line-height:1.08;color:rgba(229,231,235,.98);">{_greeting}</div>
          <div style="color:rgba(148,163,184,.78);font-size:clamp(15px,1.35vw,1.05rem);line-height:1.45;margin-top:5px;">What are you trading today?</div>
        </div>
        <script>
        (function () {{
          const APPLY_TO = (doc) => {{
            const ul = doc.querySelector('ul[data-testid="stSelectboxVirtualDropdown"]');
            if (ul) {{
              ul.style.setProperty('background-color', '#0F172A', 'important');
              ul.style.setProperty('color', '#E5E7EB', 'important');
              ul.querySelectorAll('li, li *').forEach((el) => {{
                el.style.setProperty('color', '#E5E7EB', 'important');
                el.style.setProperty('opacity', '1', 'important');
              }});
            }}
          }};
          const obs = new MutationObserver(() => {{ try {{ APPLY_TO(document); }} catch(e){{}} }});
          obs.observe(document.documentElement, {{ childList: true, subtree: true }});
          setTimeout(() => {{ try {{ APPLY_TO(document); }} catch(e){{}} }}, 500);
        }})();
        </script>
        """,
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        """
        <div class="hero">
          <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px;">
            <span style="display:inline-flex;align-items:center;gap:6px;background:rgba(56,189,248,.06);border:1px solid rgba(56,189,248,.18);border-radius:999px;padding:5px 12px;font-size:0.80rem;font-weight:600;color:rgba(229,231,235,.80);">📡 Real-time social sentiment</span>
            <span style="display:inline-flex;align-items:center;gap:6px;background:rgba(56,189,248,.06);border:1px solid rgba(56,189,248,.18);border-radius:999px;padding:5px 12px;font-size:0.80rem;font-weight:600;color:rgba(229,231,235,.80);">🏦 Thousands of US stocks</span>
            <span style="display:inline-flex;align-items:center;gap:6px;background:rgba(56,189,248,.06);border:1px solid rgba(56,189,248,.18);border-radius:999px;padding:5px 12px;font-size:0.80rem;font-weight:600;color:rgba(229,231,235,.80);">⚡ Signal in under 60 seconds</span>
          </div>
          <div class="hero-title">Finding short-term opportunities shouldn't feel like a full-time job.</div>
          <div class="hero-subtitle">We turn noise into signals by analyzing social media sentiment and using market data to validate real momentum.</div>
        </div>
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
          const obs = new MutationObserver(() => { try { APPLY_TO(document); } catch(e){} });
          obs.observe(document.documentElement, { childList: true, subtree: true });
          setTimeout(() => { try { APPLY_TO(document); } catch(e){} }, 500);
        })();
        </script>
        """,
        unsafe_allow_html=True,
    )

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
                        "Deep Analyze",
                        type="primary",
                        key="home_cap_analyze",
                        use_container_width=True,
                    ):
                        ticker = (analyze_ticker or "").strip().upper()
                        if ticker:
                            st.session_state["prefill_deep_ticker"] = ticker
                            st.session_state["_autorun_deep_analysis"] = True
                        st.session_state["_after_auth_page"] = "Deep_Analysis"

                        st.switch_page("pages/Deep_Analysis.py" if is_logged_in() else "pages/Auth.py")

st.markdown("<div style='height: 0.1rem;'></div>", unsafe_allow_html=True)

# Mobile spacer — pushes cards below greeting on iPhone (CSS margin unreliable in Streamlit)
if is_logged_in():
    st.markdown(
        """<div style="display:none" class="mobile-spacer">
        <style>
        @media (max-width: 640px) {
          .mobile-spacer { display: block !important; height: 5rem; }
        }
        </style>
        </div>""",
        unsafe_allow_html=True,
    )

# ─── TWO-MODE SPLIT ────────────────────────────────────────────────────────────
if is_logged_in():
    # ── LOGGED-IN: Dashboard view ──────────────────────────────────────────────
    from utils.auth import get_user
    from utils.supabase_client import get_client

    @st.cache_data(ttl=10, show_spinner=False)
    def _get_credits(uid: str):
        try:
            sb = get_client()
            resp = sb.table("profiles").select("scan_credits,deep_credits").eq("user_id", uid).maybe_single().execute()
            data = getattr(resp, "data", None) or {}
            return int(data.get("scan_credits") or 0), int(data.get("deep_credits") or 0)
        except Exception:
            return None, None

    user = get_user() or {}
    uid = (user.get("id") if isinstance(user, dict) else getattr(user, "id", None)) or ""
    scan_c, deep_c = _get_credits(uid)



    # Credits row
    if scan_c is not None:
        st.markdown(
            f"""
            <div class="clawd-credits-row" style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:0.5rem 0 0.9rem 0;">
              <div style="display:inline-flex;align-items:center;gap:8px;
                background:linear-gradient(135deg,rgba(15,23,42,.92),rgba(2,6,23,.80));
                border:1px solid rgba(56,189,248,.22);border-radius:12px;
                padding:8px 14px;">
                <span style="color:rgba(148,163,184,.80);font-size:0.76rem;font-weight:600;text-transform:uppercase;letter-spacing:0.04em;">Scan Credits</span>
                <span style="color:rgba(56,189,248,.98);font-size:1.20rem;font-weight:800;line-height:1;">{scan_c}</span>
              </div>
              <div style="display:inline-flex;align-items:center;gap:8px;
                background:linear-gradient(135deg,rgba(15,23,42,.92),rgba(2,6,23,.80));
                border:1px solid rgba(56,189,248,.22);border-radius:12px;
                padding:8px 14px;">
                <span style="color:rgba(148,163,184,.80);font-size:0.76rem;font-weight:600;text-transform:uppercase;letter-spacing:0.04em;">Analyze Credits</span>
                <span style="color:rgba(56,189,248,.98);font-size:1.20rem;font-weight:800;line-height:1;">{deep_c}</span>
              </div>
              <span style="
                color:rgba(148,163,184,.35);font-size:0.80rem;font-weight:600;
                cursor:not-allowed;margin-left:2px;"
                title="Coming soon">
                + Buy Credits
              </span>
            </div>
            """,
            unsafe_allow_html=True,
        )

else:
    # ── LOGGED-OUT: Marketing view ─────────────────────────────────────────────

    # Demo scan table
    st.markdown('<div id="demo-scan" style="margin-top:-1.12rem;"></div>', unsafe_allow_html=True)

    if st.session_state.pop("_scroll_demo", False):
        components.html(
            """<script>
              const el = window.parent.document.getElementById('demo-scan');
              if (el) el.scrollIntoView({behavior: 'smooth', block: 'start'});
            </script>""",
            height=0,
        )

    df_demo = _load_demo_scan()
    if not df_demo.empty:
        st.markdown('<div class="section-title">Sample scan results <span style="color: rgba(229,231,235,.65); font-weight: 700;">(demo)</span></div>', unsafe_allow_html=True)
        st.markdown('<div class="demo-note">Shortlist for action: This table ranks candidates worth a closer look. If a ticker stands out, click Deep Analyze to see catalysts, red flags, and guidance.</div>', unsafe_allow_html=True)

        st.markdown('<div class="demo-header">', unsafe_allow_html=True)
        header_cols = st.columns([1.1, 1.8, 1.2, 1.1, 1.0])
        for col, label in zip(header_cols, ["Ticker", "Company", "Last Close", "Overall", "Deep Analyze"]):
            col.markdown(f"**{label}**")
        st.markdown('</div>', unsafe_allow_html=True)

        for _, row in df_demo.iterrows():
            ticker_symbol = row.get("Ticker", "")
            last_close = row.get("Current Price ($)", None)
            try:
                last_close_display = f"${float(last_close):.2f}"
            except (TypeError, ValueError):
                last_close_display = "N/A"
            st.markdown("<div class='ticker-row'>", unsafe_allow_html=True)
            col1, col2, col3, col4, col5 = st.columns([1.1, 1.8, 1.2, 1.1, 1.0])
            with col1: st.markdown(f"**{ticker_symbol}**")
            with col2: st.markdown(row.get("Company Name", ""))
            with col3: st.markdown(last_close_display)
            with col4: st.markdown(_sentiment_pill(row.get("Overall Sentiment", "")), unsafe_allow_html=True)
            with col5: st.button("Deep Analyze", key=f"home_deep_{ticker_symbol}", disabled=True)
            st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div style='height: 0.45rem;'></div>", unsafe_allow_html=True)

    # Demo deep analyze
    demo_ticker, demo_sector, demo_results = _load_demo_deep()
    if demo_results:
        ai_summary = generate_ai_summary(demo_results)
        st.markdown('<div class="section-title">Deep Analyze <span style="color:rgba(229,231,235,.65);font-weight:700;">(demo)</span></div>', unsafe_allow_html=True)
        st.markdown(
            '<div style="color:rgba(229,231,235,.70);font-size:0.92rem;line-height:1.42;margin:-0.4rem 0 0.6rem 0;max-width:820px;">'
            'Deep Analyze turns the data into a clear recommendation (Buy / Watch / Avoid), confidence, and the key reasons.'
            '</div>', unsafe_allow_html=True,
        )
        render_recommendation_panel(ticker=demo_ticker or "NVDA", sector=demo_sector or "tech", ai_summary=ai_summary)

    st.markdown("<div style='height: 1.25rem;'></div>", unsafe_allow_html=True)

    # CTA
    if st.button("Run your scan", type="primary", use_container_width=False):
        st.switch_page("pages/Auth.py")
    st.caption("Includes $5.00 in free credits to get started.")

close_page()
