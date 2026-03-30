"""Shared UI scaffolding (theme/layout/disclaimers/errors).

Streamlit doesn't support real HTML template inheritance. This module centralizes
our shared CSS + wrapper layout so all pages stay consistent.
"""

from __future__ import annotations

import logging

import streamlit as st
import streamlit.components.v1 as components


LOG = logging.getLogger(__name__)

GENERIC_ERROR_TEXT = "Something went wrong. Please try again later."


def apply_theme() -> None:
    """Apply the global Discovery-style theme.

    Safe to call multiple times.
    """
    st.markdown(
        """
        <style>
        :root {
          --bg: #0B1220;
          --panel: #0F172A;
          --panel2: rgba(15, 23, 42, 0.55);
          --border: rgba(148, 163, 184, 0.18);
          --text: #E5E7EB;
          --muted: #94A3B8;
          --accent: #38BDF8;
          --good: #22C55E;
          --bad: #EF4444;
          --warn: #F59E0B;
        }

        h1, h2, h3, h4, h5, h6, p, span, div, label {
          color: var(--text);
        }
        .stCaption, [data-testid="stCaptionContainer"] {
          color: var(--muted) !important;
        }

        [data-testid="stAppViewContainer"] {
          background: radial-gradient(1200px 500px at 20% 0%, rgba(56,189,248,.12), transparent 50%),
                      radial-gradient(900px 400px at 80% 10%, rgba(34,197,94,.10), transparent 45%),
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
          /* Global container: match Option B tighter layout */
          max-width: 1100px;
          margin: 0 auto;
          padding-left: 2rem;
          padding-right: 2rem;
          padding-top: 0rem;
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

        /* Buttons */
        .stButton > button {
          border-radius: 12px;
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

        footer { visibility: hidden; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # Branded page-transition overlay.
    # Fires on every page load/rerun — overlays the page with the brand dark color
    # then fades out, giving a smooth "entering the app" feel between pages.
    # Uses components.html (not st.markdown) so the <script> actually executes.
    components.html(
        """
        <script>
        (function () {
          try {
            const doc = (window.parent && window.parent.document) ? window.parent.document : document;
            const id = 'clawd-transition-overlay';

            const mount = () => {
              // Kill any stale overlay from a previous run
              try { const s = doc.getElementById(id); if (s) { s.style.transition = 'none'; s.remove(); } } catch (e) {}

              const el = doc.createElement('div');
              el.id = id;
              el.style.cssText = [
                'position:fixed',
                'inset:0',
                'z-index:99998',
                'background:#020617',
                'pointer-events:none',
                'opacity:1',
                'transition:opacity 0.40s cubic-bezier(0.4,0,0.2,1)',
                'will-change:opacity',
              ].join(';');
              (doc.body || doc.documentElement).appendChild(el);

              // Begin fade-out after a single paint frame
              requestAnimationFrame(() => {
                requestAnimationFrame(() => {
                  el.style.opacity = '0';
                });
              });

              // Remove after transition completes
              el.addEventListener('transitionend', () => { try { el.remove(); } catch (e) {} });

              // Safety net cleanup
              setTimeout(() => { try { const m = doc.getElementById(id); if (m) m.remove(); } catch (e) {} }, 1500);
            };

            // Mount as soon as the parent document body exists
            if (doc && (doc.body || doc.documentElement)) mount();
            else setTimeout(mount, 0);
          } catch (e) {}
        })();
        </script>
        """,
        height=0,
    )

    # Minimal dropdown readability fix (keeps select menus dark on Windows)
    components.html(
        """
        <script>
        (function () {
          const APPLY = () => {
            const ul = document.querySelector('ul[data-testid="stSelectboxVirtualDropdown"]');
            if (ul) {
              ul.style.setProperty('background-color', '#0F172A', 'important');
              ul.style.setProperty('color', '#E5E7EB', 'important');
              ul.querySelectorAll('li, li *').forEach((el) => {
                el.style.setProperty('color', '#E5E7EB', 'important');
                el.style.setProperty('opacity', '1', 'important');
              });
            }
          };
          const obs = new MutationObserver(APPLY);
          obs.observe(document.documentElement, { childList: true, subtree: true });
          window.addEventListener('load', APPLY);
          setTimeout(APPLY, 250);
        })();
        </script>
        """,
        height=0,
    )


def open_page(*, title: str, subtitle: str | None = None, eyebrow: str = "Stock Sentinel") -> None:
    apply_theme()

    st.markdown('<div class="clawd-app-wrapper">', unsafe_allow_html=True)

    # Discovery-style hero — font sizes match Home/Discovery exactly
    st.markdown(
        f"""
        <style>
        .clawd-page-hero-title {{
          font-size: clamp(42px, 5.1vw, 3.55rem);
          font-weight: 850;
          letter-spacing: -0.035em;
          line-height: 1.08;
          margin: 0 0 8px 0;
          color: rgba(248,250,252,.98);
        }}
        .clawd-page-hero-sub {{
          color: rgba(148,163,184,.92);
          font-size: clamp(15px, 1.35vw, 1.05rem);
          line-height: 1.45;
          margin: 0 0 10px 0;
          max-width: 760px;
        }}
        .clawd-page-hero-eyebrow {{
          color: rgba(56,189,248,.95);
          font-weight: 750;
          letter-spacing: 0.06em;
          text-transform: uppercase;
          font-size: 0.78rem;
          margin-bottom: 8px;
        }}
        </style>
        <div style="margin: -22px 0 12px 0;">
          <div class="clawd-page-hero-eyebrow">{eyebrow}</div>
          <div class="clawd-page-hero-title">{title}</div>
          {f'<div class="clawd-page-hero-sub">{subtitle}</div>' if subtitle else ''}
        </div>
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
    hold_days: str = "Unavailable",
    mentions: int = 0,
    price_points: int = 0,
) -> None:
    """Render the premium deep-analysis recommendation panel.

    Identical layout used on Discovery inline panel, Deep_Analysis page,
    and Home demo — single source of truth.
    """
    rec = ai_summary.get("recommendation", "—")
    conf = ai_summary.get("confidence", "—")
    avg_sent = float(ai_summary.get("avg_sentiment", 0.0))
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
        f"Bullish ({avg_sent:+.2f})" if avg_sent >= 0.10
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

    # Panel header
    st.markdown(
        f"""
        <div style="
          margin-top:1.0rem;
          border:1px solid rgba(56,189,248,.30);
          border-radius:16px 16px 0 0;
          padding:18px 20px 14px 20px;
          background:linear-gradient(180deg,rgba(56,189,248,.07),rgba(15,23,42,.95));
          display:flex;align-items:center;justify-content:space-between;gap:12px;
        ">
          <div>
            <div style="font-size:0.72rem;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:rgba(56,189,248,.80);margin-bottom:3px;">Deep Analysis{(' · ' + sector.title()) if sector and sector.lower() not in ('unknown','') else ''}</div>
            <div style="font-size:1.55rem;font-weight:850;letter-spacing:-0.02em;color:rgba(248,250,252,.98);">{ticker}</div>
          </div>
          <div style="text-align:right;">
            <div style="font-size:0.72rem;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:rgba(148,163,184,.65);margin-bottom:3px;">Signal</div>
            <div style="font-size:1.30rem;font-weight:850;color:{rec_color};">{rec}</div>
            <div style="font-size:0.80rem;color:rgba(148,163,184,.75);margin-top:2px;">Confidence: {conf}</div>
          </div>
        </div>
        <div style="border:1px solid rgba(56,189,248,.20);border-top:none;border-radius:0 0 16px 16px;padding:16px 20px 20px 20px;background:rgba(15,23,42,.88);margin-bottom:1.5rem;">
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

    _rec_sublabel = {"buy": "Strong upside signal", "watch": "Hold — monitor closely", "avoid": "Risk outweighs reward"}.get(rec.lower(), "")
    _conf_sublabel = {"high": "Strong data backing", "moderate": "Reasonable evidence", "low": "Thin data — use caution"}.get(conf.lower(), "")
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
        f'<div style="font-size:0.75rem;color:rgba(148,163,184,.65);margin-top:1px;">Score {avg_sent:+.3f}</div>'
        f'{_bar_html(min(100,int(abs(avg_sent)*280)), sent_color)}'
        f'</div>'

        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Price / projection / hold period row ──
    if current_price != "Unavailable" or projected_gain != "Unavailable" or hold_days != "Unavailable":
        _fc = "border-radius:10px;padding:10px 14px;background:rgba(15,23,42,.55);border:1px solid rgba(148,163,184,.12);flex:1;"
        _fl = "font-size:0.68rem;font-weight:700;letter-spacing:0.05em;text-transform:uppercase;color:rgba(148,163,184,.55);margin-bottom:3px;"
        _fv = "font-size:1.00rem;font-weight:800;color:rgba(248,250,252,.92);"
        st.markdown(
            f'<div style="display:flex;gap:8px;margin-bottom:14px;flex-wrap:nowrap;">'
            f'<div style="{_fc}"><div style="{_fl}">Last Price</div><div style="{_fv}">{current_price}</div></div>'
            f'<div style="{_fc}"><div style="{_fl}">Proj. Gain 30d</div><div style="{_fv}">{projected_gain}</div></div>'
            f'<div style="{_fc}"><div style="{_fl}">Hold Period</div><div style="{_fv}">{hold_days}</div></div>'
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

    # Close panel body
    st.markdown('</div>', unsafe_allow_html=True)


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


def render_deep_panel_header(ticker: str, sector: str, rec: str, conf: str, avg_sentiment: float) -> None:
    """Render the shared premium deep-analysis panel header (header bar + metric cards + rationale label).

    Used by Discovery inline panel, Deep_Analysis page, and Home demo.
    """
    rec_l = (rec or "").lower()
    rec_color = (
        "rgba(56,189,248,.95)" if "buy" in rec_l
        else "rgba(239,68,68,.90)" if "avoid" in rec_l
        else "rgba(245,158,11,.90)"
    )
    conf_l = (conf or "").lower()

    # Signal strength bar fill (0–100 for CSS width)
    strength_pct = {"high": 90, "moderate": 55, "low": 25}.get(conf_l, 40)
    strength_color = {
        "high": "rgba(56,189,248,.85)",
        "moderate": "rgba(245,158,11,.85)",
        "low": "rgba(239,68,68,.70)",
    }.get(conf_l, "rgba(148,163,184,.60)")

    # Sentiment bar (−1 to +1 → 0–100%)
    sent_pct = min(100, max(0, int((avg_sentiment + 1) / 2 * 100)))
    sent_color = (
        "rgba(56,189,248,.80)" if avg_sentiment >= 0.15
        else "rgba(239,68,68,.75)" if avg_sentiment <= -0.10
        else "rgba(148,163,184,.65)"
    )
    sent_label = (
        f"Bullish ({avg_sentiment:+.2f})" if avg_sentiment >= 0.15
        else f"Bearish ({avg_sentiment:+.2f})" if avg_sentiment <= -0.10
        else f"Neutral ({avg_sentiment:+.2f})"
    )

    # Rec sub-label
    rec_sub = {
        "buy": "Strong positive signal detected",
        "avoid": "Risk signals outweigh upside",
        "watch": "Insufficient conviction to act",
    }.get(rec_l, "Signal computed from social data")

    conf_sub = {
        "high": "High-confidence signal",
        "moderate": "Moderate evidence base",
        "low": "Limited data — treat with caution",
    }.get(conf_l, "Evidence base assessed")

    _card = (
        "border-radius:13px;padding:14px 16px 12px 16px;"
        "background:linear-gradient(180deg,rgba(15,23,42,.92),rgba(15,23,42,.72));"
        "border:1px solid rgba(148,163,184,.15);flex:1;min-width:0;"
    )
    _bar_track = (
        "height:4px;border-radius:99px;background:rgba(148,163,184,.12);"
        "margin-top:8px;overflow:hidden;"
    )

    st.markdown(
        f"""
        <div style="display:flex;gap:10px;margin:10px 0 14px 0;flex-wrap:wrap;">

          <div style="{_card}border-color:rgba(56,189,248,.28);">
            <div style="font-size:0.68rem;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:rgba(148,163,184,.60);margin-bottom:4px;">Recommendation</div>
            <div style="font-size:1.25rem;font-weight:850;color:{rec_color};line-height:1.1;">{rec}</div>
            <div style="font-size:0.72rem;color:rgba(148,163,184,.60);margin-top:3px;">{rec_sub}</div>
            <div style="{_bar_track}"><div style="height:100%;width:{strength_pct}%;background:{strength_color};border-radius:99px;"></div></div>
          </div>

          <div style="{_card}border-color:rgba(125,211,252,.18);">
            <div style="font-size:0.68rem;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:rgba(148,163,184,.60);margin-bottom:4px;">Confidence</div>
            <div style="font-size:1.25rem;font-weight:850;color:rgba(248,250,252,.95);line-height:1.1;">{conf}</div>
            <div style="font-size:0.72rem;color:rgba(148,163,184,.60);margin-top:3px;">{conf_sub}</div>
            <div style="{_bar_track}"><div style="height:100%;width:{strength_pct}%;background:{strength_color};border-radius:99px;"></div></div>
          </div>

          <div style="{_card}border-color:rgba(148,163,184,.18);">
            <div style="font-size:0.68rem;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:rgba(148,163,184,.60);margin-bottom:4px;">Sentiment</div>
            <div style="font-size:1.25rem;font-weight:850;color:{sent_color};line-height:1.1;">{sent_label}</div>
            <div style="font-size:0.72rem;color:rgba(148,163,184,.60);margin-top:3px;">Weighted avg across all signals</div>
            <div style="{_bar_track}"><div style="height:100%;width:{sent_pct}%;background:{sent_color};border-radius:99px;"></div></div>
          </div>

        </div>
        """,
        unsafe_allow_html=True,
    )


def render_full_analysis_expander_label() -> str:
    """Return styled label HTML for the Full Analysis Details expander trigger."""
    return "📊 View full analysis breakdown ›"
