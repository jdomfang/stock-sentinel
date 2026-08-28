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
from utils.ui import apply_theme, close_page
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


def _marketing_preview_html(
    rows: list[dict], ticker: str, summary: dict, sector: str,
) -> str:
    """Render the v4 product preview from saved, nonpaying demo data."""
    attention_fallback = (27, 16, 12)
    preview_rows = []
    for index, row in enumerate(rows[:3]):
        raw_ticker = str(row.get("Ticker") or "—").strip().upper()
        sentiment = str(
            row.get("Overall Sentiment") or "Neutral"
        ).strip().lower()
        if sentiment not in _ASSERTED:
            sentiment = "neutral"
        try:
            attention = int(row.get("Mentions") or 0)
        except (TypeError, ValueError):
            attention = 0
        if attention <= 0:
            attention = attention_fallback[index]
        selected = " selected" if raw_ticker == ticker else ""
        preview_rows.append(
            f'<div class="ss-hero-preview-row{selected}">'
            f'<strong>{html.escape(raw_ticker)}</strong>'
            f'<span class="ss-sentiment {sentiment}">{sentiment.title()}</span>'
            f'<span>{attention} mentions</span></div>'
        )

    recommendation = html.escape(
        str(summary.get("recommendation") or "Watch")
    )
    confidence = html.escape(str(summary.get("confidence") or "Moderate"))
    reasons = [
        str(reason).strip()
        for reason in (summary.get("rationale") or [])
        if str(reason).strip()
    ]
    reason = html.escape(
        reasons[0] if reasons else
        "Evidence is mixed; inspect the supporting signals before acting."
    )
    result_ticker = html.escape(ticker or "NVDA")
    result_class = recommendation.lower()
    if result_class not in {"buy", "watch", "avoid"}:
        result_class = "watch"
    return f"""
      <section class="ss-hero-preview" aria-label="Illustrative product preview">
        <div class="ss-hero-preview-kicker">Product preview · illustrative</div>
        <div class="ss-hero-preview-head">
          <h2>Market Scan</h2>
          <span>{html.escape(str(sector or 'Technology').title())} · illustrative</span>
        </div>
        <div class="ss-hero-preview-columns" aria-hidden="true">
          <span>Stock</span><span>Sentiment</span><span>Attention</span>
        </div>
        <div class="ss-hero-preview-rows">{''.join(preview_rows)}</div>
        <div class="ss-hero-result">
          <div class="ss-hero-preview-kicker">After Deep Analyze</div>
          <div class="ss-hero-result-line">
            <strong>{result_ticker}</strong>
            <span class="{result_class}">{recommendation}</span>
            <span>{confidence} confidence</span>
          </div>
          <p>{reason}</p>
          <span class="ss-preview-note">Evidence preview</span>
        </div>
      </section>
    """


st.set_page_config(
    page_title="Stock Sentinel",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

apply_theme()
render_sidebar_navigation()
render_top_nav()
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

    /* Public story uses the approved broad marketing canvas. Signed-in cards
       still establish their own readable widths inside it. */
    div[data-testid="stMainBlockContainer"] {
      max-width: var(--ss-marketing-max-width);
      margin: 0 auto;
      padding-left: clamp(16px, 3vw, 32px);
      padding-right: clamp(16px, 3vw, 32px);
      padding-top: 0.25rem;
    }

    .discovery-wrapper {
      max-width: var(--ss-marketing-max-width);
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
      background: linear-gradient(180deg, var(--ss-color-action), var(--ss-color-action-rest-end)) !important;
      background-color: var(--ss-color-action) !important;
      border: 1px solid rgba(56,189,248,.45) !important;
      color: #001018 !important;
      font-weight: 650 !important;
      padding: 0.25rem 0.65rem !important;
      font-size: 0.85rem !important;
      min-height: 44px !important;
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
    .how-grid [data-testid="stColumn"],
    .cap-grid [data-testid="stColumn"] {
      flex: 1 1 260px !important;
      min-width: 260px !important;
    }

    /* On phones, force single-column flow for these sections */
    @media (max-width: 640px) {
      .hero {
        margin: 0 0 1rem;
      }

      .how-grid [data-testid="stColumn"],
      .cap-grid [data-testid="stColumn"] {
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
    .st-key-home_public_hero [data-testid="stHorizontalBlock"]:has(
      .st-key-home_public_story
    ):has(.st-key-home_public_preview) {
      align-items:center!important;gap:clamp(24px,5vw,64px)!important;
      flex-wrap:nowrap!important;
    }
    .st-key-home_public_hero [data-testid="stHorizontalBlock"]:has(
      .st-key-home_public_story
    ):has(.st-key-home_public_preview) > [data-testid="stColumn"] {
      min-width:0!important;width:0!important;
    }
    .st-key-home_public_hero [data-testid="stHorizontalBlock"]:has(
      .st-key-home_public_story
    ):has(.st-key-home_public_preview) > [data-testid="stColumn"]:first-child {
      flex:1.04 1 0!important;
    }
    .st-key-home_public_hero [data-testid="stHorizontalBlock"]:has(
      .st-key-home_public_story
    ):has(.st-key-home_public_preview) > [data-testid="stColumn"]:last-child {
      flex:.96 1 0!important;
    }
    .ss-public-hero-copy {padding:clamp(1rem,3vw,2.2rem) 0;}
    .ss-public-hero-copy .ss-proof-strip {margin-bottom:1.15rem;}
    .ss-public-hero-copy h1 {
      margin:0;color:var(--text);font-size:clamp(2.75rem,4.2vw,3.8rem);
      font-weight:850;letter-spacing:-.045em;line-height:1.02;
    }
    .ss-public-hero-copy > p {
      max-width:590px;margin:1rem 0 0;color:var(--muted);
      font-size:clamp(1rem,1.5vw,1.15rem);line-height:1.55;
    }
    .st-key-home_public_ctas {margin-top:1.35rem;max-width:520px;}
    .st-key-home_public_ctas [data-testid="stHorizontalBlock"] {gap:12px!important;}
    .st-key-home_public_ctas .stButton > button {min-height:50px!important;}
    .st-key-home_open_scan_link [data-testid="stPageLink"] a,
    .st-key-home_open_deep_link [data-testid="stPageLink"] a {
      min-height:50px;display:flex;align-items:center;justify-content:center;
      border-radius:10px;font-weight:740;text-decoration:none!important;
      width:100%;box-sizing:border-box;
    }
    .st-key-home_open_scan_link [data-testid="stPageLink"] a {
      color:#f8fafc!important;border:1px solid rgba(56,189,248,.55);
      background:linear-gradient(180deg,#35b7e7,#0e8fb9)!important;
    }
    .st-key-home_open_deep_link [data-testid="stPageLink"] a {
      color:#dbe3ee!important;border:1px solid rgba(148,163,184,.26);
      background:rgba(15,23,42,.46)!important;
    }
    .ss-public-caveat {margin-top:.65rem;color:#8192aa;font-size:.78rem;}
    .ss-hero-preview {
      border:1px solid rgba(56,189,248,.25);border-radius:18px;
      padding:18px;background:linear-gradient(145deg,rgba(8,20,39,.98),rgba(8,15,30,.96));
      box-shadow:var(--ss-shadow-focus-panel);min-height:420px;
    }
    .ss-hero-preview-kicker {
      color:var(--accent);font-size:.67rem;font-weight:780;
      letter-spacing:.1em;text-transform:uppercase;
    }
    .ss-hero-preview-head {
      display:flex;justify-content:space-between;align-items:baseline;
      gap:16px;margin:.55rem 0 .7rem;
    }
    .ss-hero-preview-head h2 {margin:0;font-size:1.35rem;}
    .ss-hero-preview-head > span {color:#94a3b8;font-size:.73rem;}
    .ss-hero-preview-columns,.ss-hero-preview-row {
      display:grid;grid-template-columns:.8fr 1fr 1fr;align-items:center;gap:8px;
    }
    .ss-hero-preview-columns {
      padding:8px 12px;color:#8192aa;font-size:.68rem;
      border:1px solid rgba(148,163,184,.14);border-bottom:0;
      border-radius:10px 10px 0 0;
    }
    .ss-hero-preview-row {
      position:relative;padding:11px 12px;border:1px solid rgba(148,163,184,.14);
      border-top:0;color:#dbe3ee;font-size:.82rem;
    }
    .ss-hero-preview-row:last-child {border-radius:0 0 10px 10px;}
    .ss-hero-preview-row.selected {background:rgba(56,189,248,.055);}
    .ss-hero-preview-row.selected:before {
      content:"";position:absolute;inset:0 auto 0 -1px;width:2px;background:var(--accent);
    }
    .ss-hero-preview-row > span:last-child {text-align:right;color:#cbd5e1;}
    .ss-hero-result {
      margin-top:18px;padding-top:16px;border-top:1px solid rgba(148,163,184,.16);
    }
    .ss-hero-result-line {display:flex;align-items:baseline;gap:11px;margin:.55rem 0;}
    .ss-hero-result-line strong {font-size:1.35rem;}
    .ss-hero-result-line .buy {color:var(--ss-color-recommendation-buy);font-weight:800;}
    .ss-hero-result-line .watch {color:var(--ss-color-recommendation-watch);font-weight:800;}
    .ss-hero-result-line .avoid {color:var(--ss-color-recommendation-avoid);font-weight:800;}
    .ss-hero-result-line span:last-child {color:#cbd5e1;font-size:.8rem;}
    .ss-hero-result p {margin:.45rem 0;color:#a8b5c7;font-size:.86rem;line-height:1.5;}
    .ss-preview-note {
      display:block;margin-top:.8rem;color:#8192aa;font-size:.74rem;
      font-weight:700;letter-spacing:.055em;text-transform:uppercase;
    }
    .ss-workflow-section {margin:2rem 0 0;padding-top:1.65rem;border-top:1px solid rgba(148,163,184,.14);}
    .ss-workflow-section h2 {margin:0 0 1.25rem;text-align:center;font-size:1.15rem;}
    .ss-workflow-grid {display:grid;grid-template-columns:repeat(3,1fr);}
    .ss-workflow-step {display:grid;grid-template-columns:44px 1fr;gap:12px;padding:0 24px;}
    .ss-workflow-step + .ss-workflow-step {border-left:1px solid rgba(148,163,184,.18);}
    .ss-workflow-number {
      display:flex;align-items:center;justify-content:center;width:42px;height:42px;
      border:1px solid rgba(56,189,248,.38);border-radius:999px;
      color:var(--accent);font-size:1.15rem;font-weight:780;
    }
    .ss-workflow-step h3 {margin:.1rem 0 .2rem;font-size:.94rem;}
    .ss-workflow-step p {margin:0;color:#94a3b8;font-size:.78rem;line-height:1.45;}
    .ss-trust-strip {
      display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:1.7rem 0 .3rem;
      padding:14px;border:1px solid rgba(148,163,184,.16);border-radius:13px;
      background:rgba(8,15,30,.54);
    }
    .ss-trust-strip span {text-align:center;color:#a8b5c7;font-size:.78rem;}
    @media (max-width:900px) {
      .st-key-home_public_hero [data-testid="stHorizontalBlock"]:has(
        .st-key-home_public_story
      ):has(.st-key-home_public_preview) {flex-wrap:wrap!important;}
      .st-key-home_public_hero [data-testid="stHorizontalBlock"]:has(
        .st-key-home_public_story
      ):has(.st-key-home_public_preview) > [data-testid="stColumn"] {
        flex:1 1 100%!important;min-width:100%!important;width:100%!important;
      }
      .ss-hero-preview {min-height:0;margin-top:.6rem;}
    }
    @media (max-width:520px) {
      .st-key-home_public_ctas [data-testid="stHorizontalBlock"] {
        flex-wrap:wrap!important;
      }
      .st-key-home_public_ctas [data-testid="stColumn"] {
        flex:1 1 100%!important;min-width:100%!important;width:100%!important;
      }
      .st-key-home_public_ctas button {
        width:100%!important;min-height:50px!important;
      }
    }
    @media (max-width:700px) {
      .hero-title {font-size:clamp(2rem,10vw,2.65rem);}
      .ss-demo-table-head {display:block;}
      .ss-demo-table-head span {display:block;margin-top:3px;}
      .ss-demo-table th:nth-child(2),.ss-demo-table td:nth-child(2) {display:none;}
      .ss-demo-table th,.ss-demo-table td {padding-left:11px;padding-right:11px;}
      .ss-public-hero-copy {padding:.5rem 0;}
      .ss-public-hero-copy h1 {font-size:clamp(2.45rem,12vw,3.35rem);}
      .ss-workflow-grid,.ss-trust-strip {grid-template-columns:1fr;}
      .ss-workflow-step {padding:14px 4px;}
      .ss-workflow-step + .ss-workflow-step {border-left:0;border-top:1px solid rgba(148,163,184,.14);}
      .ss-trust-strip span {padding:5px 0;text-align:left;}
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# --- Public product story. Authentication only changes the primary action. ---
from utils.auth import is_logged_in

_logged_in = is_logged_in()
_demo_frame = _load_demo_scan()
_demo_available = {
    str(row.get("Ticker") or "").strip().upper()
    for row in _demo_frame.to_dict("records")
    if str(row.get("Overall Sentiment") or "").strip().lower() in _ASSERTED
}
_demo_ticker, _demo_sector, _demo_results = _load_demo_deep(
    preferred_tickers=_demo_available,
)
_demo_rows = _select_demo_rows(
    _demo_frame, limit=3, selected_ticker=_demo_ticker,
)
_demo_summary = generate_ai_summary(_demo_results) if _demo_results else {}

with st.container(key="home_public_hero"):
    story_col, preview_col = st.columns([1.04, .96])
    with story_col.container(key="home_public_story"):
        st.html(
            """
            <div class="ss-public-hero-copy">
              <div class="ss-proof-strip" aria-label="Product capabilities">
                <span class="ss-proof-item">4,000+ US stocks</span>
                <span class="ss-proof-item">Freshness shown</span>
                <span class="ss-proof-item">Evidence explained</span>
              </div>
              <h1>Finding short-term opportunities shouldn't feel like a full-time job.</h1>
              <p>Stock Sentinel turns social chatter into a ranked sentiment shortlist, then validates a selected ticker with market data.</p>
            </div>
            """
        )
        with st.container(key="home_public_ctas"):
            start_col, explain_col = st.columns([1.18, .82])
            with start_col:
                if _logged_in:
                    with st.container(key="home_open_scan_link"):
                        st.page_link(
                            "pages/Discovery.py", label="Open Market Scan",
                            use_container_width=True,
                        )
                elif st.button(
                    "Start with 2 free credits", type="primary",
                    key="home_start_free", use_container_width=True,
                ):
                    st.session_state["auth_initial_mode"] = "Create Account"
                    st.session_state["_after_auth_page"] = "Discovery"
                    st.switch_page("pages/Auth.py")
            with explain_col:
                with st.container(key="home_analyze_link"):
                    if _logged_in:
                        with st.container(key="home_open_deep_link"):
                            st.page_link(
                                "pages/Deep_Analysis.py",
                                label="Open Deep Analyze",
                                use_container_width=True,
                            )
                    elif st.button(
                        "Analyze a ticker", key="home_start_deep",
                        use_container_width=True,
                    ):
                        st.session_state["auth_initial_mode"] = "Sign In"
                        st.session_state["_after_auth_page"] = "Deep_Analysis"
                        st.switch_page("pages/Auth.py")
        caveat = (
            "Signed in · Open the workspace when you are ready."
            if _logged_in else
            "No card required · 1 credit per scan or analysis<br>"
            "Research support, not financial advice."
        )
        st.markdown(
            f'<div class="ss-public-caveat">{caveat}</div>',
            unsafe_allow_html=True,
        )
    with preview_col.container(key="home_public_preview"):
        st.html(
            _marketing_preview_html(
                _demo_rows,
                _demo_ticker or "NVDA",
                _demo_summary,
                _demo_sector or _demo_frame.attrs.get("sector") or "tech",
            )
        )

st.markdown("<div style='height:.1rem'></div>", unsafe_allow_html=True)

# The preview already proves the product. The remaining landing content is a
# short process explanation and transaction-trust strip for every visitor.
st.html(
    """
    <section class="ss-workflow-section" id="how-it-works" aria-labelledby="how-heading">
      <h2 id="how-heading">From market noise to a clearer next step</h2>
      <div class="ss-workflow-grid">
        <article class="ss-workflow-step">
          <span class="ss-workflow-number">1</span>
          <div><h3>Scan a sector</h3><p>Find unusual social attention.</p></div>
        </article>
        <article class="ss-workflow-step">
          <span class="ss-workflow-number">2</span>
          <div><h3>Analyze a ticker</h3><p>Review catalysts, risks, and confidence.</p></div>
        </article>
        <article class="ss-workflow-step">
          <span class="ss-workflow-number">3</span>
          <div><h3>Inspect the evidence</h3><p>See what supports the result and what could change it.</p></div>
        </article>
      </div>
    </section>
    <aside class="ss-trust-strip" aria-label="Credit and purchase assurances">
      <span>Costs shown before every action</span>
      <span>Eligible failed runs are automatically refunded</span>
      <span>Credits never expire</span>
    </aside>
    """
)

close_page()
