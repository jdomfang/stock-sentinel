import html
import json
from pathlib import Path

import pandas as pd
import streamlit as st

# PROJECT ROOT ON sys.path, BEFORE THE FIRST `utils` IMPORT.
#
# Streamlit Cloud can resolve `utils` to an installed site-packages module
# instead of this repo's package. The symptom is not a clean ImportError -- it
# is `KeyError: 'utils'` raised from deep inside the import machinery
# (_find_and_load_unlocked), because the parent package disappears from
# sys.modules midway through loading a submodule.
#
# pages/Discovery.py has carried this guard for exactly that reason; every other
# entrypoint was left exposed, so whichever page a user happened to land on
# first decided whether the app worked. Home is the landing page, so it is the
# one that fails.
from pathlib import Path as _Path
import sys as _sys
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))


from utils.navigation import render_sidebar_navigation, render_top_nav
from utils.ui import apply_theme, close_page, render_recommendation_panel
from utils.deep_analysis import generate_ai_summary


# Verdicts this page is willing to assert. Discovery's evidence floor also emits
# "Single mention", "Limited signal" and "Unscored", and a demo snapshot can
# carry those here -- rendering them as bold bordered pills would present "one
# post said something" exactly like a conclusion, which is what the floor exists
# to prevent. Kept in step with pages/Discovery.py::_ASSERTED.
_ASSERTED = {"bullish", "bearish", "neutral"}


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
    frame = pd.DataFrame(rows) if rows else pd.DataFrame()
    frame.attrs["sector"] = payload.get("sector", "") or ""
    frame.attrs["generated_at"] = payload.get("generated_at", "") or ""
    return frame


def _load_demo_deep(
    preferred_tickers: set[str] | None = None,
) -> tuple[str, str, dict]:
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

    for p in candidates:
        if not p.exists():
            continue
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        ticker = str(payload.get("ticker", "") or "").strip().upper()
        if preferred_tickers and ticker not in preferred_tickers:
            continue
        sector = payload.get("sector", "") or ""
        results = payload.get("analysis_results", {}) or {}
        if ticker and results:
            return ticker, sector, results
    return "", "", {}


def _select_demo_rows(
    frame: pd.DataFrame,
    limit: int = 5,
    selected_ticker: str = "",
) -> list[dict]:
    """Return a small representative preview without changing scan ranking.

    The landing page demonstrates the three Market Scan sentiment states; it
    is not a second results page. Rows retain their source order after we make
    sure the available Bullish, Bearish, and Neutral examples are represented.
    """
    if frame.empty or limit <= 0:
        return []

    records = [
        row for row in frame.to_dict("records")
        if str(row.get("Overall Sentiment", "")).strip().lower() in _ASSERTED
    ]
    if not records:
        return []
    chosen: set[int] = set()
    selected_ticker = str(selected_ticker or "").strip().upper()
    if selected_ticker:
        for index, row in enumerate(records):
            if str(row.get("Ticker", "")).strip().upper() == selected_ticker:
                chosen.add(index)
                break
    for sentiment in ("bullish", "bearish", "neutral"):
        for index, row in enumerate(records):
            if str(row.get("Overall Sentiment", "")).strip().lower() == sentiment:
                chosen.add(index)
                break
    for index in range(len(records)):
        if len(chosen) >= limit:
            break
        chosen.add(index)
    return [records[index] for index in sorted(chosen)[:limit]]


st.set_page_config(
    page_title="Stock Sentinel",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

render_sidebar_navigation()
render_top_nav(active="home")
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
      margin: 0 0 1rem;
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
      .hero { margin: 0 0 1rem; }
    }
    @media (max-width: 640px) {
      .hero { margin: 0 0 1rem; }
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
      min-height: 44px !important;
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
      min-height: 44px !important;
    }

    .st-key-home_card_scan .stButton > button,
    .st-key-home_card_analyze .stButton > button {
      border-radius: 12px !important;
      min-height: 44px !important;
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
    .cap-grid [data-testid="stHorizontalBlock"] {
      flex-wrap: wrap !important;
      gap: 12px !important;
    }

    /* Give Streamlit columns a sane min width so they wrap to 2-up / 1-up naturally */
    .how-grid [data-testid="column"],
    .cap-grid [data-testid="column"] {
      flex: 1 1 260px !important;
      min-width: 260px !important;
    }

    /* On phones, force single-column flow for these sections */
    @media (max-width: 640px) {
      .hero {
        margin: 0 0 1rem;
      }

      .how-grid [data-testid="column"],
      .cap-grid [data-testid="column"] {
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

    /* Release B: compact landing narrative and product walkthrough. */
    .hero { margin: 0 0 1.15rem !important; }
    .hero-title {
      font-size: clamp(2.35rem, 4.6vw, 3.25rem);
      max-width: 820px;
    }
    .ss-proof-strip {
      display:flex;flex-wrap:wrap;gap:7px;margin-bottom:13px;
    }
    .ss-proof-item {
      display:inline-flex;align-items:center;min-height:30px;
      padding:3px 10px;border:1px solid rgba(56,189,248,.18);
      border-radius:999px;background:rgba(56,189,248,.045);
      color:#b9c6d8;font-size:.76rem;font-weight:650;
    }
    .st-key-home_cap_grid [data-testid="stHorizontalBlock"] {
      gap:14px !important;
    }
    .st-key-home_card_scan,
    .st-key-home_card_analyze {
      min-height:168px;height:auto;box-shadow:0 8px 24px rgba(0,0,0,.24);
    }
    .section-title { margin: 1.45rem 0 .28rem; }
    .demo-note { margin:.05rem 0 .85rem;max-width:780px;line-height:1.5; }
    .ss-demo-table-shell {
      border:1px solid rgba(148,163,184,.16);border-radius:14px;
      background:rgba(8,15,30,.72);overflow:hidden;margin:.75rem 0 .8rem;
    }
    .ss-demo-table-head {
      display:flex;justify-content:space-between;gap:16px;align-items:center;
      flex-wrap:wrap;
      padding:12px 16px;border-bottom:1px solid rgba(148,163,184,.14);
    }
    .ss-demo-table-head strong {font-size:.94rem;}
    .ss-demo-table-head span {color:#8192aa;font-size:.76rem;}
    .ss-demo-table {width:100%;border-collapse:collapse;table-layout:fixed;}
    .ss-demo-table th {
      padding:9px 16px;text-align:left;color:#8192aa;font-size:.67rem;
      font-weight:750;letter-spacing:.065em;text-transform:uppercase;
      border-bottom:1px solid rgba(148,163,184,.12);
    }
    .ss-demo-table td {
      padding:11px 16px;border-bottom:1px solid rgba(148,163,184,.09);
      color:#dbe3ee;font-size:.86rem;line-height:1.35;vertical-align:middle;
    }
    .ss-demo-table tr:last-child td {border-bottom:0;}
    .ss-demo-table tr.selected {background:rgba(56,189,248,.055);}
    .ss-demo-table .ticker {font-weight:800;color:#f1f5f9;}
    .ss-demo-table .company {overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
    .ss-sentiment {
      display:inline-flex;padding:3px 8px;border-radius:999px;border:1px solid;
      font-size:.72rem;font-weight:750;
    }
    .ss-sentiment.bullish {color:var(--ss-color-sentiment-bullish);border-color:rgba(56,189,248,.32);background:rgba(56,189,248,.11);}
    .ss-sentiment.bearish {color:var(--ss-color-sentiment-bearish);border-color:rgba(248,113,113,.30);background:rgba(248,113,113,.10);}
    .ss-sentiment.neutral {color:var(--ss-color-sentiment-neutral);border-color:rgba(148,163,184,.28);background:rgba(148,163,184,.10);}
    .ss-demo-selected {
      display:inline-flex;margin-left:7px;padding:2px 6px;border-radius:999px;
      background:rgba(56,189,248,.13);color:var(--ss-color-sentiment-bullish);
      font-size:.61rem;font-weight:750;letter-spacing:.035em;text-transform:uppercase;
    }
    .ss-demo-rule {
      margin:.45rem 0 1rem;padding-left:11px;border-left:2px solid rgba(56,189,248,.46);
      color:#94a3b8;font-size:.8rem;line-height:1.45;
    }
    .st-key-home_signup_cta {
      margin:1.2rem 0 .25rem;padding:16px 18px;border:1px solid rgba(56,189,248,.20);
      border-radius:14px;background:rgba(56,189,248,.045);
    }
    .ss-home-cta {margin-bottom:10px;}
    .ss-home-cta h2 {margin:0;font-size:1.02rem;}
    .ss-home-cta p {margin:4px 0 0;color:#94a3b8;font-size:.84rem;}
    @media (max-width:700px) {
      .hero-title {font-size:clamp(2rem,10vw,2.65rem);}
      .st-key-home_cap_grid [data-testid="stHorizontalBlock"] {flex-wrap:wrap !important;}
      .st-key-home_cap_grid [data-testid="column"] {
        flex:1 1 100% !important;min-width:100% !important;
      }
      .ss-demo-table-head {display:block;}
      .ss-demo-table-head span {display:block;margin-top:3px;}
      .ss-demo-table th:nth-child(2),.ss-demo-table td:nth-child(2) {display:none;}
      .ss-demo-table th,.ss-demo-table td {padding-left:11px;padding-right:11px;}
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
          margin: 0 0 1rem;
        }
        [data-testid="stVerticalBlock"] > [data-testid="stElementContainer"],
        [data-testid="stVerticalBlock"] > [data-testid="stVerticalBlockBorderWrapper"] {
          margin-bottom: 0 !important;
          padding-bottom: 0 !important;
          gap: 0 !important;
        }
        [data-testid="stVerticalBlock"] { gap: 0.55rem !important; }
        .st-key-home_cap_grid { margin-top: 0 !important; }
        @media (max-width: 640px) {
          .clawd-credits-row { flex-direction: column !important; align-items: flex-start !important; }
          .clawd-credits-row > div { width: 100% !important; justify-content: space-between !important; }
        }
        </style>
        """ + f"""
        <div class="clawd-dashboard-hero">
          <h1 style="margin:0;font-size:clamp(32px,3.8vw,2.8rem);font-weight:850;letter-spacing:-0.035em;line-height:1.08;color:rgba(229,231,235,.98);">{_greeting}</h1>
          <div style="color:rgba(148,163,184,.78);font-size:clamp(15px,1.35vw,1.05rem);line-height:1.45;margin-top:5px;">What are you trading today?</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        """
        <div class="hero">
          <div class="ss-proof-strip" aria-label="Product capabilities">
            <span class="ss-proof-item">Recent social sentiment</span>
            <span class="ss-proof-item">Broad US stock coverage</span>
            <span class="ss-proof-item">Evidence context shown</span>
          </div>
          <h1 class="hero-title">Finding short-term opportunities shouldn't feel like a full-time job.</h1>
          <div class="hero-subtitle">We turn noise into signals by analyzing social media sentiment and using market data to validate real momentum.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with st.container(key="home_cap_grid"):
    cap1, cap2 = st.columns(2)

    with cap1:
        with st.container(key="home_card_scan"):
            st.markdown(
                """
                <h2 class="cap-title">Market Scan</h2>
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
                        "Run scan · 1 credit",
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
                <h2 class="cap-title">Analyze a Stock</h2>
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
                        "Analyze · 1 credit",
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

# ─── TWO-MODE SPLIT ────────────────────────────────────────────────────────────
if is_logged_in():
    # ── LOGGED-IN: Dashboard view ──────────────────────────────────────────────
    from utils.auth import get_user
    from utils.supabase_client import get_client

    @st.cache_data(ttl=10, show_spinner=False)
    def _get_credits(uid: str):
        """The merged balance, or None if it genuinely cannot be read.

        Delegates. Home used to carry its own copy of this query, which is
        exactly why the deploy-order fallback in utils/profile.py did nothing
        for the landing page -- and why this page showed no balance and no Buy
        button before the migration was applied.
        """
        from utils.profile import fetch_credits
        try:
            return fetch_credits(uid)
        except Exception:
            # A genuine failure. Return None so the pill is hidden -- but the
            # Buy control below renders regardless, because not knowing the
            # number is a reason to offer a purchase, not to withhold one.
            return None

    user = get_user() or {}
    uid = (user.get("id") if isinstance(user, dict) else getattr(user, "id", None)) or ""

    # BEFORE the balance is read, and above where it renders. app.py captured
    # the ?payment= Stripe redirects back to -- it cannot be read here, because
    # st.switch_page clears query params on the way. The message deliberately
    # does not claim credits have arrived: the webhook that grants them is
    # asynchronous, so the number below may still be the old one.
    from utils import billing
    if st.session_state.get("billing.return"):
        _get_credits.clear()
    billing.render_payment_return()

    credits_c = _get_credits(uid)


    # Credits row. ONE pill, because there is one wallet.
    #
    # The legend lives here and nowhere else. Home is the dashboard, and the pad
    # beside each spend button already states the price on the button itself --
    # repeating "1 credit = 1 scan or 1 analysis" next to every control would be
    # noise in the one place that earns its quiet.
    if credits_c is not None:
        st.markdown(
            f"""
            <div class="clawd-credits-row" style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:0.5rem 0 0.9rem 0;">
              <div style="display:inline-flex;align-items:center;gap:8px;
                background:linear-gradient(135deg,rgba(15,23,42,.92),rgba(2,6,23,.80));
                border:1px solid rgba(56,189,248,.22);border-radius:12px;
                padding:8px 14px;">
                <span style="color:rgba(148,163,184,.80);font-size:0.76rem;font-weight:600;text-transform:uppercase;letter-spacing:0.04em;">Credits</span>
                <span style="color:rgba(56,189,248,.98);font-size:1.20rem;font-weight:800;line-height:1;">{credits_c}</span>
              </div>
              <span style="color:rgba(148,163,184,.70);font-size:0.80rem;">
                1 credit = 1 sector scan <em>or</em> 1 deep analysis
              </span>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # OUTSIDE the `if credits_c is not None` above, deliberately.
    #
    # It used to be inside it, so a balance we could not READ removed the
    # ability to BUY -- the one control that is still correct and still useful
    # when the read fails. That is backwards: not knowing the number is a reason
    # to show the button, not to hide it. The control was previously an inert
    # <span> (cursor:not-allowed, title="Coming soon"); hiding it on a failed
    # read reproduced that dead end by another route.
    #
    # A real widget cannot live inside the markdown blob above, so it renders
    # beneath it. Nothing above moves.
    _bc, _bpad = st.columns([1.1, 3.9])
    with _bc:
        billing.render_buy_credits(key="home")


else:
    # ── LOGGED-OUT: Marketing view ─────────────────────────────────────────────

    df_demo = _load_demo_scan()
    available_demo_tickers = {
        str(row.get("Ticker", "")).strip().upper()
        for row in df_demo.to_dict("records")
        if (
            str(row.get("Ticker", "")).strip()
            and str(row.get("Overall Sentiment", "")).strip().lower()
            in _ASSERTED
        )
    }
    demo_ticker, demo_sector, demo_results = _load_demo_deep(
        preferred_tickers=available_demo_tickers,
    )
    demo_rows = _select_demo_rows(
        df_demo,
        selected_ticker=demo_ticker,
    )
    if demo_rows:
        table_rows = []
        for row in demo_rows:
            raw_ticker = str(row.get("Ticker", "—"))
            ticker_symbol = html.escape(raw_ticker)
            company_name = html.escape(str(row.get("Company Name", "Unavailable")))
            sentiment = str(row.get("Overall Sentiment", "Neutral")).strip().lower()
            sentiment_label = sentiment.title()
            is_selected = bool(
                demo_ticker
                and raw_ticker.strip().upper() == demo_ticker.strip().upper()
            )
            selected_badge = (
                '<span class="ss-demo-selected">Selected</span>'
                if is_selected else ""
            )
            row_class = ' class="selected"' if is_selected else ""
            try:
                last_close_display = f"${float(row.get('Current Price ($)')):,.2f}"
            except (TypeError, ValueError):
                last_close_display = "Unavailable"
            table_rows.append(
                f"<tr{row_class}>"
                f'<td class="ticker">{ticker_symbol}{selected_badge}</td>'
                f'<td class="company" title="{company_name}">{company_name}</td>'
                f"<td>{html.escape(last_close_display)}</td>"
                f'<td><span class="ss-sentiment {sentiment}">{html.escape(sentiment_label)}</span></td>'
                "</tr>"
            )

        st.html(
            f"""
            <section aria-labelledby="demo-flow-heading">
              <h2 class="section-title" id="demo-flow-heading">See the workflow before you sign up</h2>
              <p class="demo-note">A scan narrows the market to stocks worth investigating. Deep Analyze then evaluates one selected ticker and produces the separate action recommendation.</p>
              <div class="ss-demo-table-shell">
                <div class="ss-demo-table-head">
                  <strong>Market Scan preview</strong>
                  <span>{html.escape(str(df_demo.attrs.get("sector") or "Sample").title())} sector · {len(demo_rows)} representative stocks</span>
                </div>
                <table class="ss-demo-table">
                  <caption class="ss-sr-only">Illustrative Market Scan preview; prices are not live</caption>
                  <thead><tr><th style="width:14%">Ticker</th><th>Company</th><th style="width:19%">Last close</th><th style="width:19%">Sentiment</th></tr></thead>
                  <tbody>{''.join(table_rows)}</tbody>
                </table>
              </div>
              <p class="ss-demo-rule"><strong>Illustrative snapshot · prices are not live.</strong> Market Scan reports social sentiment only: Bullish, Bearish, or Neutral. Buy, Watch, or Avoid appears only after Deep Analyze evaluates the selected ticker.</p>
            </section>
            """
        )

    # Demo deep analyze
    if demo_results:
        ai_summary = generate_ai_summary(demo_results)
        demo_mentions = max(
            (int(result.get("mention_count", 0) or 0)
             for result in demo_results.values()),
            default=0,
        )
        st.markdown('<h2 class="section-title">Selected example: Deep Analyze</h2>', unsafe_allow_html=True)
        st.markdown(
            '<p class="demo-note">This illustrative snapshot shows the decision summary a completed one-credit analysis produces.</p>',
            unsafe_allow_html=True,
        )
        render_recommendation_panel(
            ticker=demo_ticker or "NVDA",
            sector=demo_sector or "tech",
            ai_summary=ai_summary,
            mentions=demo_mentions,
            evidence_label=f"{demo_mentions} public posts" if demo_mentions else "Illustrative evidence",
            freshness="Illustrative demo snapshot",
        )

    # CTA
    with st.container(key="home_signup_cta"):
        st.markdown(
            '<div class="ss-home-cta"><h2>Ready to investigate your own ticker?</h2>'
            '<p>Create an account, receive two free credits, and choose a market scan or deep analysis.</p></div>',
            unsafe_allow_html=True,
        )
        if st.button("Create free account", type="primary", use_container_width=False):
            st.session_state["auth_initial_mode"] = "Create Account"
            st.switch_page("pages/Auth.py")
        # True again, and only by arithmetic: a new account starts with 2 credits
        # and a pack is 2 credits for $5. It was FALSE for the length of time the
        # pack was going to be 10-for-$5 -- the same two free credits would have
        # been $1 of value. Restate it in credits if the pack size ever moves.
        st.caption("No card required · Includes 2 free credits ($5.00 value)")

close_page()
