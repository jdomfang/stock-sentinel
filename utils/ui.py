"""Shared UI scaffolding (theme/layout/disclaimers/errors).

Streamlit doesn't support real HTML template inheritance. This module centralizes
our shared CSS + wrapper layout so all pages stay consistent.
"""

from __future__ import annotations

import logging
from pathlib import Path

import streamlit as st


LOG = logging.getLogger(__name__)

GENERIC_ERROR_TEXT = "Something went wrong. Please try again later."
_TOKEN_CSS = (
    Path(__file__).resolve().parents[1]
    / "assets" / "styles" / "stock-sentinel-tokens.css"
).read_text(encoding="utf-8")


def apply_theme() -> None:
    """Apply the global Discovery-style theme.

    Safe to call multiple times.
    """
    # Adapter boundary: load portable product tokens first, then map them onto
    # the current Streamlit renderer below. Future frontends import the CSS
    # asset directly and replace only this adapter layer.
    st.markdown(f"<style>{_TOKEN_CSS}</style>", unsafe_allow_html=True)
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
          max-width: 1180px;
          margin: 0 auto;
          padding: 0 clamp(16px, 3vw, 32px) 2rem;
        }

        @media (max-width: 640px) {
          div[data-testid="stMainBlockContainer"] {
            padding-left: 1.05rem !important;
            padding-right: 1.05rem !important;
          }
        }

        .clawd-app-wrapper {
          /* Keep wrapper aligned with global main container */
          max-width: 1100px;
          margin: 0 auto;
          padding: 0 1rem;
        }

        /* Inputs */
        [data-baseweb="select"] > div,
        [data-baseweb="input"] > div {
          background-color: rgba(2,6,23,.55) !important;
          border-color: var(--border) !important;
          color: var(--text) !important;
        }

        /* BaseWeb portals render outside the page container. Keep every
           select menu readable without page-specific DOM observers. */
        [data-baseweb="popover"] [role="listbox"],
        ul[data-testid="stSelectboxVirtualDropdown"] {
          background: #0F172A !important;
          color: var(--text) !important;
          border: 1px solid var(--border) !important;
        }
        [data-baseweb="popover"] [role="option"],
        ul[data-testid="stSelectboxVirtualDropdown"] li,
        ul[data-testid="stSelectboxVirtualDropdown"] li * {
          color: var(--text) !important;
          opacity: 1 !important;
        }
        [data-baseweb="popover"] [role="option"]:hover,
        [data-baseweb="popover"] [role="option"][aria-selected="true"],
        ul[data-testid="stSelectboxVirtualDropdown"] li:hover {
          background: rgba(56,189,248,.14) !important;
        }

        /* Buttons */
        .stButton > button {
          min-height: 44px;
          border-radius: var(--radius-control);
          border: 1px solid rgba(56,189,248,0.28);
          background: rgba(15, 23, 42, 0.85);
          color: #E5E7EB;
          font-weight: 650;
          opacity: 1;
        }

        button[data-testid="stBaseButton-primary"],
        .stButton > button[kind="primary"] {
          background: linear-gradient(180deg, rgba(56,189,248,.95), rgba(14,116,144,.95)) !important;
          background-color: transparent !important;
          border: 1px solid rgba(56,189,248,.45) !important;
          color: #001018 !important;
          font-weight: 650 !important;
        }

        a:focus-visible,
        button:focus-visible,
        input:focus-visible,
        [role="button"]:focus-visible,
        [tabindex]:focus-visible {
          outline: 3px solid var(--focus-ring) !important;
          outline-offset: 3px !important;
          box-shadow: none !important;
        }

        @media (prefers-reduced-motion: reduce) {
          *, *::before, *::after {
            scroll-behavior: auto !important;
            transition-duration: .01ms !important;
            animation-duration: .01ms !important;
            animation-iteration-count: 1 !important;
          }
        }

        footer { visibility: hidden; }
        </style>
        """,
        unsafe_allow_html=True,
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
          .clawd-footer-links [data-testid="stPageLink"] a {
            color: rgba(229,231,235,.88) !important;
            text-decoration: none !important;
            font-weight: 650;
          }
          .clawd-footer-links [data-testid="stPageLink"] a:hover {
            text-decoration: underline !important;
          }
          .clawd-footer-links {
            display: flex;
            gap: 14px;
            flex-wrap: wrap;
            align-items: center;
          }
        </style>
        <div class="clawd-footer">
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Links row (keep FAQ + Contact adjacent)
    try:
        c1, c2, _sp = st.columns([0.25, 0.35, 5.0])
        with c1:
            st.page_link("pages/FAQ.py", label="FAQ")
        with c2:
            st.page_link("pages/Contact.py", label="Contact")
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
) -> None:
    """Render the premium deep-analysis recommendation panel.

    Identical layout used on Discovery inline panel, Deep_Analysis page,
    and Home demo — single source of truth.
    """
    rec = ai_summary.get("recommendation") or "—"
    conf = ai_summary.get("confidence") or "—"
    # None means the adjudicator reported NO score, which is not the same as a
    # score of zero. float(None) raised here; float(0.0) is worse -- it prints
    # "Neutral (+0.00)", stating a finding nobody made. card() returns None on
    # purpose and Discovery already honours it.
    _raw_sent = ai_summary.get("avg_sentiment")
    _scored = _raw_sent is not None
    avg_sent = float(_raw_sent) if _scored else 0.0
    rationale = ai_summary.get("rationale", [])

    # Colors
    rec_color = (
        "rgba(56,189,248,.95)" if "buy" in rec.lower()
        else "rgba(239,68,68,.90)" if "avoid" in rec.lower()
        else "rgba(245,158,11,.90)"
    )
    sent_color = (
        "rgba(56,189,248,.95)" if avg_sent >= 0.10
        else "rgba(239,68,68,.88)" if avg_sent <= -0.10
        else "rgba(148,163,184,.85)"
    )
    sent_label = (
        "Unscored" if not _scored
        else f"Bullish ({avg_sent:+.2f})" if avg_sent >= 0.10
        else f"Bearish ({avg_sent:+.2f})" if avg_sent <= -0.10
        else f"Neutral ({avg_sent:+.2f})"
    )
    conf_color = (
        "rgba(56,189,248,.90)" if conf.lower() == "high"
        else "rgba(245,158,11,.90)" if conf.lower() == "moderate"
        else "rgba(148,163,184,.80)"
    )

    # Signal strength bar (0–100 based on sentiment magnitude + confidence)
    _bar_pct = min(100, int(abs(avg_sent) * 250 + ({"high": 30, "moderate": 15, "low": 0}.get(conf.lower(), 0))))
    _bar_color = rec_color

    # ── Contracted panel: natural content width, no dead space ──
    _sector_label = (' · ' + sector.title()) if sector and sector.lower() not in ('unknown', '') else ''
    st.markdown(
        f"""
        <div style="
          display:inline-block;
          min-width:320px;
          max-width:520px;
          margin-top:0.75rem;
          margin-bottom:1.2rem;
          border:1px solid rgba(56,189,248,.28);
          border-radius:16px;
          background:linear-gradient(180deg,rgba(56,189,248,.06) 0%,rgba(15,23,42,.96) 60px);
          overflow:hidden;
        ">
          <!-- Header row: ticker left, signal right, naturally close -->
          <div style="
            padding:14px 20px 12px 20px;
            display:flex;align-items:center;justify-content:space-between;gap:32px;
            border-bottom:1px solid rgba(56,189,248,.12);
          ">
            <div>
              <div style="font-size:0.68rem;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:rgba(56,189,248,.75);">Deep Analysis{_sector_label}</div>
              <div style="font-size:1.45rem;font-weight:850;letter-spacing:-0.02em;color:rgba(248,250,252,.98);line-height:1.15;">{ticker}</div>
            </div>
            <div style="text-align:right;">
              <div style="font-size:0.68rem;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:rgba(148,163,184,.50);margin-bottom:2px;">Signal</div>
              <div style="font-size:1.45rem;font-weight:850;color:{rec_color};line-height:1.15;">{rec}</div>
              <div style="font-size:0.76rem;color:rgba(148,163,184,.60);margin-top:1px;">Confidence: {conf}</div>
            </div>
          </div>
          <!-- Body -->
          <div style="padding:14px 20px 18px 20px;">
        """,
        unsafe_allow_html=True,
    )

    # ── 3 premium metric cards with signal bar + sublabel ──
    _mc_base = (
        "border-radius:12px;padding:14px 16px 12px 16px;"
        "background:rgba(15,23,42,.70);"
        "flex:1;min-width:0;display:flex;flex-direction:column;gap:6px;"
    )

    def _bar_html(pct, color):
        return (
            f'<div style="width:100%;height:4px;background:rgba(148,163,184,.12);border-radius:999px;margin-top:6px;">'
            f'<div style="width:{pct}%;height:4px;background:{color};border-radius:999px;transition:width 0.6s ease;"></div>'
            f'</div>'
        )

    # "Strong upside signal" and "Strong data backing" were unconditional claims
    # about signal strength, on a system whose own adjudicator sets
    # ALLOW_HIGH = False because it "has not earned the word". Suppressing the
    # word High for that reason and then rendering "Strong" in its place is
    # worse than either alternative -- it looks like restraint was exercised.
    # Moderate is the CEILING here, so it is named as one.
    _rec_sublabel = {"buy": "Evidence leans upside", "watch": "Hold — monitor closely", "avoid": "Risk outweighs reward"}.get(rec.lower(), "")
    _conf_sublabel = {"high": "Strong data backing", "moderate": "Highest we issue — unvalidated", "low": "Thin data — use caution"}.get(conf.lower(), "")
    _conf_bar = {"high": 90, "moderate": 55, "low": 25}.get(conf.lower(), 30)

    st.markdown(
        f'<div style="display:flex;gap:10px;margin:10px 0 16px 0;flex-wrap:nowrap;">'

        # Recommendation card
        f'<div style="{_mc_base}border:1px solid {rec_color.replace(".95",",.30").replace(".90",",.28")};">'
        f'<div style="font-size:0.70rem;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:rgba(148,163,184,.60);">Recommendation</div>'
        f'<div style="font-size:1.18rem;font-weight:850;color:{rec_color};letter-spacing:-0.01em;">{rec}</div>'
        f'<div style="font-size:0.75rem;color:rgba(148,163,184,.65);margin-top:1px;">{_rec_sublabel}</div>'
        f'{_bar_html(_bar_pct, rec_color)}'
        f'</div>'

        # Confidence card
        f'<div style="{_mc_base}border:1px solid rgba(148,163,184,.18);">'
        f'<div style="font-size:0.70rem;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:rgba(148,163,184,.60);">Confidence</div>'
        f'<div style="font-size:1.18rem;font-weight:850;color:{conf_color};letter-spacing:-0.01em;">{conf}</div>'
        f'<div style="font-size:0.75rem;color:rgba(148,163,184,.65);margin-top:1px;">{_conf_sublabel}</div>'
        f'{_bar_html(_conf_bar, conf_color)}'
        f'</div>'

        # Sentiment card
        f'<div style="{_mc_base}border:1px solid rgba(148,163,184,.18);">'
        f'<div style="font-size:0.70rem;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:rgba(148,163,184,.60);">Market Mood</div>'
        f'<div style="font-size:1.18rem;font-weight:850;color:{sent_color};letter-spacing:-0.01em;">{sent_label.split(" ")[0]}</div>'
        f'<div style="font-size:0.75rem;color:rgba(148,163,184,.65);margin-top:1px;">{f"Score {avg_sent:+.3f}" if _scored else "No score"}</div>'
        f'{_bar_html(min(100,int(abs(avg_sent)*280)), sent_color)}'
        f'</div>'

        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Price / projection / hold period row ──
    if (current_price != "Unavailable" or projected_gain != "Unavailable"
            or drawdown_first != "Unavailable"):
        _fc = "border-radius:10px;padding:10px 14px;background:rgba(15,23,42,.55);border:1px solid rgba(148,163,184,.12);flex:1;"
        _fl = "font-size:0.68rem;font-weight:700;letter-spacing:0.05em;text-transform:uppercase;color:rgba(148,163,184,.55);margin-bottom:3px;"
        _fv = "font-size:1.00rem;font-weight:800;color:rgba(248,250,252,.92);"
        st.markdown(
            f'<div style="display:flex;gap:8px;margin-bottom:14px;flex-wrap:nowrap;">'
            f'<div style="{_fc}"><div style="{_fl}">Last Price</div><div style="{_fv}">{current_price}</div></div>'
            # "Proj. Gain 30d" promised a forecast. The value beneath it is a
            # symmetric volatility range centred on zero -- the model makes no
            # directional claim at all -- so the label was the last place the
            # old promise survived.
            f'<div style="{_fc}"><div style="{_fl}">30d range (vol)</div><div style="{_fv}">{projected_gain}</div></div>'
            # This tile has now held two numbers that meant nothing. "Hold
            # Period" was the median day the WINNING simulations first touched
            # +5%, with the paths that never got there dropped -- it invited
            # "hold this long and you are up". Its replacement, "Review window",
            # was the constant (14, 28) printed for every ticker on every run.
            # It now carries a measured figure: of the simulated paths that DID
            # reach +5%, the median worst drawdown suffered first. That is the
            # number a stop is placed from, and it is the only one of the three
            # that changes with the stock.
            f'<div style="{_fc}"><div style="{_fl}">Drawdown first</div><div style="{_fv}">{drawdown_first}</div></div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Data quality
    if mentions or price_points:
        st.markdown(
            f'<div style="color:rgba(148,163,184,.48);font-size:0.73rem;margin-bottom:12px;">'
            f'{mentions} posts analysed · {price_points} price points</div>',
            unsafe_allow_html=True,
        )

    # Rationale
    st.markdown(
        '<div style="font-size:0.80rem;font-weight:700;color:rgba(148,163,184,.65);letter-spacing:0.05em;text-transform:uppercase;margin-bottom:7px;">Why this signal</div>',
        unsafe_allow_html=True,
    )
    for bullet in rationale:
        st.markdown(f"- {bullet}")

    # Close body div + outer card div
    st.markdown('</div></div>', unsafe_allow_html=True)


def render_full_analysis_expander(analysis_results: dict, key_suffix: str = "") -> None:
    """Styled 'Full breakdown' expander — visually obvious, premium look."""
    from utils.deep_analysis import ANALYSIS_PROMPTS

    # Styled trigger strip (more obvious than default Streamlit expander arrow)
    st.markdown(
        """
        <style>
        details > summary { list-style: none; }
        details > summary::-webkit-details-marker { display: none; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("📋  Full breakdown  ↓  click to expand", expanded=False):
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
                f'<td style="padding:9px 10px;color:rgba(229,231,235,.90);font-size:0.82rem;">{pn}</td>'
                f'<td style="padding:9px 10px;color:rgba(148,163,184,.70);font-size:0.82rem;">{tf}</td>'
                f'<td style="padding:9px 10px;color:rgba(148,163,184,.80);font-size:0.82rem;text-align:center;">{ev}</td>'
                f'<td style="padding:9px 10px;color:rgba(148,163,184,.80);font-size:0.82rem;">{st_}</td>'
                f'<td style="padding:9px 10px;font-size:0.82rem;font-weight:700;color:{tilt_color.get(tl,"rgba(148,163,184,.80)")};">{tl}</td>'
                f'</tr>'
                for pn, tf, ev, st_, tl in coverage_rows
            )
            st.markdown(
                f'<table style="width:100%;border-collapse:collapse;background:rgba(15,23,42,.60);border-radius:10px;overflow:hidden;">'
                f'<thead><tr style="border-bottom:1px solid rgba(148,163,184,.20);">'
                + "".join(
                    f'<th style="padding:8px 10px;text-align:{"center" if h=="Evidence" else "left"};font-size:0.70rem;font-weight:700;letter-spacing:0.05em;text-transform:uppercase;color:rgba(148,163,184,.55);">{h}</th>'
                    for h in ["Signal Type", "Timeframe", "Evidence", "Strength", "Tilt"]
                )
                + f'</tr></thead><tbody>{rows_html}</tbody></table>',
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
                        st.text(f"{i}. {tw}")
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


def render_evidence_check(card: dict, ticker: str = "") -> None:
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
    if card.get("would_change"):
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

    st.markdown(
        f"<div style='border:1px solid rgba({rgb},.28);border-radius:14px;"
        f"padding:16px 20px;margin:0.75rem 0;background:rgba({rgb},.04);'>"
        f"<div style='display:flex;align-items:baseline;gap:10px;margin-bottom:2px;'>"
        f"<span style='font-size:1.05rem;font-weight:800;'>{dot} "
        f"{_html.escape(ticker) + ' — ' if ticker else ''}{_html.escape(recommendation)}</span>"
        f"<span style='color:rgba(148,163,184,.8);font-size:0.86rem;'>"
        f"{_html.escape(str(card.get('confidence') or ''))} confidence</span></div>"
        # Watch/Low leads with the reason, because "we could not judge" is the
        # message -- not a hedged verdict.
        f"<div style='color:rgba(203,213,225,{'.95' if thin else '.8'});"
        f"font-size:0.9rem;margin-bottom:12px;'>{_html.escape(str(card.get('reason') or ''))}</div>"
        f"<table style='width:100%;border-collapse:collapse;'>{''.join(rows)}</table>"
        f"{change}{notes}</div>",
        unsafe_allow_html=True,
    )
