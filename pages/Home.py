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
from utils.ui import apply_theme, close_page, load_sector_pulse, render_sector_pulse
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
    frame.attrs["total_results"] = payload.get("total_results", len(rows))
    frame.attrs["signal_results"] = sum(
        str(row.get("Overall Sentiment") or "").strip().lower() in _ASSERTED
        for row in rows
    )
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
    frame.attrs["total_results"] = scan.get("total_results", len(rows))
    frame.attrs["signal_results"] = sum(
        str(row.get("Overall Sentiment") or "").strip().lower() in _ASSERTED
        for row in rows
    )
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

    The landing page is not a second results page. It keeps the analyzed ticker,
    represents the available directional states, then uses genuine inconclusive
    rows to show that the scan rejects weak evidence.
    """
    if frame.empty or limit <= 0:
        return []

    records = frame.to_dict("records")
    if not records:
        return []
    chosen: list[int] = []
    selected_ticker = str(selected_ticker or "").strip().upper()
    if selected_ticker:
        for index, row in enumerate(records):
            if str(row.get("Ticker", "")).strip().upper() == selected_ticker:
                chosen.append(index)
                break
    for sentiment in ("bullish", "bearish", "neutral"):
        if len(chosen) >= limit:
            break
        for index, row in enumerate(records):
            if (
                index not in chosen
                and str(row.get("Overall Sentiment", "")).strip().lower()
                == sentiment
            ):
                chosen.append(index)
                break
    for index, row in enumerate(records):
        if len(chosen) >= limit:
            break
        if (
            index not in chosen
            and str(row.get("Overall Sentiment", "")).strip().lower()
            in _ASSERTED
        ):
            chosen.append(index)
    for index in range(len(records)):
        if len(chosen) >= limit:
            break
        if index not in chosen:
            chosen.append(index)
    return [records[index] for index in sorted(chosen)]


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


def _count_phrase(count: int, singular: str, plural: str | None = None) -> str:
    """Render a grammatically correct compact count for public copy."""
    word = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {word}"


def _polish_preview_text(value: object, *, kind: str) -> str:
    """Translate canonical internal phrasing into calm public-facing copy."""
    text = " ".join(str(value or "").strip().split())
    lowered = text.lower()
    if kind == "reason" and lowered.startswith("real evidence"):
        return "Evidence is present, but it does not support a directional call."
    if (
        kind == "change"
        and "confirmed event" in lowered
        and "needed to carry a call" in lowered
    ):
        return (
            "A directional call would require the confirmed events to align "
            "more strongly; their current combined signal remains below the "
            "decision threshold."
        )
    text = text.replace("event(s)", "events")
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    if text[-1] not in ".!?":
        text += "."
    return text


def _sector_label(value: object) -> str:
    sector = str(value or "").strip().lower()
    return {"tech": "Technology"}.get(sector, sector.title() or "Technology")


def _scan_summary(total_results: int, signal_results: int) -> str:
    """Summarize selectivity without implying inconclusive rows are signals."""
    total = max(int(total_results), 0)
    signals = min(max(int(signal_results), 0), total)
    needs = total - signals
    needs_label = (
        f"{needs} need more evidence" if needs != 1 else
        "1 needs more evidence"
    )
    return (
        f"{total} stocks scanned · {_count_phrase(signals, 'signal')} · "
        f"{needs_label}"
    )


def _decision_workspace_html(
    rows: list[dict], ticker: str, card: dict, sector: str,
    *, total_results: int | None = None, durable: bool = False,
    total_results_complete: bool = False,
    signal_results: int | None = None,
) -> str:
    """Render the date-free, saved-workflow preview from the public card."""
    preview_rows = []
    for row in rows[:3]:
        raw_ticker = str(row.get("Ticker") or "—").strip().upper()
        company = str(row.get("Company Name") or "").strip()
        sentiment = str(
            row.get("Overall Sentiment") or "Neutral"
        ).strip().lower()
        is_signal = sentiment in _ASSERTED
        evidence_state = str(row.get("Evidence State") or "").strip()
        if not is_signal:
            evidence_state = evidence_state or "Needs more evidence"
        state_html = (
            f'<span class="ss-sentiment {sentiment}">{sentiment.title()}</span>'
            if is_signal else
            f'<span class="ss-b5-evidence-state">'
            f'{html.escape(evidence_state)}</span>'
        )
        social_posts = social_posts_value(row)
        selected = " selected" if raw_ticker == ticker else ""
        selected_context = (
            '<span class="ss-visually-hidden">Selected for the Deep Analyze example. </span>'
            if selected else ""
        )
        count_label = (
            f'{html.escape(social_posts)} <span aria-hidden="true">social posts</span>'
            if social_posts != "—" else "—"
        )
        count_aria = (
            f' aria-label="{html.escape(social_posts)} social posts"'
            if social_posts != "—" else ' aria-label="Social-post count unavailable"'
        )
        preview_rows.append(
            f'<article class="ss-b5-scan-card{selected}" role="listitem">'
            f'<div class="ss-b5-stock"><strong>{selected_context}'
            f'{html.escape(raw_ticker)}</strong>'
            f'<small>{html.escape(company)}</small></div>'
            f'{state_html}'
            f'<span class="ss-b5-count"{count_aria}>{count_label}</span>'
            f'</article>'
        )

    recommendation = html.escape(str(card.get("verdict") or "Watch"))
    confidence = html.escape(str(card.get("confidence") or "Low"))
    reason = html.escape(
        _polish_preview_text(card.get("reason"), kind="reason")
    )
    would_change = card.get("would_change") or []
    change = html.escape(
        _polish_preview_text(would_change[0], kind="change")
    ) if would_change else ""
    result_ticker = html.escape(ticker or "NVDA")
    result_class = recommendation.lower()
    if result_class not in {"buy", "watch", "avoid"}:
        result_class = "watch"

    evidence = card.get("evidence") or {}
    movement = card.get("movement") or {}
    independent = evidence.get("independent_voices")
    evidence_value = (
        f"{int(independent)} clusters" if independent is not None else "Unavailable"
    )
    horizon = movement.get("horizon_days")
    price_points = evidence.get("price_points")
    range_tile = next(
        (
            tile for tile in (card.get("tiles") or [])
            if isinstance(tile, dict) and tile.get("key") == "range_30d"
        ),
        {},
    )
    selected_row = next(
        (
            row for row in rows
            if str(row.get("Ticker") or "").strip().upper() == ticker
        ),
        {},
    )
    social_sentiment = str(
        selected_row.get("Overall Sentiment") or "Neutral"
    ).strip().title()
    range_value = str(range_tile.get("value") or "Unavailable")
    metrics = [
        ("Scan sentiment", social_sentiment, "Saved scan classification"),
        ("Independent evidence", evidence_value, "Distinct evidence groups"),
        (
            "Signal horizon",
            f"{int(horizon)} trading days" if horizon is not None else "Unavailable",
            "Monitoring window",
        ),
        (
            "Recent volatility range",
            range_value,
            "Historical movement context · not a forecast"
            if range_value != "Unavailable" else "",
        ),
    ]
    metric_html = "".join(
        '<div class="ss-b5-metric"><span>'
        f'{html.escape(label)}</span><strong>{html.escape(value)}</strong>'
        f'<small>{html.escape(helper)}</small></div>'
        for label, value, helper in metrics
    )
    change_html = (
        '<div class="ss-b5-change"><span>What would change this</span>'
        f'<p>{change}</p></div>' if change else ""
    )
    shown_count = len(rows)
    total_results = max(int(total_results or shown_count), shown_count)
    signal_count = (
        int(signal_results) if signal_results is not None else
        sum(
            str(row.get("Overall Sentiment") or "").strip().lower() in _ASSERTED
            for row in rows
        )
    )
    if durable and total_results_complete:
        provenance = "Actual saved run · not live market data"
        shown_label = _scan_summary(total_results, signal_count)
    elif durable:
        provenance = "Saved example from an actual run · not live market data"
        shown_label = f"{_count_phrase(shown_count, 'public result')} shown"
    else:
        provenance = "Illustrative fallback example · not live market data"
        shown_label = f"{_count_phrase(shown_count, 'fallback result')} shown"
    closes = (
        f"{int(price_points)} daily closes"
        if price_points is not None else "Price-history count unavailable"
    )
    return f"""
      <section class="ss-b5-workspace" aria-label="Saved product example">
        <header class="ss-b5-workspace-head">
          <div>
            <div class="ss-b5-kicker">Saved product example</div>
            <h2>See what a scan and analysis deliver</h2>
            <p class="ss-demo-description">A previously saved sector scan and ticker analysis, separate from the current Sector Pulse above.</p>
          </div>
          <div class="ss-b5-provenance">
            <strong>{html.escape(_sector_label(sector))}</strong>
            <span>{provenance}</span>
          </div>
        </header>
        <div class="ss-b5-workspace-body"><div class="ss-demo-grid"><div>
          <div class="ss-b5-section-head">
            <span>Market Scan</span><strong>{shown_label}</strong>
          </div>
          <div class="ss-b5-scan-grid items-{min(max(shown_count, 1), 3)}" role="list" aria-label="Illustrative saved Market Scan results">
            {''.join(preview_rows)}
          </div>
          <p class="ss-b5-attention-note">Social-post count indicates attention, not independent evidence.</p>
          </div>
          <aside class="ss-b5-analysis" aria-label="Illustrative Deep Analysis result">
            <div class="ss-b5-analysis-head">
              <div>
                <span class="ss-b5-section-label">Deep Analysis · model output</span>
                <div class="ss-b5-verdict"><strong>{result_ticker}</strong><b class="{result_class}">{recommendation}</b><span>{confidence} confidence</span></div>
              </div>
            </div>
            <div class="ss-b5-insight">
              <span>Why this output</span><p>{reason}</p>
            </div>
            <p class="ss-demo-summary">{html.escape(evidence_value)} · {html.escape(str(int(horizon)) + " trading days" if horizon is not None else "Horizon unavailable")}</p>
            <details class="ss-demo-details"><summary>Explore the full example</summary>
            <div class="ss-b5-metrics">{metric_html}</div>
            {change_html}
            </details>
          </aside>
        </div>
            <div class="ss-b5-source ss-demo-source">
              <span>Sources: public social discussion + market-price history · {html.escape(closes)}</span>
              <small>Confidence reflects evidence quality and agreement, not probability of return.</small>
            </div>
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
render_top_nav(signup_primary=False)
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

    /* Panel-selected B5 landing: one quiet hero and one evidence-rich surface. */
    .st-key-home_public_intro [data-testid="stHorizontalBlock"] {
      align-items:center!important;gap:clamp(32px,4vw,58px)!important;
    }
    .st-key-home_public_intro [data-testid="stColumn"]:first-child {
      flex:1.38 1 0!important;min-width:0!important;
    }
    .st-key-home_public_intro [data-testid="stColumn"]:last-child {
      flex:.82 1 0!important;min-width:300px!important;
    }
    .ss-b5-hero {padding:1.25rem 0 1.5rem;}
    .ss-b5-eyebrow,.ss-b5-kicker,.ss-b5-section-label,
    .ss-b5-section-head > span {
      color:var(--accent);font-size:.75rem;font-weight:800;
      letter-spacing:.08em;text-transform:uppercase;
    }
    .ss-b5-hero h1 {
      max-width:720px;margin:.75rem 0 0;padding:0;color:var(--text);
      font-size:clamp(2.6rem,3.85vw,3.3rem);font-weight:820;
      letter-spacing:-.052em;line-height:1.06;
    }
    .ss-b5-hero-side p {
      max-width:430px;margin:0 0 1.2rem;color:#b4c1d2;
      font-size:1.125rem;line-height:1.55;
    }
    .st-key-home_intro_action_shell {
      padding:0!important;border:0!important;border-radius:0!important;
      background:transparent!important;box-shadow:none!important;
    }
    .ss-b5-cta-copy {margin-top:.75rem;color:#a8b5c7;font-size:.8125rem;line-height:1.5;}
    .st-key-home_intro_action .stButton > button,
    .st-key-home_intro_action [data-testid="stPageLink"] a {
      min-height:52px!important;width:100%;max-width:288px;display:flex;align-items:center;
      justify-content:center;border-radius:10px;font-weight:780;
    }
    .ss-b5-workspace {
      margin:.1rem 0 0;border:1px solid rgba(56,189,248,.3);border-radius:var(--ss-radius-panel);
      overflow:hidden;background:linear-gradient(145deg,rgba(7,20,39,.99),rgba(7,15,29,.98));
      box-shadow:none;
    }
    .ss-b5-workspace-head {
      display:flex;align-items:flex-end;justify-content:space-between;gap:28px;
      padding:20px 24px;border-bottom:1px solid rgba(148,163,184,.15);
    }
    .ss-b5-workspace-head h2 {margin:.45rem 0 0;padding:0;font-size:1.625rem;letter-spacing:-.025em;}
    .ss-b5-provenance {max-width:520px;text-align:right;}
    .ss-b5-provenance strong,.ss-b5-provenance span {display:block;}
    .ss-b5-provenance strong {color:#dbe3ee;font-size:.875rem;}
    .ss-b5-provenance span {margin-top:4px;color:#a8b5c7;font-size:.8125rem;line-height:1.5;}
    .ss-b5-workspace-body {padding:21px 24px 24px;}
    .ss-b5-section-head {
      display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:11px;
    }
    .ss-b5-section-head > strong {color:#a8b5c7;font-size:.8125rem;font-weight:650;line-height:1.4;}
    .ss-b5-scan-grid {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px;}
    .ss-b5-scan-card {
      position:relative;display:grid;grid-template-columns:minmax(0,1fr) auto;
      align-items:center;gap:8px 12px;min-height:78px;padding:13px 14px;
      border:1px solid rgba(148,163,184,.16);border-radius:10px;
      background:rgba(15,23,42,.5);
    }
    .ss-b5-scan-card.selected {background:rgba(56,189,248,.065);}
    .ss-b5-scan-card.selected::before {
      content:"";position:absolute;inset:0 auto 0 0;width:2px;border-radius:10px 0 0 10px;
      background:var(--accent);
    }
    .ss-b5-stock {grid-column:1 / -1;min-width:0;}
    .ss-b5-stock strong {display:block;color:#e2e8f0;font-size:.9375rem;font-weight:820;}
    .ss-b5-stock small {display:block;margin-top:3px;color:#a8b5c7;font-size:.8125rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
    .ss-b5-scan-grid.items-1 {grid-template-columns:1fr;}
    .ss-b5-scan-grid.items-1 .ss-b5-scan-card {
      grid-template-columns:minmax(240px,1fr) auto auto;min-height:62px;
    }
    .ss-b5-scan-grid.items-1 .ss-b5-stock {grid-column:auto;}
    .ss-b5-count {color:#cbd5e1;font-size:.8125rem;font-variant-numeric:tabular-nums;}
    .ss-b5-count {text-align:right;}
    .ss-b5-count span {color:#a8b5c7;}
    .ss-b5-evidence-state {color:#cbd5e1;font-size:.8125rem;font-weight:650;}
    .ss-b5-attention-note {margin:9px 0 0;color:#a8b5c7;font-size:.8125rem;}
    .ss-b5-divider {height:1px;margin:20px 0;background:rgba(148,163,184,.15);}
    .ss-b5-analysis {
      position:relative;overflow:hidden;padding:20px;border:1px solid rgba(56,189,248,.2);
      border-radius:14px;background:linear-gradient(180deg,rgba(11,24,42,.96),rgba(8,20,36,.92));
      box-shadow:inset 0 1px 0 rgba(226,232,240,.035);
    }
    .ss-b5-analysis::before {
      content:"";position:absolute;inset:0 34% auto 0;height:2px;
      background:linear-gradient(90deg,rgba(56,189,248,.8),rgba(56,189,248,0));
    }
    .ss-b5-analysis-head {display:flex;align-items:flex-end;justify-content:space-between;gap:24px;}
    .ss-b5-verdict {display:flex;align-items:center;flex-wrap:wrap;gap:10px;margin-top:8px;}
    .ss-b5-verdict strong {font-size:1.75rem;letter-spacing:-.02em;}
    .ss-b5-verdict b {
      display:inline-flex;align-items:center;min-height:30px;padding:3px 10px;
      border:1px solid transparent;border-radius:999px;font-size:1rem;font-weight:800;
    }
    .ss-b5-verdict .buy {color:var(--ss-color-recommendation-buy);background:rgba(34,197,94,.09);border-color:rgba(34,197,94,.28);}
    .ss-b5-verdict .watch {color:#fbbf24;background:rgba(245,158,11,.1);border-color:rgba(245,158,11,.3);}
    .ss-b5-verdict .avoid {color:var(--ss-color-recommendation-avoid);background:rgba(248,113,113,.09);border-color:rgba(248,113,113,.28);}
    .ss-b5-verdict > span {
      display:inline-flex;align-items:center;min-height:30px;padding:3px 10px;
      border:1px solid rgba(148,163,184,.18);border-radius:999px;
      color:#cbd5e1;background:rgba(15,30,51,.66);font-size:.875rem;font-weight:650;
    }
    .ss-b5-insight,.ss-b5-change {
      position:relative;margin-top:16px;padding:13px 15px 14px;border:1px solid rgba(148,163,184,.16);
      border-radius:10px;background:rgba(15,30,51,.55);
    }
    .ss-b5-insight {border-left:2px solid rgba(56,189,248,.72);background:rgba(56,189,248,.045);}
    .ss-b5-change {border-left:2px solid rgba(245,158,11,.62);background:rgba(245,158,11,.035);}
    .ss-b5-insight span,.ss-b5-change span {
      display:block;color:#afc0d2;font-size:.78125rem;font-weight:700;letter-spacing:.01em;
    }
    .ss-b5-insight p,.ss-b5-change p {
      max-width:72ch;margin:6px 0 0;color:#e6edf5;font-size:.9375rem;
      font-weight:520;line-height:1.55;
    }
    .ss-b5-metrics {
      display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-top:16px;
    }
    .ss-b5-metric {
      min-height:94px;padding:15px 16px;border:1px solid rgba(148,163,184,.2);
      border-radius:10px;background:rgba(15,30,51,.72);box-shadow:inset 0 1px 0 rgba(226,232,240,.025);
    }
    .ss-b5-metric span {display:block;color:#afc0d2;font-size:.78125rem;font-weight:650;letter-spacing:.01em;text-transform:none;}
    .ss-b5-metric strong {display:block;margin-top:7px;color:#f1f5f9;font-size:1rem;line-height:1.35;}
    .ss-b5-metric small {display:block;margin-top:5px;color:#afc0d2;font-size:.8125rem;line-height:1.45;}
    .ss-b5-source {
      display:flex;justify-content:space-between;gap:20px;margin-top:17px;padding-top:14px;
      border-top:1px solid rgba(148,163,184,.16);color:#afc0d2;font-size:.8125rem;line-height:1.5;
    }
    .ss-b5-source small {font-size:inherit;text-align:right;}
    .ss-b5-assurance {
      display:grid;grid-template-columns:repeat(3,1fr);gap:0;margin:1.35rem 0 .25rem;
      padding:15px 0;border-top:1px solid rgba(148,163,184,.14);border-bottom:1px solid rgba(148,163,184,.14);
    }
    .ss-b5-assurance span {padding:0 18px;text-align:center;color:#a8b5c7;font-size:.875rem;}
    .ss-b5-assurance span + span {border-left:1px solid rgba(148,163,184,.14);}
    .ss-demo-description {max-width:570px;color:var(--muted);font-size:.875rem;line-height:1.6;margin:10px 0 0;}
    .ss-demo-grid {display:grid;grid-template-columns:minmax(0,2fr) minmax(0,3fr);gap:26px;align-items:start;}
    .ss-demo-grid .ss-b5-scan-grid {grid-template-columns:1fr;}
    .ss-demo-grid .ss-b5-scan-grid .ss-b5-scan-card {grid-template-columns:minmax(0,1fr) auto;}
    .ss-demo-grid .ss-b5-scan-grid .ss-b5-stock {grid-column:1 / -1;}
    .ss-demo-grid .ss-b5-count {white-space:nowrap;}
    .ss-demo-grid .ss-sentiment {justify-self:start;width:fit-content;}
    .ss-demo-grid .ss-b5-stock small {white-space:normal;}
    .ss-demo-grid .ss-b5-analysis {padding:0 0 0 24px;border:0;border-left:1px solid var(--border);border-radius:0;background:none;box-shadow:none;}
    .ss-demo-grid .ss-b5-analysis::before {display:none;}
    .ss-demo-grid .ss-b5-insight {padding:0;border:0;background:none;}
    .ss-demo-grid .ss-b5-metrics {grid-template-columns:repeat(2,minmax(0,1fr));}
    .ss-demo-summary {margin:12px 0;color:var(--muted);font-size:.875rem;}
    .ss-demo-details summary {cursor:pointer;color:var(--accent);min-height:44px;display:flex;align-items:center;gap:8px;padding:12px 0;font-size:.875rem;}
    .ss-demo-source {align-items:flex-start;gap:12px 28px;font-size:.78rem;}
    .ss-demo-source > * {flex:1;min-width:0;}
    @media(max-width:700px) {
      .ss-demo-grid {grid-template-columns:1fr;}
      .ss-demo-grid .ss-b5-analysis {padding:22px 0 0;border-left:0;border-top:1px solid var(--border);}
    }
    @media (max-width:900px) {
      .st-key-home_public_intro [data-testid="stHorizontalBlock"] {flex-wrap:wrap!important;}
      .st-key-home_public_intro [data-testid="stColumn"]:first-child,
      .st-key-home_public_intro [data-testid="stColumn"]:last-child {
        flex:1 1 100%!important;min-width:100%!important;width:100%!important;
      }
      .ss-b5-hero {padding-bottom:1rem;}
      .st-key-home_intro_action_shell {max-width:420px;}
      .ss-b5-metrics {grid-template-columns:repeat(2,minmax(0,1fr));}
    }
    @media (max-width:800px) {
      .ss-b5-scan-grid {grid-template-columns:1fr;}
    }
    @media (max-width:650px) {
      .ss-b5-hero {padding-top:.75rem;}
      .st-key-home_intro_action .stButton > button {max-width:100%;}
      .ss-b5-hero h1 {font-size:clamp(2.35rem,11vw,2.55rem);}
      .ss-b5-workspace-head,.ss-b5-analysis-head,.ss-b5-source {align-items:flex-start;flex-direction:column;}
      .ss-b5-provenance,.ss-b5-source small {text-align:left;}
      .ss-b5-stock small,.ss-b5-count,.ss-b5-evidence-state,
      .ss-b5-attention-note,.ss-b5-metric small,.ss-b5-source {font-size:.8125rem;}
      .ss-b5-metrics,.ss-b5-assurance {grid-template-columns:1fr;}
      .ss-b5-scan-grid.items-1 .ss-b5-scan-card {grid-template-columns:minmax(0,1fr) auto;}
      .ss-b5-scan-grid.items-1 .ss-b5-stock {grid-column:1 / -1;}
      .ss-b5-analysis {padding:16px;}
      .ss-b5-workspace-head,.ss-b5-workspace-body {padding-left:16px;padding-right:16px;}
      .ss-b5-assurance span {padding:8px 4px;text-align:left;}
      .ss-b5-assurance span + span {border-left:0;border-top:1px solid rgba(148,163,184,.12);}
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
    _demo_frame, limit=3, selected_ticker=_demo_ticker,
)

with st.container(key="home_public_intro"):
    story_col, action_col = st.columns([1.38, .82])
    with story_col:
        st.html(
            """
            <div class="ss-b5-hero">
              <div class="ss-b5-eyebrow">Short-term market intelligence</div>
              <h1>Finding short-term opportunities shouldn't feel like a full-time job.</h1>
            </div>
            """
        )
    with action_col:
        with st.container(key="home_intro_action_shell"):
            st.html(
                """
                <div class="ss-b5-hero-side">
                  <p>Explore trading activity across sectors. Scan the conversation, then examine the evidence behind a company.</p>
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
                    st.session_state.pop("_public_research_intent", None)
                    st.session_state["auth_initial_mode"] = "Create Account"
                    st.session_state["_after_auth_page"] = "Discovery"
                    st.switch_page("pages/Auth.py")
            st.html(
                '<p class="ss-b5-cta-copy">Continue to Market Scan.</p>'
                if _logged_in else
                '<p class="ss-b5-cta-copy">Enough for one sector scan + one ticker analysis · no card required</p>'
            )

render_sector_pulse(load_sector_pulse(), surface="home")
with st.container(key="home_direct_analysis"):
    st.page_link("pages/Deep_Analysis.py", label="Already have a company in mind? **Open Deep Analyze →**")

st.html(
    _decision_workspace_html(
        _demo_rows,
        _demo_ticker or "NVDA",
        _demo_card,
        _demo_sector or _demo_frame.attrs.get("sector") or "tech",
        total_results=_demo_frame.attrs.get("total_results"),
        signal_results=_demo_frame.attrs.get("signal_results"),
        durable=bool(_demo_bundle),
        total_results_complete=bool(
            (_demo_publication or {}).get("total_results_complete")
        ),
    )
)

st.markdown("<div style='height:.1rem'></div>", unsafe_allow_html=True)

# The workspace demonstrates the workflow; keep the final reassurance flat.
st.html(
    """
    <aside class="ss-b5-assurance" aria-label="Credit and purchase assurances">
      <span>Costs shown before every action</span>
      <span>Eligible failed runs are automatically refunded</span>
      <span>Credits never expire</span>
    </aside>
    """
)

close_page()
