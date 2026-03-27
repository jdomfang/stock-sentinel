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

    # Branded page-transition overlay (best-effort)
    # Streamlit Cloud doesn't reliably run <script> inside st.markdown; components.html does.
    # We inject an overlay into the parent document, then fade it out quickly.
    components.html(
        """
        <script>
        (function () {
          try {
            const doc = (window.parent && window.parent.document) ? window.parent.document : document;
            const id = 'clawd-transition-overlay';

            const mount = () => {
              // Remove any stale overlay first
              try {
                const stale = doc.getElementById(id);
                if (stale) stale.remove();
              } catch (e) {}

              const el = doc.createElement('div');
              el.id = id;
              el.style.cssText = [
                'position:fixed',
                'inset:0',
                'z-index:99999',
                'background:#020617',
                'pointer-events:none',
                'opacity:1',
                'transition:opacity .35s ease'
              ].join(';');

              (doc.body || doc.documentElement).appendChild(el);

              // Fade out + remove
              setTimeout(() => { el.style.opacity = '0'; }, 120);
              setTimeout(() => { try { el.remove(); } catch (e) {} }, 650);

              // Safety cleanup
              setTimeout(() => {
                try {
                  const maybe = doc.getElementById(id);
                  if (maybe) maybe.remove();
                } catch (e) {}
              }, 2500);
            };

            if (!doc || (!doc.body && !doc.documentElement)) return;
            if (!doc.body) setTimeout(mount, 0);
            else mount();
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

    # Discovery-style hero
    st.markdown(
        f"""
        <div style="margin: -22px 0 12px 0;">
          <div style="color: rgba(56,189,248,.95); font-weight: 750; letter-spacing: 0.06em; text-transform: uppercase; font-size: 0.78rem; margin-bottom: 10px;">{eyebrow}</div>
          <div style="font-size: 2.05rem; font-weight: 850; letter-spacing: -0.03em; line-height: 1.1; margin: 0 0 8px 0;">{title}</div>
          {f'<div style="color: rgba(148,163,184,.95); font-size: 1.02rem; line-height: 1.5; margin: 0 0 10px 0;">{subtitle}</div>' if subtitle else ''}
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
