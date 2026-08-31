"""Shared UI scaffolding (theme/layout/disclaimers/errors).

Streamlit doesn't support real HTML template inheritance. This module centralizes
our shared CSS + wrapper layout so all pages stay consistent.
"""

from __future__ import annotations

import html
import logging
import math
from pathlib import Path
from typing import Callable

import streamlit as st


LOG = logging.getLogger(__name__)

GENERIC_ERROR_TEXT = "Something went wrong. Please try again later."
_TOKEN_CSS_PATH = (
    Path(__file__).resolve().parents[1]
    / "assets" / "styles" / "stock-sentinel-tokens.css"
)
_COMPONENT_CSS_PATH = (
    Path(__file__).resolve().parents[1]
    / "assets" / "styles" / "stock-sentinel-components.css"
)
_STREAMLIT_ADAPTER_CSS_PATH = (
    Path(__file__).resolve().parents[1]
    / "assets" / "styles" / "stock-sentinel-streamlit-adapter.css"
)


def apply_theme() -> None:
    """Apply the global Discovery-style theme.

    Safe to call multiple times.
    """
    # Adapter boundary: load portable product tokens first, then map them onto
    # the current Streamlit renderer below. Future frontends import the CSS
    # asset directly and replace only this adapter layer.
    portable_css = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (_TOKEN_CSS_PATH, _COMPONENT_CSS_PATH)
    )
    st.markdown(f"<style>{portable_css}</style>", unsafe_allow_html=True)
    st.markdown(
        """
        <style>
        h1, h2, h3, h4, h5, h6, p, span, div, label {
          color: var(--text);
        }
        .stCaption, [data-testid="stCaptionContainer"] {
          color: var(--muted) !important;
        }

        [data-testid="stAppViewContainer"] {
          background: radial-gradient(1000px 420px at 18% 0%, rgba(56,189,248,.08), transparent 55%),
                      var(--bg);
          color: var(--text);
        }

        [data-testid="stMain"],
        .stMain,
        section[data-testid="stMain"] {
          padding-top: 0 !important;
          margin-top: 0 !important;
        }

        /* Hide Streamlit chrome so the app matches the mockup */
        [data-testid="collapsedControl"],
        button[title="Open sidebar"],
        button[title="Close sidebar"],
        [data-testid="stSidebarCollapsedControl"],
        [data-testid="stSidebarNavCollapseButton"],
        [data-testid="stSidebarNavExpandButton"],
        [data-testid="stHeader"],
        [data-testid="stToolbar"],
        .stAppToolbar,
        header[data-testid="stHeader"] {
          display: none !important;
        }

        div[data-testid="stMainBlockContainer"] {
          max-width: var(--ss-shell-max-width);
          margin: 0 auto;
          padding: 0 var(--ss-shell-gutter) 2rem;
        }

        /* Establish the vertical shell before page-local styles arrive. */
        div[data-testid="stMainBlockContainer"] > div:first-child {
          margin-top: 0 !important;
          padding-top: 0 !important;
        }
        section[data-testid="stMain"] > div {
          padding-top: 0 !important;
        }

        @media (max-width: 640px) {
          div[data-testid="stMainBlockContainer"] {
            padding-left: 1.05rem !important;
            padding-right: 1.05rem !important;
          }
        }

        .clawd-app-wrapper {
          /* The shared main container owns width and gutters. */
          width: 100%;
          max-width: none;
          margin: 0;
          padding: 0;
          box-sizing: border-box;
        }

        footer { visibility: hidden; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    # Loaded last so host defaults cannot override product tokens. This file
    # is the only shared layer that knows how Streamlit names its widgets.
    adapter_css = _STREAMLIT_ADAPTER_CSS_PATH.read_text(encoding="utf-8")
    st.markdown(f"<style>{adapter_css}</style>", unsafe_allow_html=True)


def system_state_html(
    *, kind: str, title: str, message: str, meta: str = ""
) -> str:
    """Return portable, semantic feedback markup without host-specific state."""
    normalized = kind if kind in {"info", "success", "warning", "error"} else "info"
    role = "alert" if normalized == "error" else "status"
    live = "assertive" if normalized == "error" else "polite"
    eyebrow = {
        "info": "Status", "success": "Completed",
        "warning": "Needs attention", "error": "Request not completed",
    }[normalized]
    meta_html = (
        f'<p class="ss-system-state__meta">{html.escape(str(meta))}</p>'
        if meta else ""
    )
    return (
        f'<section class="ss-system-state" data-kind="{normalized}" '
        f'role="{role}" aria-live="{live}" aria-atomic="true">'
        f'<p class="ss-system-state__eyebrow">{eyebrow}</p>'
        f'<h2 class="ss-system-state__title">{html.escape(str(title))}</h2>'
        f'<p class="ss-system-state__message">{html.escape(str(message))}</p>'
        f'{meta_html}</section>'
    )


def render_system_state(
    *, kind: str, title: str, message: str, meta: str = ""
) -> None:
    """Render a consistent loading/failure/empty/payment-adjacent state."""
    st.html(system_state_html(kind=kind, title=title, message=message, meta=meta))


def processing_state_html(message: str) -> str:
    """Return a screen-reader-announced processing state for live updates."""
    return (
        '<div class="ss-processing-state" role="status" aria-live="polite" '
        'aria-atomic="true">'
        f'{html.escape(str(message))}</div>'
    )


def render_workflow_hint(*, title: str, message: str, steps: list[str]) -> None:
    """Render a quiet, portable empty-state contract for task pages."""
    safe_title = html.escape(str(title))
    heading_id = "workflow-" + "-".join(
        part for part in "".join(
            char.lower() if char.isalnum() else " " for char in str(title)
        ).split()[:6]
    )
    heading_id = heading_id or "workflow-next-steps"
    safe_message = html.escape(str(message))
    safe_steps = "".join(
        f"<li>{html.escape(str(step))}</li>" for step in steps[:3]
    )
    st.html(
        f"""
        <section class="ss-workflow-hint" aria-labelledby="{heading_id}">
          <div>
            <h2 id="{heading_id}">{safe_title}</h2>
            <p>{safe_message}</p>
          </div>
          <ol>{safe_steps}</ol>
        </section>
        <style>
          .ss-workflow-hint {{
            display:grid;grid-template-columns:minmax(0,1fr) minmax(280px,.9fr);
            gap:20px;align-items:start;margin:.75rem 0 1rem;padding:14px 16px;
            border:1px solid rgba(148,163,184,.14);border-radius:12px;
            background:rgba(8,15,30,.46);
          }}
          .ss-workflow-hint h2 {{margin:0;font-size:.88rem;color:#dbe3ee;}}
          .ss-workflow-hint p {{margin:.25rem 0 0;color:#8192aa;font-size:.8rem;line-height:1.45;}}
          .ss-workflow-hint ol {{margin:0;padding-left:1.15rem;}}
          .ss-workflow-hint li {{color:#a8b5c7;font-size:.78rem;line-height:1.45;margin:.12rem 0;}}
          @media (max-width:700px) {{.ss-workflow-hint {{grid-template-columns:1fr;gap:10px;}}}}
        </style>
        """
    )


def render_compact_task_hint(*, title: str, message: str) -> None:
    """Render the terse pre-run state used by signed-in task pages.

    Unlike ``render_workflow_hint``, this is deliberately one content row on
    desktop.  The controls immediately above it already explain the task, so a
    second instructional panel would repeat the interface and reserve result
    space before any result exists.
    """
    safe_title = html.escape(str(title))
    safe_message = html.escape(str(message))
    heading_id = "task-hint-" + "-".join(
        part for part in "".join(
            char.lower() if char.isalnum() else " " for char in str(title)
        ).split()[:6]
    )
    heading_id = heading_id or "task-hint-status"
    st.html(
        f"""
        <section class="ss-task-hint" aria-labelledby="{heading_id}">
          <h2 id="{heading_id}">{safe_title}</h2>
          <p>{safe_message}</p>
        </section>
        """
    )

def render_footer() -> None:
    """Simple footer with support + disclaimer.

    Note: Streamlit doesn't support real <a href> navigation for switch_page.
    We use st.page_link so it works in-app.
    """

    st.markdown(
        """
        <style>
          .clawd-footer {
            margin-top: 18px;
            padding: 14px 0 6px 0;
            border-top: 1px solid rgba(148,163,184,0.12);
          }
          .clawd-footer .meta {
            color: rgba(148,163,184,.9);
            font-size: 0.86rem;
            line-height: 1.45;
            margin-top: 10px;
          }
          .st-key-footer_links [data-testid="stPageLink"] a {
            color: rgba(229,231,235,.88) !important;
            text-decoration: none !important;
            font-weight: 650;
            min-height: 44px;
            display: flex;
            align-items: center;
            padding: .35rem .25rem;
          }
          .st-key-footer_links [data-testid="stPageLink"] a:hover {
            text-decoration: underline !important;
          }
          .st-key-footer_links [data-testid="stHorizontalBlock"] {
            align-items:center!important;
          }
        </style>
        <div class="clawd-footer">
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Small, stable support/trust set. The trust center keeps methodology,
    # sources, privacy, and terms together instead of adding four nav items.
    try:
        with st.container(key="footer_links"):
            c1, c2, c3, _sp = st.columns([0.25, 0.35, 0.7, 4.3])
            with c1:
                st.page_link("pages/FAQ.py", label="FAQ")
            with c2:
                st.page_link("pages/Contact.py", label="Contact")
            with c3:
                st.page_link("pages/Trust_Center.py", label="Trust Center")
    except Exception:
        # Older Streamlit builds: fail silently
        pass

    # Disclaimer
    st.markdown(
        """
        <div class="meta"><b>Disclaimer:</b> Not financial advice.</div>
        """,
        unsafe_allow_html=True,
    )


def close_page() -> None:
    render_footer()
    st.markdown("</div>", unsafe_allow_html=True)


def render_recommendation_panel(
    *,
    ticker: str,
    sector: str,
    ai_summary: dict,
    current_price: str = "Unavailable",
    projected_gain: str = "Unavailable",
    drawdown_first: str = "Unavailable",
    mentions: int = 0,
    price_points: int = 0,
    horizon: str = "Short-term horizon",
    freshness: str = "Generated for this request",
    evidence_label: str = "",
    source_context: str = "Public social discussion and market-price data",
    would_change: list[str] | None = None,
    compact: bool = False,
    embedded: bool = False,
    element_id: str = "",
) -> None:
    """Render one self-contained, portable decision-summary component.

    The fields and hierarchy are the product contract. ``st.html`` is only the
    current adapter; a future frontend can render the same model without
    inheriting Streamlit containers or CSS selectors.
    """
    rec = str(ai_summary.get("recommendation") or "—").strip()
    conf = str(ai_summary.get("confidence") or "—").strip()
    raw_sentiment = ai_summary.get("avg_sentiment")
    scored = raw_sentiment is not None
    try:
        avg_sentiment = float(raw_sentiment) if scored else 0.0
    except (TypeError, ValueError):
        scored = False
        avg_sentiment = 0.0
    if scored and not math.isfinite(avg_sentiment):
        scored = False
        avg_sentiment = 0.0

    rec_key = rec.lower()
    rec_class = (
        "buy" if rec_key == "buy"
        else "avoid" if rec_key == "avoid"
        else "watch" if rec_key == "watch"
        else "neutral"
    )
    rec_explanation = {
        "buy": "Evidence currently supports a closer look",
        "watch": "Hold — monitor for a clearer setup",
        "avoid": "Current risks outweigh the setup",
    }.get(rec_key, "No directional recommendation")
    sentiment_label = (
        "Unscored" if not scored
        else "Bullish" if avg_sentiment >= 0.10
        else "Bearish" if avg_sentiment <= -0.10
        else "Neutral"
    )
    sentiment_detail = "No score" if not scored else f"Score {avg_sentiment:+.3f}"
    confidence_note = {
        "high": "Broad, consistent evidence",
        "moderate": "Useful evidence with unresolved uncertainty",
        "low": "Thin or conflicting evidence",
    }.get(conf.lower(), "Confidence not available")

    if not evidence_label:
        evidence_suffix = "s" if int(mentions) != 1 else ""
        evidence_label = (
            f"{int(mentions)} evidence item{evidence_suffix}"
            if mentions else "Evidence count unavailable"
        )
    if price_points:
        source_context = f"{source_context} · {int(price_points)} price observations"

    ticker_safe = html.escape(str(ticker or "—"))
    sector_safe = html.escape(str(sector or "Unknown").title())
    rec_safe = html.escape(rec)
    conf_safe = html.escape(conf)
    confidence_note_safe = html.escape(confidence_note)
    horizon_safe = html.escape(str(horizon))
    freshness_safe = html.escape(str(freshness))
    evidence_safe = html.escape(str(evidence_label))
    sources_safe = html.escape(str(source_context))

    rationale = [str(item).strip() for item in (ai_summary.get("rationale") or []) if str(item).strip()]
    visible_reasons = rationale[:1] if embedded else rationale[:3]
    reasons_html = "".join(
        f"<li>{html.escape(reason)}</li>" for reason in visible_reasons
    ) or "<li>No supporting explanation was returned for this analysis.</li>"
    change_items = [
        str(item).strip() for item in (would_change or [])
        if str(item).strip()
    ]
    change_html = (
        '<div class="ss-decision-change"><h3>What would change this</h3>'
        f'<p>{html.escape(change_items[0])}</p></div>'
        if change_items and not embedded else ""
    )
    density_class = " compact" if compact else ""
    if embedded:
        density_class += " embedded"
    safe_element_id = "".join(
        character for character in str(element_id)
        if character.isalnum() or character in ("-", "_")
    )
    element_attrs = (
        f' id="{safe_element_id}" tabindex="-1"'
        if safe_element_id else ""
    )

    financial_tiles = []
    for label, value in (
        ("Last price", current_price),
        ("30d range (volatility)", projected_gain),
        ("Drawdown before +5%", drawdown_first),
    ):
        if value != "Unavailable":
            financial_tiles.append(
                '<div class="ss-decision-financial">'
                f'<span>{html.escape(label)}</span>'
                f'<strong>{html.escape(str(value))}</strong>'
                '</div>'
            )
    financial_html = (
        '<div class="ss-decision-financials">'
        + "".join(financial_tiles)
        + "</div>"
        if financial_tiles and not embedded else ""
    )
    source_html = (
        f'<p class="ss-decision-source">{freshness_safe} · {sources_safe}</p>'
        if not embedded else ""
    )
    status_html = (
        '<p class="ss-decision-sr-only" role="status" aria-live="polite" '
        f'aria-atomic="true">Analysis complete for {ticker_safe}. '
        f'Recommendation {rec_safe}, confidence {conf_safe}.</p>'
        if safe_element_id else ""
    )

    st.html(
        f"""
        <style>
          .ss-decision-card {{
            width:100%;box-sizing:border-box;margin:.7rem 0 1.2rem;
            border:1px solid rgba(56,189,248,.24);border-radius:16px;
            background:linear-gradient(145deg,rgba(8,20,39,.98),rgba(8,15,30,.98));
            overflow:hidden;color:var(--ss-color-text,#e5e7eb);
          }}
          .ss-decision-head {{
            display:flex;justify-content:space-between;align-items:flex-start;
            gap:24px;padding:18px 20px;border-bottom:1px solid rgba(148,163,184,.14);
          }}
          .ss-decision-eyebrow,.ss-decision-label {{
            color:var(--ss-color-text-muted,#94a3b8);font-size:.69rem;font-weight:750;
            letter-spacing:.075em;text-transform:uppercase;
          }}
          .ss-decision-ticker {{font-size:1.55rem;font-weight:850;line-height:1.1;margin-top:4px;}}
          h2.ss-decision-ticker {{margin:4px 0 0;}}
          .ss-decision-signal {{text-align:right;}}
          .ss-decision-value {{font-size:1.5rem;font-weight:850;line-height:1.1;margin:4px 0;}}
          .ss-decision-value.buy {{color:var(--ss-color-recommendation-buy,#38bdf8);}}
          .ss-decision-value.watch {{color:var(--ss-color-recommendation-watch,#f59e0b);}}
          .ss-decision-value.avoid {{color:var(--ss-color-recommendation-avoid,#f87171);}}
          .ss-decision-value.neutral {{color:var(--ss-color-sentiment-neutral,#cbd5e1);}}
          .ss-decision-signal p {{margin:0;color:#94a3b8;font-size:.78rem;}}
          .ss-decision-body {{padding:18px 20px 20px;}}
          .ss-decision-context {{
            display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;
            margin-bottom:16px;
          }}
          .ss-decision-context > div,.ss-decision-financial {{
            border:1px solid rgba(148,163,184,.14);border-radius:10px;
            background:rgba(15,23,42,.62);padding:11px 12px;min-width:0;
          }}
          .ss-decision-context strong,.ss-decision-financial strong {{
            display:block;margin-top:4px;color:#e5e7eb;font-size:.9rem;line-height:1.3;
            overflow-wrap:anywhere;
          }}
          .ss-decision-context small {{display:block;margin-top:3px;color:#8192aa;font-size:.69rem;line-height:1.3;}}
          .ss-decision-context span,.ss-decision-financial span {{
            color:#8192aa;font-size:.68rem;font-weight:720;letter-spacing:.045em;
            text-transform:uppercase;
          }}
          .ss-decision-financials {{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-bottom:16px;}}
          .ss-decision-reasons h3 {{margin:0 0 7px;font-size:.78rem;letter-spacing:.06em;text-transform:uppercase;color:#8192aa;}}
          .ss-decision-reasons ul {{margin:0;padding-left:1.15rem;}}
          .ss-decision-reasons li {{margin:.4rem 0;color:#dbe3ee;font-size:.91rem;line-height:1.45;}}
          .ss-decision-change {{margin-top:15px;padding-top:13px;border-top:1px solid rgba(148,163,184,.14);}}
          .ss-decision-change h3 {{margin:0 0 5px;font-size:.78rem;letter-spacing:.06em;text-transform:uppercase;color:#8192aa;}}
          .ss-decision-change p {{margin:0;color:#a8b5c7;font-size:.86rem;line-height:1.45;}}
          .ss-decision-source {{margin:15px 0 0;color:#8192aa;font-size:.73rem;line-height:1.4;}}
          .ss-decision-sr-only {{
            position:absolute!important;width:1px!important;height:1px!important;
            padding:0!important;margin:-1px!important;overflow:hidden!important;
            clip:rect(0,0,0,0)!important;white-space:nowrap!important;border:0!important;
          }}
          .ss-decision-card.compact {{margin:0 0 .7rem;container-type:inline-size;}}
          .ss-decision-card.compact .ss-decision-head {{padding:16px;}}
          .ss-decision-card.compact .ss-decision-body {{padding:15px 16px 17px;}}
          .ss-decision-card.compact .ss-decision-context {{grid-template-columns:repeat(2,minmax(0,1fr));}}
          .ss-decision-card.compact .ss-decision-financials {{grid-template-columns:repeat(2,minmax(0,1fr));}}
          .ss-decision-card.compact .ss-decision-financials > :last-child:nth-child(odd) {{grid-column:1 / -1;}}
          .ss-decision-card.embedded {{
            border:0;border-radius:0;background:transparent;margin:0;
            overflow:visible;
          }}
          .ss-decision-card.embedded .ss-decision-head {{padding:0 0 14px;}}
          .ss-decision-card.embedded .ss-decision-body {{padding:14px 0 0;}}
          .ss-decision-card.compact.embedded .ss-decision-context {{
            grid-template-columns:repeat(4,minmax(0,1fr));
          }}
          .ss-decision-card.compact.embedded .ss-decision-financials {{
            grid-template-columns:repeat(3,minmax(0,1fr));
          }}
          .ss-decision-card.compact.embedded .ss-decision-financials > :last-child:nth-child(odd) {{
            grid-column:auto;
          }}
          @container (max-width:440px) {{
            .ss-decision-card.compact .ss-decision-head {{display:block;}}
            .ss-decision-card.compact .ss-decision-signal {{text-align:left;margin-top:14px;}}
            .ss-decision-card.compact .ss-decision-context,
            .ss-decision-card.compact .ss-decision-financials {{grid-template-columns:1fr;}}
            .ss-decision-card.compact .ss-decision-financials > :last-child:nth-child(odd) {{grid-column:auto;}}
          }}
          @media (max-width:720px) {{
            .ss-decision-head {{padding:15px 16px;}}
            .ss-decision-body {{padding:15px 16px 17px;}}
            .ss-decision-context {{grid-template-columns:repeat(2,minmax(0,1fr));}}
            .ss-decision-financials {{grid-template-columns:1fr;}}
            .ss-decision-card.compact.embedded .ss-decision-context,
            .ss-decision-card.compact.embedded .ss-decision-financials {{
              grid-template-columns:repeat(2,minmax(0,1fr));
            }}
          }}
          @media (max-width:420px) {{
            .ss-decision-head {{display:block;}}
            .ss-decision-signal {{text-align:left;margin-top:14px;}}
          }}
        </style>
        <article class="ss-decision-card{density_class}"{element_attrs} aria-label="Deep analysis summary for {ticker_safe}">
          {status_html}
          <header class="ss-decision-head">
            <div>
              <div class="ss-decision-eyebrow">Deep analysis · {sector_safe}</div>
              <h2 class="ss-decision-ticker">{ticker_safe}</h2>
            </div>
            <div class="ss-decision-signal">
              <div class="ss-decision-label">Recommendation</div>
              <div class="ss-decision-value {rec_class}">{rec_safe}</div>
              <p>{html.escape(rec_explanation)}</p>
            </div>
          </header>
          <div class="ss-decision-body">
            <div class="ss-decision-context">
              <div><span>Confidence</span><strong>{conf_safe}</strong><small>{confidence_note_safe}</small></div>
              <div><span>Social sentiment</span><strong>{html.escape(sentiment_label)} · {html.escape(sentiment_detail)}</strong></div>
              <div><span>Signal horizon</span><strong>{horizon_safe}</strong></div>
              <div><span>Evidence</span><strong>{evidence_safe}</strong></div>
            </div>
            {financial_html}
            <div class="ss-decision-reasons">
              <h3>Why this recommendation</h3>
              <ul>{reasons_html}</ul>
            </div>
            {change_html}
            {source_html}
          </div>
        </article>
        """
    )


def render_full_analysis_expander(
    analysis_results: dict,
    key_suffix: str = "",
    *,
    expanded: bool = False,
    label: str = "Full breakdown",
    decision_details: dict | None = None,
) -> None:
    """Styled 'Full breakdown' expander — visually obvious, premium look."""
    from utils.deep_analysis import ANALYSIS_PROMPTS

    # Styled trigger strip (more obvious than default Streamlit expander arrow)
    st.markdown(
        """
        <style>
        details > summary { list-style: none; }
        details > summary::-webkit-details-marker { display: none; }
        .ss-breakdown-scroll {
          width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch;
          border-radius:10px;
        }
        .ss-breakdown-scroll table { min-width:620px; }
        .ss-breakdown-scroll:focus-visible {
          outline:3px solid var(--focus-ring);outline-offset:3px;
        }
        .ss-breakdown-details {
          display:grid;grid-template-columns:repeat(3,minmax(0,1fr));
          gap:8px;margin:.55rem 0 1rem;
        }
        .ss-breakdown-detail {
          border-left:2px solid rgba(56,189,248,.30);
          padding:.2rem .7rem;min-width:0;
        }
        .ss-breakdown-detail span {
          display:block;color:#8192aa;font-size:.68rem;font-weight:720;
          letter-spacing:.045em;text-transform:uppercase;
        }
        .ss-breakdown-detail strong {
          display:block;margin-top:.2rem;color:#e5e7eb;font-size:.9rem;
          overflow-wrap:anywhere;
        }
        .ss-sample-post {overflow-wrap:anywhere;word-break:break-word;}
        @media (max-width:720px) {
          .ss-breakdown-details {grid-template-columns:1fr;}
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    with st.expander(label, expanded=expanded):
        details = decision_details or {}
        detail_metrics = details.get("metrics") or []
        additional_reasons = details.get("additional_reasons") or []
        detail_changes = details.get("would_change") or []
        detail_source = str(details.get("source") or "").strip()
        if detail_metrics or additional_reasons or detail_changes:
            st.markdown("**Decision details**")
            if detail_metrics:
                detail_tiles = "".join(
                    '<div class="ss-breakdown-detail"><span>'
                    f'{html.escape(str(item.get("label") or "Detail"))}</span>'
                    f'<strong>{html.escape(str(item.get("value") or "—"))}</strong></div>'
                    for item in detail_metrics
                )
                st.markdown(
                    '<div class="ss-breakdown-details">'
                    f'{detail_tiles}</div>',
                    unsafe_allow_html=True,
                )
            if additional_reasons:
                st.markdown("**Additional reasons**")
                for reason in additional_reasons:
                    st.markdown(f"- {reason}")
            if detail_changes:
                st.markdown("**What would change this**")
                for change in detail_changes:
                    st.markdown(f"- {change}")
            if detail_source:
                st.caption(detail_source)
            st.divider()

        coverage_rows = []
        for prompt_name, result in (analysis_results or {}).items():
            timeframe = (ANALYSIS_PROMPTS.get(prompt_name, {}) or {}).get("timeframe", "")
            evidence = int(result.get("mention_count", 0) or 0)
            overall = (result.get("overall_sentiment") or "").lower()
            if overall == "error":
                strength, tilt = "Unavailable", "Unavailable"
            elif evidence == 0:
                strength, tilt = "No Signal", "Neutral"
            else:
                strength = "Strong" if evidence > 5 else "Weak"
                tilt = overall.title() if overall in ("bullish", "bearish", "neutral") else "Neutral"
            coverage_rows.append((prompt_name, timeframe, evidence, strength, tilt))

        if coverage_rows:
            tilt_color = {
                "Bullish": "rgba(56,189,248,.95)",
                "Bearish": "rgba(239,68,68,.90)",
                "Neutral": "rgba(148,163,184,.80)",
            }
            rows_html = "".join(
                f'<tr style="border-bottom:1px solid rgba(148,163,184,.10);">'
                f'<td style="padding:9px 10px;color:rgba(229,231,235,.90);font-size:0.82rem;">{html.escape(str(pn))}</td>'
                f'<td style="padding:9px 10px;color:rgba(148,163,184,.70);font-size:0.82rem;">{html.escape(str(tf))}</td>'
                f'<td style="padding:9px 10px;color:rgba(148,163,184,.80);font-size:0.82rem;text-align:center;">{ev}</td>'
                f'<td style="padding:9px 10px;color:rgba(148,163,184,.80);font-size:0.82rem;">{html.escape(str(st_))}</td>'
                f'<td style="padding:9px 10px;font-size:0.82rem;font-weight:700;color:{tilt_color.get(tl,"rgba(148,163,184,.80)")};">{html.escape(str(tl))}</td>'
                f'</tr>'
                for pn, tf, ev, st_, tl in coverage_rows
            )
            st.markdown(
                f'<div class="ss-breakdown-scroll" role="region" aria-label="Signal coverage table" tabindex="0">'
                f'<table style="width:100%;border-collapse:collapse;background:rgba(15,23,42,.60);border-radius:10px;overflow:hidden;">'
                f'<thead><tr style="border-bottom:1px solid rgba(148,163,184,.20);">'
                + "".join(
                    f'<th style="padding:8px 10px;text-align:{"center" if h=="Evidence" else "left"};font-size:0.70rem;font-weight:700;letter-spacing:0.05em;text-transform:uppercase;color:rgba(148,163,184,.55);">{h}</th>'
                    for h in ["Signal Type", "Timeframe", "Evidence", "Strength", "Tilt"]
                )
                + f'</tr></thead><tbody>{rows_html}</tbody></table></div>',
                unsafe_allow_html=True,
            )
        else:
            st.caption("No coverage data available.")

        st.markdown('<div style="height:0.75rem"></div>', unsafe_allow_html=True)
        st.markdown(
            '<div style="font-size:0.80rem;font-weight:700;color:rgba(148,163,184,.65);letter-spacing:0.05em;text-transform:uppercase;margin-bottom:8px;">Detailed breakdown</div>',
            unsafe_allow_html=True,
        )
        for prompt_name, config in ANALYSIS_PROMPTS.items():
            st.markdown(f"**{prompt_name}** · {config.get('timeframe','')}")
            if prompt_name in (analysis_results or {}):
                result = analysis_results[prompt_name]
                c1, c2, c3 = st.columns(3)
                c1.metric("Sentiment Score", f"{result['sentiment_score']:.3f}")
                c2.metric("Overall", result["overall_sentiment"].title())
                c3.metric("Mentions", result["mention_count"])
                st.markdown(f"**Insights:** {result['insights']}")
                if result.get("key_themes"):
                    st.markdown(f"**Themes:** {', '.join(result['key_themes'])}")
                if result.get("sample_tweets"):
                    st.markdown("**Sample posts:**")
                    for i, tw in enumerate(result["sample_tweets"], 1):
                        st.markdown(
                            f'<p class="ss-sample-post">{i}. '
                            f'{html.escape(str(tw))}</p>',
                            unsafe_allow_html=True,
                        )
            else:
                st.caption("Unavailable.")
            st.markdown("<hr style='border:none;border-top:1px solid rgba(148,163,184,.10);margin:8px 0;'>", unsafe_allow_html=True)


def ui_error(message: str = GENERIC_ERROR_TEXT) -> None:
    st.error(message)


def safe_ui(fn, *, context: str = ""):
    """Run a UI operation safely.

    - Logs full exception stack trace for debugging.
    - Shows only a generic error to the user.

    Intended for unexpected errors only.
    """

    try:
        return fn()
    except Exception:
        LOG.exception("UI error%s", f" ({context})" if context else "")
        ui_error()
        return None


def render_evidence_check(
    card: dict,
    ticker: str = "",
    *,
    show_header: bool = True,
    show_change: bool = True,
) -> None:
    """The pillar readout: which gates passed, which failed, what would change it.

    TAKES THE CARD, not a Verdict. The remote path has no Verdict object -- it
    has JSON over HTTPS -- and rebuilding one just to satisfy this signature
    would put a second producer of these strings back in the codebase, which is
    what card() exists to prevent. Every field read here is one card() already
    publishes, so the local and remote paths render from identical input.

    This is the part of the page that makes a Watch worth a credit. The old
    output said "Watch / Moderate" with prose beneath that could contradict the
    numbers above it; here every line is cascade state, so the explanation and
    the decision cannot disagree.

    Four states, deliberately rendered differently. Watch/Moderate means "we
    found real evidence and it does not line up" -- a finding. Watch/Low means
    "we could not find enough to judge" -- a different product entirely, and
    showing them identically is how a corpus of 100 spam posts once earned
    Moderate confidence.
    """
    import html as _html

    import streamlit as st

    card = card or {}
    recommendation = card.get("verdict") or ""
    tone = {"Buy": ("56,189,248", "🟢"), "Avoid": ("239,68,68", "🔴"),
            "Watch": ("148,163,184", "🟡")}.get(recommendation, ("148,163,184", "🟡"))
    rgb, dot = tone
    thin = card.get("confidence") == "Low"

    rows = []
    for p in (card.get("pillars") or []):
        mark = "✅" if p.get("passed") else "❌"
        colour = ("rgba(226,232,240,.88)" if p.get("passed")
                  else "rgba(248,113,113,.95)")
        # Built OUTSIDE the f-string. A backslash inside an f-string
        # expression is a SyntaxError before Python 3.12, and runtime.txt pins
        # 3.11 -- so this line took down every page that imports this module,
        # invisibly, because the dev box is 3.12 and no test imports ui.py.
        needs = "" if p.get("passed") else (
            "<br><span style='opacity:.7;'>needs: "
            + _html.escape(str(p.get("requirement") or "")) + "</span>")
        rows.append(
            f"<tr>"
            f"<td style='padding:5px 10px 5px 0;vertical-align:top;'>{mark}</td>"
            f"<td style='padding:5px 14px 5px 0;color:{colour};white-space:nowrap;'>"
            f"{_html.escape(str(p.get('name') or ''))}</td>"
            f"<td style='padding:5px 0;color:rgba(148,163,184,.85);font-size:0.86rem;'>"
            f"{_html.escape(str(p.get('value')))}{needs}"
            f"</td></tr>"
        )

    change = ""
    if show_change and card.get("would_change"):
        items = "".join(f"<li style='margin:2px 0;'>{_html.escape(str(c))}</li>"
                        for c in card["would_change"])
        label = ("What would make this a Buy" if recommendation == "Watch"
                 else "What would change this")
        change = (f"<div style='margin-top:14px;padding-top:12px;"
                  f"border-top:1px solid rgba(148,163,184,.16);'>"
                  f"<div style='font-weight:700;font-size:0.86rem;margin-bottom:5px;'>"
                  f"{label}</div>"
                  f"<ul style='margin:0;padding-left:18px;color:rgba(148,163,184,.9);"
                  f"font-size:0.88rem;'>{items}</ul></div>")

    notes = ""
    if card.get("confidence_notes"):
        notes = ("<div style='margin-top:8px;color:rgba(148,163,184,.65);"
                 "font-size:0.8rem;font-style:italic;'>"
                 + _html.escape(" · ".join(str(n) for n in card["confidence_notes"]))
                 + "</div>")

    header = ""
    lead = ""
    if show_header:
        header = (
            f"<div style='display:flex;align-items:baseline;gap:10px;margin-bottom:2px;'>"
            f"<span style='font-size:1.05rem;font-weight:800;'>{dot} "
            f"{_html.escape(ticker) + ' — ' if ticker else ''}{_html.escape(recommendation)}</span>"
            f"<span style='color:rgba(148,163,184,.8);font-size:0.86rem;'>"
            f"{_html.escape(str(card.get('confidence') or ''))} confidence</span></div>"
        )
        lead = (
            f"<div style='color:rgba(203,213,225,{'.95' if thin else '.8'});"
            f"font-size:0.9rem;margin-bottom:12px;'>"
            f"{_html.escape(str(card.get('reason') or ''))}</div>"
        )
    else:
        header = (
            "<div style='color:rgba(148,163,184,.82);font-size:.72rem;"
            "font-weight:760;letter-spacing:.07em;text-transform:uppercase;"
            "margin-bottom:10px;'>Evidence check</div>"
        )

    st.markdown(
        "<style>"
        ".ss-evidence-check__scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;}"
        ".ss-evidence-check table{width:100%;border-collapse:collapse;}"
        "@media(max-width:520px){.ss-evidence-check table{min-width:560px;}}"
        "</style>"
        f"<div class='ss-evidence-check' style='border:1px solid rgba({rgb},.28);"
        f"border-radius:14px;padding:16px 20px;margin:0.75rem 0;"
        f"background:rgba({rgb},.04);'>"
        f"{header}{lead}"
        "<div class='ss-evidence-check__scroll' role='region' "
        "aria-label='Evidence checks' tabindex='0'>"
        f"<table>{''.join(rows)}</table></div>"
        f"{change}{notes}</div>",
        unsafe_allow_html=True,
    )


def analysis_card_view_model(card: dict) -> dict:
    """Normalize one canonical analysis card for every UI entry path.

    Market Scan and Deep Analyze previously assembled the same labels and tile
    values independently. That allowed the scan-launched result to become a
    stripped-down, visually different product. This adapter is now the single
    card-to-view contract shared by both routes.
    """
    card = card or {}
    evidence = card.get("evidence") or {}
    movement = card.get("movement") or {}
    independent = evidence.get("independent_voices")
    raw_mentions = evidence.get("mentions")
    if independent is not None:
        shown_mentions = int(independent or 0)
        suffix = "s" if shown_mentions != 1 else ""
        evidence_label = (
            f"{shown_mentions} independent evidence cluster{suffix}"
        )
    elif raw_mentions is not None:
        shown_mentions = int(raw_mentions or 0)
        suffix = "s" if shown_mentions != 1 else ""
        evidence_label = f"{shown_mentions} post{suffix} analyzed"
    else:
        shown_mentions = 0
        evidence_label = "Evidence count unavailable"

    tiles = {
        str(tile.get("key") or ""): str(
            tile.get("value") or "Unavailable"
        )
        for tile in (card.get("tiles") or [])
        if isinstance(tile, dict)
    }
    horizon_days = int(movement.get("horizon_days") or 0)
    day_suffix = "s" if horizon_days != 1 else ""
    horizon = (
        f"{horizon_days} trading day{day_suffix}"
        if horizon_days else "Short-term horizon"
    )
    return {
        "summary": {
            "recommendation": card.get("verdict") or "—",
            "confidence": card.get("confidence") or "—",
            "avg_sentiment": card.get("avg_sentiment"),
            "rationale": card.get("rationale") or [],
        },
        "current_price": tiles.get("last_price", "Unavailable"),
        "projected_range": tiles.get("range_30d", "Unavailable"),
        "drawdown_first": tiles.get("drawdown_first", "Unavailable"),
        "shown_mentions": shown_mentions,
        "evidence_label": evidence_label,
        "price_points": int(evidence.get("price_points") or 0),
        "horizon": horizon,
    }


def render_movement_profile(card: dict, ticker: str) -> None:
    """Render the shared, symmetric volatility context for an analysis."""
    movement = (card or {}).get("movement") or {}
    targets = movement.get("targets") or {}
    if not targets:
        return

    horizon_days = int(movement.get("horizon_days") or 10)
    rows = [
        "<tr class='ss-movement-head'><th>Target</th><th>Reached, 30d</th>"
        "<th>Fallen, 30d</th>"
        f"<th>Within {horizon_days}d</th></tr>"
    ]
    for target, values in targets.items():
        up_day = values.get("up_median_day")
        down_day = values.get("down_median_day")
        up = f"{values['up_rate']:.0%}" + (
            f" · day {up_day}" if up_day else ""
        )
        down = f"{values['down_rate']:.0%}" + (
            f" · day {down_day}" if down_day else ""
        )
        short_up = values.get("up_rate_by_decision")
        short_down = values.get("down_rate_by_decision")
        short = (
            "—" if short_up is None or short_down is None
            else f"{short_up:.0%} up · {short_down:.0%} down"
        )
        rows.append(
            "<tr>"
            f"<th scope='row'>±{html.escape(str(target))}</th>"
            f"<td class='up'>{html.escape(up)}</td>"
            f"<td class='down'>{html.escape(down)}</td>"
            f"<td>{html.escape(short)}</td></tr>"
        )

    five_percent = targets.get("5%") or {}
    up_first = five_percent.get("up_first_rate")
    touched = five_percent.get("touched_rate")
    first_note = ""
    if up_first is not None:
        touched_copy = f" ({touched:.0%} of them)" if touched is not None else ""
        first_note = (
            "<p class='ss-movement-note'>Of the paths that reach ±5% at all"
            f"{touched_copy}, {up_first:.0%} reach +5% <em>first</em>. "
            "Price history alone carries no direction; only the evidence "
            "above does.</p>"
        )

    ticker_safe = html.escape(str(ticker or "the stock"))
    band = float(movement.get("band_pct") or 0)
    st.markdown(
        """
        <style>
        .ss-movement-profile {
          border:1px solid rgba(148,163,184,.22);border-radius:14px;
          padding:16px 20px;margin:.75rem 0;background:rgba(15,23,42,.28);
        }
        .ss-movement-profile h3 {margin:0 0 3px;font-size:1rem;}
        .ss-movement-copy,.ss-movement-note {
          color:rgba(148,163,184,.82);font-size:.82rem;line-height:1.45;
        }
        .ss-movement-copy {margin:0 0 10px;}
        .ss-movement-note {margin:10px 0 0;}
        .ss-movement-scroll {overflow-x:auto;-webkit-overflow-scrolling:touch;}
        .ss-movement-table {border-collapse:collapse;min-width:560px;font-size:.88rem;}
        .ss-movement-table th,.ss-movement-table td {
          padding:7px 14px;text-align:left;border-bottom:1px solid rgba(148,163,184,.13);
        }
        .ss-movement-table th:first-child,.ss-movement-table td:first-child {padding-left:0;}
        .ss-movement-table .ss-movement-head th {
          color:rgba(148,163,184,.62);font-size:.69rem;font-weight:720;
          letter-spacing:.05em;text-transform:uppercase;
        }
        .ss-movement-table td.up {color:rgba(56,189,248,.95);}
        .ss-movement-table td.down {color:rgba(248,113,113,.95);}
        </style>
        """
        "<section class='ss-movement-profile' aria-labelledby='ss-movement-title'>"
        "<h3 id='ss-movement-title'>Movement profile</h3>"
        f"<p class='ss-movement-copy'>How far {ticker_safe} normally travels, "
        f"from its recent volatility (±{band:.1f}% over 30 days). Not a "
        "forecast—the same volatility carries it both ways.</p>"
        "<div class='ss-movement-scroll' role='region' aria-label='Movement "
        "profile table' tabindex='0'><table class='ss-movement-table'>"
        f"{''.join(rows)}</table></div>{first_note}</section>",
        unsafe_allow_html=True,
    )


def render_delivered_analysis_result(
    *,
    card: dict,
    analysis_results: dict,
    ticker: str,
    sector: str,
    key_suffix: str,
    element_id: str = "",
    freshness: str = "Analysis generated now",
    on_summary_delivered: Callable[[], None] | None = None,
) -> None:
    """Render the complete paid result identically from every entry path."""
    view = analysis_card_view_model(card)
    render_recommendation_panel(
        ticker=ticker,
        sector=sector,
        ai_summary=view["summary"],
        current_price=view["current_price"],
        projected_gain=view["projected_range"],
        drawdown_first=view["drawdown_first"],
        mentions=view["shown_mentions"],
        price_points=view["price_points"],
        horizon=view["horizon"],
        freshness=freshness,
        evidence_label=view["evidence_label"],
        would_change=(card or {}).get("would_change") or [],
        element_id=element_id,
    )
    # This is the paid-product boundary. Route code can close its ledger work
    # immediately after the canonical recommendation is visible, before any
    # optional evidence, movement, CSS or disclosure element yields control
    # back to Streamlit.
    if on_summary_delivered is not None:
        on_summary_delivered()

    if (card or {}).get("pillars"):
        try:
            # Identity, verdict and change criteria already belong to the
            # decision card immediately above. Keep this section focused on
            # its unique contribution: the evidence gates themselves.
            render_evidence_check(
                card,
                ticker,
                show_header=False,
                show_change=False,
            )
        except Exception:
            LOG.warning("evidence check render failed", exc_info=True)
    try:
        render_movement_profile(card, ticker)
    except Exception:
        LOG.warning("movement profile render failed", exc_info=True)

    safe_key = "".join(
        character if character.isalnum() else "_"
        for character in str(key_suffix)
    )
    decision_details = {
        "additional_reasons": ((card or {}).get("rationale") or [])[3:],
        "would_change": ((card or {}).get("would_change") or [])[1:],
    }
    has_decision_details = any(decision_details.values())
    st.markdown(
        """
        <style>
        [class*="st-key-delivered_analysis_breakdown_"] [data-testid="stExpander"] {
          border:1px solid rgba(56,189,248,.36)!important;
          border-radius:var(--radius-control)!important;
          background:rgba(8,15,30,.58)!important;
        }
        [class*="st-key-delivered_analysis_breakdown_"] details > summary {
          min-height:var(--ss-control-min-height);color:var(--accent)!important;
          font-size:.9rem;font-weight:720!important;
        }
        [class*="st-key-delivered_analysis_breakdown_"] [data-testid="stExpander"]:hover {
          background:rgba(56,189,248,.07)!important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    try:
        with st.container(key=f"delivered_analysis_breakdown_{safe_key}"):
            if analysis_results or has_decision_details:
                render_full_analysis_expander(
                    analysis_results or {},
                    key_suffix=key_suffix,
                    label="View full breakdown",
                    decision_details=decision_details,
                )
            else:
                st.caption(
                    "Detailed signal excerpts are unavailable for this earlier "
                    "result; the delivered recommendation remains available above."
                )
            st.caption("Already analyzed · no additional credit")
    except Exception:
        # The recommendation card above is the paid product. An optional
        # disclosure renderer must never turn a visibly delivered result into
        # a billing failure.
        LOG.warning("full analysis breakdown render failed", exc_info=True)
        st.caption(
            "The recommendation is available above; its optional detailed "
            "breakdown could not be displayed."
        )
