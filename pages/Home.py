import html
import json
import logging
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
from utils.demo_snapshots import social_posts_value
from utils.demo_repository import load_latest_public_demo


logger = logging.getLogger(__name__)


# Verdicts this page is willing to assert. Discovery's evidence floor also emits
# "Single mention", "Limited signal" and "Unscored", and a demo snapshot can
# carry those here -- rendering them as bold bordered pills would present "one
# post said something" exactly like a conclusion, which is what the floor exists
# to prevent. Kept in step with pages/Discovery.py::_ASSERTED.
_ASSERTED = {"bullish", "bearish", "neutral"}


def _load_demo_scan() -> pd.DataFrame:
    """Load the checked-in Scan fallback.

    Priority:
      1) data/education/scan_latest.json (legacy emergency fallback)
      2) data/demo/scan_tech.json (checked-in emergency fallback)
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
    """Load the checked-in Deep Analyze fallback (no API calls).

    Priority:
      1) data/education/deep_latest.json (legacy emergency fallback)
      2) data/demo/deep_NVDA_tech.json (checked-in emergency fallback)
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


@st.cache_data(ttl=60, show_spinner=False)
def _load_published_demo() -> dict | None:
    """Read the durable publication without making Home depend on Streamlit."""
    try:
        return load_latest_public_demo()
    except Exception:
        # Marketing must remain available during a Supabase outage. The local
        # files below are an explicit emergency fallback, never a write target.
        logger.warning("durable public demo unavailable; using local fallback",
                       exc_info=True)
        return None


def _scan_frame_from_bundle(bundle: dict) -> pd.DataFrame:
    scan = bundle.get("scan") or {}
    rows = scan.get("validated_rows") or []
    frame = pd.DataFrame(rows) if rows else pd.DataFrame()
    frame.attrs["sector"] = scan.get("sector") or ""
    frame.attrs["generated_at"] = scan.get("generated_at") or ""
    return frame


def _deep_from_bundle(bundle: dict) -> tuple[str, str, dict]:
    analysis = bundle.get("deep_analysis") or {}
    return (
        str(analysis.get("ticker") or "").strip().upper(),
        str(analysis.get("sector") or "").strip(),
        analysis.get("public_card") or {},
    )


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


def _legacy_public_card(
    results: dict, ticker: str, sector: str,
) -> dict:
    """Adapt checked-in v1 fallback files without inventing rich metrics."""
    summary = generate_ai_summary(results) if results else {}
    rationale = [
        str(reason).strip()
        for reason in (summary.get("rationale") or [])
        if str(reason).strip()
    ]
    return {
        "ticker": ticker,
        "sector": sector,
        "verdict": summary.get("recommendation") or "Watch",
        "confidence": summary.get("confidence") or "Low",
        "reason": (
            rationale[0] if rationale else
            "Evidence is mixed; inspect the supporting signals before acting."
        ),
        "would_change": [],
        "tiles": [],
        "evidence": {},
        "movement": {},
    }


def _decision_workspace_html(
    rows: list[dict], ticker: str, card: dict, sector: str,
) -> str:
    """Render a rich, unified preview from the canonical public card."""
    preview_rows = []
    for row in rows[:5]:
        raw_ticker = str(row.get("Ticker") or "—").strip().upper()
        company = str(row.get("Company Name") or "").strip()
        sentiment = str(
            row.get("Overall Sentiment") or "Neutral"
        ).strip().lower()
        if sentiment not in _ASSERTED:
            sentiment = "neutral"
        social_posts = social_posts_value(row)
        selected = " selected" if raw_ticker == ticker else ""
        selected_context = (
            '<span class="ss-visually-hidden">Selected for the Deep Analyze example. </span>'
            if selected else ""
        )
        preview_rows.append(
            f'<tr class="ss-decision-row{selected}">'
            f'<th scope="row"><span class="ss-decision-symbol">'
            f'{selected_context}{html.escape(raw_ticker)}</span>'
            f'<span class="ss-decision-company">{html.escape(company)}</span></th>'
            f'<td><span class="ss-sentiment {sentiment}">'
            f'{sentiment.title()}</span></td>'
            f'<td class="ss-decision-count">'
            f'{html.escape(social_posts)}</td></tr>'
        )

    recommendation = html.escape(str(card.get("verdict") or "Watch"))
    confidence = html.escape(str(card.get("confidence") or "Low"))
    reason = html.escape(str(card.get("reason") or ""))
    would_change = card.get("would_change") or []
    change = html.escape(str(would_change[0])) if would_change else ""
    result_ticker = html.escape(ticker or "NVDA")
    result_class = recommendation.lower()
    if result_class not in {"buy", "watch", "avoid"}:
        result_class = "watch"

    evidence = card.get("evidence") or {}
    movement = card.get("movement") or {}
    independent = evidence.get("independent_voices")
    mentions = evidence.get("mentions")
    if independent is not None:
        evidence_value = f"{int(independent)} independent clusters"
    elif mentions is not None:
        evidence_value = f"{int(mentions)} social posts"
    else:
        evidence_value = ""
    horizon = movement.get("horizon_days")
    price_points = evidence.get("price_points")
    range_tile = next(
        (
            tile for tile in (card.get("tiles") or [])
            if isinstance(tile, dict) and tile.get("key") == "range_30d"
        ),
        {},
    )
    metrics = []
    if evidence_value:
        metrics.append(("Evidence", evidence_value))
    if horizon is not None:
        metrics.append(("Signal horizon", f"{int(horizon)} trading days"))
    if price_points is not None:
        metrics.append(("Price history", f"{int(price_points)} daily closes"))
    if range_tile.get("value"):
        metrics.append(("30D risk range", str(range_tile["value"])))
    metric_html = "".join(
        '<div class="ss-decision-metric"><span>'
        f'{html.escape(label)}</span><strong>{html.escape(value)}</strong></div>'
        for label, value in metrics
    )
    change_html = (
        '<div class="ss-decision-change"><span>What would change this</span>'
        f'<strong>{change}</strong></div>' if change else ""
    )
    return f"""
      <section class="ss-decision" aria-label="Illustrative decision workspace">
        <header class="ss-decision-head">
          <div>
            <div class="ss-decision-kicker">Product preview · illustrative</div>
            <h2>Decision Workspace</h2>
          </div>
          <div class="ss-decision-meta">
            <strong>{html.escape(str(sector or 'Technology').title())}</strong>
            <span>{len(rows)} shortlisted stocks</span>
            <small>Saved illustrative example · not live market data</small>
          </div>
        </header>
        <div class="ss-decision-grid">
          <div class="ss-decision-scan">
            <div class="ss-decision-section-label">Market Scan</div>
            <table class="ss-decision-table">
              <caption>Illustrative saved Market Scan results.</caption>
              <colgroup><col class="stock"><col class="sentiment"><col class="posts"></colgroup>
              <thead><tr><th scope="col">Stock</th><th scope="col">Sentiment</th>
              <th scope="col">Social posts</th></tr></thead>
              <tbody>{''.join(preview_rows)}</tbody>
            </table>
          </div>
          <aside class="ss-decision-analysis">
            <div class="ss-decision-section-label">Deep Analyze</div>
            <div class="ss-decision-verdict">
              <div><strong>{result_ticker}</strong><span class="{result_class}">{recommendation}</span></div>
              <span>{confidence} confidence</span>
            </div>
            <p class="ss-decision-reason">{reason}</p>
            <div class="ss-decision-metrics">{metric_html}</div>
            {change_html}
            <div class="ss-decision-context">
              <span>Market context</span>
              <strong>Public social discussion + market-price history</strong>
            </div>
          </aside>
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
      align-items:center!important;gap:clamp(24px,3vw,40px)!important;
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
      flex:1 1 0!important;
    }
    .st-key-home_public_hero [data-testid="stHorizontalBlock"]:has(
      .st-key-home_public_story
    ):has(.st-key-home_public_preview) > [data-testid="stColumn"]:last-child {
      flex:1 1 0!important;
    }
    .ss-public-hero-copy {padding:clamp(1rem,3vw,2.2rem) 0;}
    .ss-public-hero-copy .ss-proof-strip {margin-bottom:1.15rem;}
    .ss-public-hero-copy h1 {
      margin:0;color:var(--text);font-size:clamp(2.65rem,4vw,3.35rem);
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
      padding:16px;background:linear-gradient(145deg,rgba(8,20,39,.98),rgba(8,15,30,.96));
      box-shadow:var(--ss-shadow-focus-panel);min-height:0;
    }
    .ss-hero-preview-kicker {
      color:var(--accent);font-size:.67rem;font-weight:780;
      letter-spacing:.1em;text-transform:uppercase;
    }
    .ss-hero-preview-head {
      display:flex;justify-content:space-between;align-items:baseline;
      gap:16px;margin:.45rem 0 .55rem;
    }
    .ss-hero-preview-head h2 {margin:0;font-size:1.35rem;}
    .ss-hero-preview-head > span {color:#94a3b8;font-size:.73rem;}
    .ss-hero-preview-table {
      width:100%;table-layout:fixed;border-collapse:separate;border-spacing:0;
      border:1px solid rgba(148,163,184,.14);border-radius:10px;overflow:hidden;
      color:#dbe3ee;font-size:.82rem;
    }
    .ss-hero-preview-table caption {
      position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
      clip:rect(0,0,0,0);white-space:nowrap;border:0;
    }
    .ss-hero-preview-table col.stock {width:27%;}
    .ss-hero-preview-table col.sentiment {width:43%;}
    .ss-hero-preview-table col.posts {width:30%;}
    .ss-hero-preview-table th,.ss-hero-preview-table td {
      padding:9px 12px;text-align:left;vertical-align:middle;
      border:0;border-bottom:1px solid rgba(148,163,184,.14);
    }
    .ss-hero-preview-table thead th {
      color:#8192aa;font-size:.68rem;font-weight:600;
    }
    .ss-hero-preview-table tbody th {color:#dbe3ee;font-weight:750;}
    .ss-hero-preview-table tbody tr:last-child th,
    .ss-hero-preview-table tbody tr:last-child td {border-bottom:0;}
    .ss-hero-preview-table tbody tr.selected th,
    .ss-hero-preview-table tbody tr.selected td {background:rgba(56,189,248,.055);}
    .ss-hero-preview-table tbody tr.selected th {
      box-shadow:inset 2px 0 0 var(--accent);
    }
    .ss-hero-preview-table th:last-child,
    .ss-hero-preview-table td:last-child {text-align:right;}
    .ss-hero-preview-count {color:#cbd5e1;font-variant-numeric:tabular-nums;}
    .ss-hero-result {
      margin-top:14px;padding-top:12px;border-top:1px solid rgba(148,163,184,.16);
    }
    .ss-hero-result-line {display:flex;align-items:baseline;gap:11px;margin:.55rem 0;}
    .ss-hero-result-line strong {font-size:1.35rem;}
    .ss-hero-result-line .buy {color:var(--ss-color-recommendation-buy);font-weight:800;}
    .ss-hero-result-line .watch {color:var(--ss-color-recommendation-watch);font-weight:800;}
    .ss-hero-result-line .avoid {color:var(--ss-color-recommendation-avoid);font-weight:800;}
    .ss-hero-result-line span:last-child {color:#cbd5e1;font-size:.8rem;}
    .ss-hero-result p {
      display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;
      overflow:hidden;margin:.4rem 0 0;color:#a8b5c7;font-size:.84rem;line-height:1.45;
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

    /* Schema-v2 landing: compact introduction + one unified decision surface. */
    .st-key-home_public_intro [data-testid="stHorizontalBlock"] {
      align-items:center!important;gap:clamp(28px,5vw,72px)!important;
    }
    .ss-public-value {padding:clamp(1rem,3vw,2.1rem) 0;}
    .ss-public-value h1 {
      margin:0;color:var(--text);font-size:clamp(2.65rem,4vw,3.45rem);
      font-weight:850;letter-spacing:-.045em;line-height:1.02;
    }
    .ss-public-value p {
      max-width:610px;margin:1rem 0 0;color:var(--muted);
      font-size:clamp(1rem,1.5vw,1.14rem);line-height:1.55;
    }
    .st-key-home_intro_action_shell {
      padding:20px;border:1px solid rgba(56,189,248,.2);border-radius:16px;
      background:linear-gradient(145deg,rgba(8,20,39,.8),rgba(8,15,30,.68));
    }
    .ss-public-action {padding:0;}
    .ss-public-action .ss-decision-kicker {margin-bottom:.45rem;}
    .ss-public-action h2 {margin:0;font-size:1.2rem;}
    .ss-public-action p {margin:.4rem 0 .9rem;color:#94a3b8;font-size:.84rem;}
    .ss-public-action ul {margin:.85rem 0 0;padding:0;list-style:none;}
    .ss-public-action li {
      display:grid;grid-template-columns:100px 1fr;gap:10px;padding:8px 0;
      border-top:1px solid rgba(148,163,184,.12);font-size:.76rem;
    }
    .ss-public-action li span {color:#8192aa;}
    .ss-public-action li strong {color:#cbd5e1;font-weight:650;}
    .st-key-home_intro_action .stButton > button,
    .st-key-home_intro_action [data-testid="stPageLink"] a {
      min-height:48px!important;width:100%;display:flex;align-items:center;
      justify-content:center;border-radius:10px;font-weight:750;
    }
    .ss-decision {
      margin:1.35rem 0 0;border:1px solid rgba(56,189,248,.26);
      border-radius:18px;background:linear-gradient(145deg,rgba(8,20,39,.98),rgba(8,15,30,.96));
      box-shadow:var(--ss-shadow-focus-panel);overflow:hidden;
    }
    .ss-decision-head {
      display:flex;align-items:flex-end;justify-content:space-between;gap:24px;
      padding:18px 20px;border-bottom:1px solid rgba(148,163,184,.15);
    }
    .ss-decision-kicker {
      color:var(--accent);font-size:.67rem;font-weight:780;
      letter-spacing:.1em;text-transform:uppercase;
    }
    .ss-decision-head h2 {margin:.38rem 0 0;font-size:1.45rem;}
    .ss-decision-meta {text-align:right;}
    .ss-decision-meta strong,.ss-decision-meta span,.ss-decision-meta small {
      display:block;
    }
    .ss-decision-meta strong {color:#dbe3ee;font-size:.8rem;}
    .ss-decision-meta span {color:#94a3b8;font-size:.74rem;}
    .ss-decision-meta small {margin-top:2px;color:#64748b;font-size:.68rem;}
    .ss-decision-grid {display:grid;grid-template-columns:42% 58%;}
    .ss-decision-scan,.ss-decision-analysis {padding:18px 20px 20px;min-width:0;}
    .ss-decision-analysis {border-left:1px solid rgba(148,163,184,.15);}
    .ss-decision-section-label {
      margin-bottom:10px;color:#8192aa;font-size:.66rem;font-weight:780;
      letter-spacing:.09em;text-transform:uppercase;
    }
    .ss-decision-table {
      width:100%;table-layout:fixed;border-collapse:separate;border-spacing:0;
      border:1px solid rgba(148,163,184,.14);border-radius:10px;overflow:hidden;
    }
    .ss-decision-table caption {
      position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
      clip:rect(0,0,0,0);white-space:nowrap;border:0;
    }
    .ss-decision-table col.stock {width:47%;}
    .ss-decision-table col.sentiment {width:30%;}
    .ss-decision-table col.posts {width:23%;}
    .ss-decision-table th,.ss-decision-table td {
      padding:9px 11px;text-align:left;vertical-align:middle;
      border-bottom:1px solid rgba(148,163,184,.12);
    }
    .ss-decision-table thead th {color:#8192aa;font-size:.66rem;font-weight:650;}
    .ss-decision-table tbody th {font-size:.78rem;}
    .ss-decision-table tbody tr:last-child th,
    .ss-decision-table tbody tr:last-child td {border-bottom:0;}
    .ss-decision-table .selected th,.ss-decision-table .selected td {
      background:rgba(56,189,248,.06);
    }
    .ss-decision-table .selected th {box-shadow:inset 2px 0 0 var(--accent);}
    .ss-decision-table th:last-child,.ss-decision-table td:last-child {text-align:right;}
    .ss-decision-symbol,.ss-decision-company {display:block;min-width:0;}
    .ss-decision-symbol {color:#e2e8f0;font-weight:800;}
    .ss-decision-company {
      margin-top:2px;color:#718198;font-size:.65rem;font-weight:500;
      overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
    }
    .ss-decision-count {color:#cbd5e1;font-size:.76rem;font-variant-numeric:tabular-nums;}
    .ss-decision-verdict {display:flex;align-items:baseline;justify-content:space-between;gap:18px;}
    .ss-decision-verdict > div {display:flex;align-items:baseline;gap:12px;}
    .ss-decision-verdict strong {font-size:1.55rem;}
    .ss-decision-verdict .buy {color:var(--ss-color-recommendation-buy);font-weight:820;}
    .ss-decision-verdict .watch {color:var(--ss-color-recommendation-watch);font-weight:820;}
    .ss-decision-verdict .avoid {color:var(--ss-color-recommendation-avoid);font-weight:820;}
    .ss-decision-verdict > span {color:#cbd5e1;font-size:.78rem;}
    .ss-decision-reason {margin:.55rem 0 1rem;color:#a8b5c7;font-size:.84rem;line-height:1.45;}
    .ss-decision-metrics {
      display:grid;grid-template-columns:repeat(2,minmax(0,1fr));
      border-top:1px solid rgba(148,163,184,.14);
      border-bottom:1px solid rgba(148,163,184,.14);
    }
    .ss-decision-metric {padding:11px 12px 11px 0;min-width:0;}
    .ss-decision-metric:nth-child(even) {padding-left:14px;border-left:1px solid rgba(148,163,184,.12);}
    .ss-decision-metric:nth-child(n+3) {border-top:1px solid rgba(148,163,184,.12);}
    .ss-decision-metric span,.ss-decision-change span,.ss-decision-context span {
      display:block;color:#718198;font-size:.62rem;font-weight:760;
      letter-spacing:.065em;text-transform:uppercase;
    }
    .ss-decision-metric strong {
      display:block;margin-top:4px;color:#dbe3ee;font-size:.78rem;
      overflow-wrap:anywhere;
    }
    .ss-decision-change,.ss-decision-context {padding-top:11px;}
    .ss-decision-change strong,.ss-decision-context strong {
      display:block;margin-top:4px;color:#a8b5c7;font-size:.75rem;font-weight:600;
    }
    @media (max-width:900px) {
      .st-key-home_public_intro [data-testid="stHorizontalBlock"] {flex-wrap:wrap!important;}
      .st-key-home_public_intro [data-testid="stColumn"] {
        flex:1 1 100%!important;min-width:100%!important;width:100%!important;
      }
      .ss-decision-grid {grid-template-columns:1fr;}
      .ss-decision-analysis {border-left:0;border-top:1px solid rgba(148,163,184,.15);}
    }
    @media (max-width:600px) {
      .ss-decision-head {align-items:flex-start;}
      .ss-decision-meta {text-align:left;}
      .ss-decision-table .ss-decision-company {display:none;}
      .ss-decision-table col.stock {width:31%;}
      .ss-decision-table col.sentiment {width:40%;}
      .ss-decision-table col.posts {width:29%;}
      .ss-decision-verdict {align-items:flex-start;flex-direction:column;gap:4px;}
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# --- Public product story. Authentication only changes the primary action. ---
from utils.auth import is_logged_in

_logged_in = is_logged_in()
_demo_publication = _load_published_demo()
_demo_bundle = (
    (_demo_publication or {}).get("bundle")
    if _demo_publication else None
)
_demo_frame = (
    _scan_frame_from_bundle(_demo_bundle)
    if _demo_bundle else _load_demo_scan()
)
_demo_available = {
    str(row.get("Ticker") or "").strip().upper()
    for row in _demo_frame.to_dict("records")
    if str(row.get("Overall Sentiment") or "").strip().lower() in _ASSERTED
}
if _demo_bundle:
    _demo_ticker, _demo_sector, _demo_card = _deep_from_bundle(_demo_bundle)
else:
    _demo_ticker, _demo_sector, _demo_results = _load_demo_deep(
        preferred_tickers=_demo_available,
    )
    _demo_card = _legacy_public_card(
        _demo_results,
        _demo_ticker,
        _demo_sector or _demo_frame.attrs.get("sector") or "tech",
    )
_demo_rows = _select_demo_rows(
    _demo_frame, limit=5, selected_ticker=_demo_ticker,
)

with st.container(key="home_public_intro"):
    story_col, action_col = st.columns([1.12, .88])
    with story_col:
        st.html(
            """
            <div class="ss-public-value">
              <h1>Finding short-term opportunities shouldn't feel like a full-time job.</h1>
              <p>Stock Sentinel turns social chatter into a ranked sentiment shortlist, then validates a selected ticker with market data.</p>
            </div>
            """
        )
    with action_col:
        with st.container(key="home_intro_action_shell"):
            st.html(
                """
                <div class="ss-public-action">
                  <div class="ss-decision-kicker">A complete decision path</div>
                  <h2>One scan. One analysis. Clear evidence.</h2>
                  <p>See the cost before every action. No card is required to begin.</p>
                </div>
                """
            )
            with st.container(key="home_intro_action"):
                if _logged_in:
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
            st.html(
                """
                <div class="ss-public-action">
                  <ul>
                    <li><span>Coverage</span><strong>US-listed stocks in the supported market universe</strong></li>
                    <li><span>Transparency</span><strong>Evidence age shown on live results</strong></li>
                    <li><span>Explanation</span><strong>Reasons and confidence included</strong></li>
                  </ul>
                </div>
                """
            )

st.html(
    _decision_workspace_html(
        _demo_rows,
        _demo_ticker or "NVDA",
        _demo_card,
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
